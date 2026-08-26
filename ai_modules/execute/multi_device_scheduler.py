# -*- coding: utf-8 -*-
"""多设备并行调度器：将 DevicePool 集成到跨端编排器。

支持：
- cross_end plan 中 mobile stage 声明 parallel_devices / device_group
- 自动发现可用设备并行执行同一 stage 的 steps
- 汇总多设备结果，任一失败即 stage 失败（可配置 allow_partial）
- 设备级超时与重试
"""
from __future__ import annotations

import concurrent.futures
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from uat_logger import uat_logger
except Exception:
    import logging
    uat_logger = logging.getLogger(__name__)


def _discover_available_devices(
    *,
    platform_filter: str = "android",
    max_devices: int = 0,
    exclude_busy: bool = True,
) -> List[Dict[str, Any]]:
    """发现当前可用设备。返回 [{udid, model, platform, ...}]。"""
    devices: List[Dict[str, Any]] = []
    try:
        from modules.mobile.mobile_device_manager import (
            get_device_info,
            list_emulators,
            list_real_usb_devices,
        )

        for d in list_real_usb_devices():
            udid = d.get("udid", "")
            if not udid:
                continue
            info = get_device_info(udid)
            devices.append({
                "udid": udid,
                "platform": "android",
                "model": info.get("model", d.get("display_name", "")),
                "screen_width": info.get("width", 1080),
                "screen_height": info.get("height", 1920),
                "is_emulator": False,
                "connection_type": "usb",
            })
        for d in list_emulators():
            udid = d.get("udid", "")
            if not udid:
                continue
            info = get_device_info(udid)
            devices.append({
                "udid": udid,
                "platform": "android",
                "model": info.get("model", d.get("display_name", "")),
                "screen_width": info.get("width", 1080),
                "screen_height": info.get("height", 1920),
                "is_emulator": True,
                "connection_type": "usb",
            })
    except Exception as e:
        uat_logger.warning("多设备发现失败: %s", e)

    if platform_filter:
        pf = platform_filter.strip().lower()
        devices = [d for d in devices if (d.get("platform") or "").lower() == pf]

    if max_devices > 0:
        devices = devices[:max_devices]

    return devices


def _execute_steps_on_device(
    device: Dict[str, Any],
    steps: List[Dict[str, Any]],
    *,
    timeout_sec: float = 300.0,
    source: str = "multi_device",
) -> Dict[str, Any]:
    """在单个设备上执行步骤列表。返回标准化结果 dict。"""
    udid = device.get("udid", "unknown")
    t0 = time.perf_counter()

    # 策略1：通过 enqueue/await（手机本机执行，推荐路径）
    try:
        from modules.mobile.mobile_sync_store import enqueue_run_job, wait_for_run_job

        job_id = enqueue_run_job(
            case_id=0,
            steps=steps,
            user_id=0,
            device_id=udid,
            source=source,
            job_kind="run_steps",
        )
        job = wait_for_run_job(job_id, timeout_sec=timeout_sec)
        payload = job.get("result_payload") if isinstance(job.get("result_payload"), dict) else {}
        status = str(job.get("status") or "").strip().lower()
        ok = status in ("success", "ok") or payload.get("success") is True
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "device_udid": udid,
            "device_model": device.get("model", ""),
            "ok": ok,
            "job_id": job_id,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "error": None if ok else (job.get("error") or payload.get("error") or "执行失败"),
            "error_code": None if ok else (job.get("error_code") or payload.get("error_code")),
            "result_payload": payload,
            "steps_executed": len(payload.get("results") or []),
        }
    except ImportError:
        pass

    # 策略2：通过 MobileEngineDispatcher（本机 USB 直连执行）
    try:
        from mobile_engine.engine_dispatcher import MobileEngineDispatcher
        from mobile_engine.engine_interface import DeviceInfo, FlowStep

        dispatcher = MobileEngineDispatcher()
        info = DeviceInfo(
            udid=udid,
            platform=device.get("platform", "android"),
            model=device.get("model", ""),
            screen_width=device.get("screen_width", 1080),
            screen_height=device.get("screen_height", 1920),
            is_emulator=bool(device.get("is_emulator")),
        )
        dispatcher.connect_device(info)
        flow_steps = _convert_to_flow_steps(steps)
        result = dispatcher.execute_flow(flow_steps)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "device_udid": udid,
            "device_model": device.get("model", ""),
            "ok": result.passed_count > 0 and result.failed_count == 0,
            "elapsed_ms": elapsed_ms,
            "status": "success" if result.failed_count == 0 else "error",
            "error": f"{result.failed_count} steps failed" if result.failed_count > 0 else None,
            "steps_executed": len(result.steps),
            "passed": result.passed_count,
            "failed": result.failed_count,
        }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "device_udid": udid,
            "device_model": device.get("model", ""),
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "status": "error",
            "error": str(e)[:200],
            "error_code": "DEVICE_EXEC_ERROR",
            "steps_executed": 0,
        }


def _convert_to_flow_steps(steps: List[Dict[str, Any]]) -> List:
    """将 dict 步骤转为 FlowStep（尽力转换，缺失则跳过）。"""
    try:
        from mobile_engine.engine_interface import FlowStep, LocatorInfo, LocatorStrategy
    except ImportError:
        return []

    out = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        action = (s.get("action") or "").strip().lower()
        sel_type = (s.get("selector_type") or s.get("strategy") or "").strip()
        sel_value = (s.get("selector_value") or s.get("selector") or "").strip()
        input_value = (s.get("input_value") or s.get("value") or "").strip()
        desc = (s.get("description") or "").strip()

        strategy = LocatorStrategy.TEXT
        if sel_type in ("id", "accessibility_id"):
            strategy = LocatorStrategy.ACCESSIBILITY_ID
        elif sel_type == "coordinate":
            strategy = LocatorStrategy.COORDINATE

        locator = LocatorInfo(strategy=strategy, value=sel_value)
        fs = FlowStep(
            action=action or "tap",
            locator=locator,
            input_value=input_value,
            description=desc,
        )
        out.append(fs)
    return out


def is_multi_device_stage(stage: Dict[str, Any]) -> bool:
    """判断 stage 是否需要多设备并行执行。"""
    if not isinstance(stage, dict):
        return False
    if stage.get("parallel_devices") or stage.get("device_group"):
        return True
    devices_cfg = stage.get("devices")
    if isinstance(devices_cfg, list) and len(devices_cfg) > 1:
        return True
    return False


def parse_device_config(stage: Dict[str, Any]) -> Dict[str, Any]:
    """从 stage 解析多设备配置。"""
    cfg: Dict[str, Any] = {
        "enabled": False,
        "devices": [],          # 指定设备列表 [{udid, ...}]
        "max_devices": 0,       # 0=不限
        "platform": "android",
        "allow_partial": False,  # True=部分设备成功即 stage 成功
        "timeout_per_device": 300.0,
        "retry_on_device_fail": False,
        "max_retries": 1,
    }

    if stage.get("parallel_devices") is True:
        cfg["enabled"] = True
    elif isinstance(stage.get("parallel_devices"), dict):
        cfg["enabled"] = True
        pd = stage["parallel_devices"]
        cfg["max_devices"] = int(pd.get("max_devices") or 0)
        cfg["platform"] = str(pd.get("platform") or "android")
        cfg["allow_partial"] = bool(pd.get("allow_partial"))
        cfg["timeout_per_device"] = float(pd.get("timeout_per_device") or 300)
        cfg["retry_on_device_fail"] = bool(pd.get("retry_on_device_fail"))
        cfg["max_retries"] = int(pd.get("max_retries") or 1)

    if isinstance(stage.get("devices"), list) and len(stage["devices"]) > 1:
        cfg["enabled"] = True
        cfg["devices"] = [d for d in stage["devices"] if isinstance(d, dict)]

    dg = stage.get("device_group")
    if isinstance(dg, str) and dg.strip():
        cfg["enabled"] = True
        # device_group 用于后续扩展（如从设备池按标签筛选）

    return cfg


def execute_multi_device_stage(
    stage: Dict[str, Any],
    *,
    progress_callback: Optional[Callable] = None,
    user_id: int = 0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """执行多设备并行 mobile stage。

    Returns:
        (result_dict, extracted_vars)
    """
    cfg = parse_device_config(stage)
    stage_id = stage.get("id", "multi-device")
    steps = stage.get("steps", [])
    if not isinstance(steps, list):
        steps = []

    result: Dict[str, Any] = {
        "ok_assert": False,
        "error": None,
        "elapsed_ms": 0,
        "stage_id": stage_id,
        "layer": "mobile",
        "executor": "multi_device",
        "device_count": 0,
        "device_results": [],
        "steps_executed": 0,
    }
    extracted: Dict[str, Any] = {}

    t0 = time.perf_counter()

    # 确定设备列表
    uid = int(user_id or stage.get("user_id") or 0)

    if cfg["devices"]:
        devices = cfg["devices"]
    else:
        devices = _discover_available_devices(
            platform_filter=cfg["platform"],
            max_devices=cfg["max_devices"],
        )

    if not devices:
        result["error"] = "无可用设备（parallel_devices 启用但未发现设备）"
        result["error_code"] = "NO_DEVICES"
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return result, extracted

    result["device_count"] = len(devices)
    timeout_per = cfg["timeout_per_device"]

    uat_logger.info(
        "多设备并行执行 stage=%s devices=%d steps=%d",
        stage_id, len(devices), len(steps),
    )

    # 并行执行
    device_results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(devices), 8)
    ) as executor:
        future_map: Dict[str, concurrent.futures.Future] = {}
        for dev in devices:
            udid = dev.get("udid", "unknown")
            future = executor.submit(
                _execute_steps_on_device,
                dev,
                steps,
                timeout_sec=timeout_per,
                source=f"multi_device:{stage_id}",
            )
            future_map[udid] = future

        for udid, future in future_map.items():
            try:
                dev_result = future.result(timeout=timeout_per + 30)
            except concurrent.futures.TimeoutError:
                dev_result = {
                    "device_udid": udid,
                    "ok": False,
                    "status": "timeout",
                    "error": f"设备 {udid} 执行超时 ({timeout_per}s)",
                    "error_code": "DEVICE_TIMEOUT",
                    "elapsed_ms": timeout_per * 1000,
                    "steps_executed": 0,
                }
            except Exception as e:
                dev_result = {
                    "device_udid": udid,
                    "ok": False,
                    "status": "error",
                    "error": str(e)[:200],
                    "error_code": "DEVICE_EXCEPTION",
                    "elapsed_ms": 0,
                    "steps_executed": 0,
                }
            device_results.append(dev_result)
            if progress_callback:
                try:
                    progress_callback(stage_id, udid, dev_result.get("ok", False))
                except Exception:
                    pass

    result["device_results"] = device_results
    result["steps_executed"] = sum(
        int(d.get("steps_executed") or 0) for d in device_results
    )

    # 汇总结果
    all_ok = all(d.get("ok") for d in device_results)
    any_ok = any(d.get("ok") for d in device_results)
    failed_devices = [d for d in device_results if not d.get("ok")]

    if all_ok:
        result["ok_assert"] = True
    elif cfg["allow_partial"] and any_ok:
        result["ok_assert"] = True
        result["partial_success"] = True
        result["failed_devices"] = [d.get("device_udid") for d in failed_devices]
        uat_logger.warning(
            "多设备部分成功 stage=%s failed=%d/%d",
            stage_id, len(failed_devices), len(device_results),
        )
    else:
        result["ok_assert"] = False
        result["error"] = (
            f"{len(failed_devices)}/{len(device_results)} 台设备执行失败: "
            + "; ".join(
                f"{d.get('device_udid')}: {d.get('error', 'unknown')}"
                for d in failed_devices[:3]
            )
        )
        result["error_code"] = "MULTI_DEVICE_FAILED"
        result["failed_devices"] = [d.get("device_udid") for d in failed_devices]

    # 重试失败设备
    if (
        cfg["retry_on_device_fail"]
        and not all_ok
        and failed_devices
        and cfg["max_retries"] > 0
    ):
        uat_logger.info(
            "多设备重试 stage=%s retrying %d devices",
            stage_id, len(failed_devices),
        )
        retry_results: List[Dict[str, Any]] = []
        for dev_info in failed_devices:
            udid = dev_info.get("device_udid", "")
            # 从原始 devices 列表找到完整 device 信息
            dev_full = next(
                (d for d in devices if d.get("udid") == udid), None
            )
            if not dev_full:
                continue
            retry_r = _execute_steps_on_device(
                dev_full,
                steps,
                timeout_sec=timeout_per,
                source=f"multi_device_retry:{stage_id}",
            )
            retry_results.append(retry_r)
            # 更新 device_results
            for i, dr in enumerate(device_results):
                if dr.get("device_udid") == udid:
                    device_results[i] = retry_r
                    break

        result["device_results"] = device_results
        result["retry_results"] = retry_results
        # 重新汇总
        all_ok = all(d.get("ok") for d in device_results)
        any_ok = any(d.get("ok") for d in device_results)
        if all_ok or (cfg["allow_partial"] and any_ok):
            result["ok_assert"] = True
            result["error"] = None
            result["error_code"] = None
        else:
            result["ok_assert"] = False
            result["error"] = "重试后仍有设备失败"
            result["error_code"] = "MULTI_DEVICE_RETRY_EXHAUSTED"

    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # 变量抽取：汇总所有设备成功的结果
    if result["ok_assert"]:
        for dr in device_results:
            if not dr.get("ok"):
                continue
            payload = dr.get("result_payload") or {}
            vars_out = payload.get("variables") or {}
            if isinstance(vars_out, dict):
                for k, v in vars_out.items():
                    if k and v is not None:
                        extracted[f"{dr.get('device_udid', 'dev')}_{k}"] = v
                        # 首个设备的变量也写入顶层（兼容单设备语义）
                        if k not in extracted:
                            extracted[k] = v

    return result, extracted


def multi_device_summary(result: Dict[str, Any]) -> str:
    """生成多设备执行摘要文本。"""
    total = result.get("device_count", 0)
    drs = result.get("device_results") or []
    ok_count = sum(1 for d in drs if d.get("ok"))
    fail_count = total - ok_count
    elapsed = result.get("elapsed_ms", 0)
    lines = [
        f"多设备并行: {total} 台, 成功 {ok_count}, 失败 {fail_count}",
        f"总耗时: {elapsed:.0f}ms",
    ]
    for dr in drs:
        mark = "OK" if dr.get("ok") else "FAIL"
        udid = dr.get("device_udid", "?")
        model = dr.get("model", "")
        err = dr.get("error", "")
        line = f"  [{mark}] {udid} ({model})"
        if err:
            line += f" err={err[:80]}"
        lines.append(line)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# PC + 手机跨端并行编排
# stage 配置示例：
#   {
#     "id": "s3",
#     "cross_end_parallel": true,
#     "branches": [
#       {"name": "pc",  "layer": "desktop", "steps": [{"action": "click", ...}]},
#       {"name": "mobile", "layer": "mobile", "steps": [{"action": "tap", ...}],
#        "device_id": "emulator-5554"},
#     ],
#     "allow_partial": false,
#     "timeout_sec": 600
#   }
# 简写（无 branches 时）：
#   {
#     "parallel": {
#       "pc":     {"steps": [...]},
#       "mobile": {"steps": [...], "device_id": "..."}
#     }
#   }
# ─────────────────────────────────────────────────────────────


def is_cross_end_parallel_stage(stage: Dict[str, Any]) -> bool:
    """判断 stage 是否需要 PC + 手机跨端并行编排。"""
    if not isinstance(stage, dict):
        return False
    if stage.get("cross_end_parallel") is True:
        return True
    par = stage.get("parallel")
    if isinstance(par, dict) and (
        par.get("pc") or par.get("desktop") or par.get("mobile") or par.get("android") or par.get("branches")
    ):
        return True
    if isinstance(stage.get("branches"), list) and len(stage["branches"]) > 1:
        return True
    return False


def _parse_cross_end_parallel(stage: Dict[str, Any]) -> Dict[str, Any]:
    """解析跨端并行配置，返回 {branches, allow_partial, timeout_sec}。"""
    cfg: Dict[str, Any] = {
        "branches": [],
        "allow_partial": False,
        "timeout_sec": 600.0,
    }
    par = stage.get("parallel") if isinstance(stage.get("parallel"), dict) else {}
    branches = stage.get("branches") if isinstance(stage.get("branches"), list) else None

    if branches is None:
        branches = []
        for nm in ("pc", "desktop", "mobile", "android"):
            if isinstance(par.get(nm), dict) and par[nm].get("steps"):
                b = dict(par[nm])
                b.setdefault("name", nm)
                b.setdefault("layer", "mobile" if nm in ("mobile", "android") else "desktop")
                branches.append(b)

    cfg["branches"] = [b for b in branches if isinstance(b, dict) and isinstance(b.get("steps"), list)]
    cfg["allow_partial"] = bool(
        stage.get("allow_partial") or par.get("allow_partial")
    )
    try:
        cfg["timeout_sec"] = float(
            stage.get("timeout_sec") or par.get("timeout_sec") or 600
        )
    except (TypeError, ValueError):
        cfg["timeout_sec"] = 600.0
    return cfg


def _execute_pc_branch(
    branch: Dict[str, Any],
    *,
    timeout_sec: float = 600.0,
) -> Dict[str, Any]:
    """PC 桌面分支：preflight 检查 + 桌面步骤逐步执行（复用自愈执行器）。

    返回标准化结果 dict（与 _execute_steps_on_device 字段对齐）。
    """
    t0 = time.perf_counter()
    steps = branch.get("steps", [])
    name = str(branch.get("name") or "pc")
    layer = str(branch.get("layer") or "desktop")
    base: Dict[str, Any] = {
        "branch": name,
        "layer": layer,
        "device_udid": "pc",
        "device_model": "PC 桌面",
        "ok": False,
        "steps_executed": 0,
        "elapsed_ms": 0,
    }

    try:
        from ai_modules.execute.desktop_preflight import check_desktop_preflight
        from ai_modules.optimize.desktop_runtime_heal import (
            run_desktop_step_with_optional_heal,
        )
        from modules.execution.step_executor import validate_desktop_step_result
    except ImportError as ie:
        base["error"] = f"PC 分支依赖不可用: {ie}"
        base["error_code"] = "PC_BRANCH_DEPS_MISSING"
        base["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return base

    if not steps:
        base["error"] = "PC 分支 steps 为空，不得当绿"
        base["error_code"] = "PC_BRANCH_NO_STEPS"
        base["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return base

    pre = check_desktop_preflight()
    if not pre.get("ok"):
        base["error"] = pre.get("error") or "桌面会话不可用"
        base["error_code"] = pre.get("error_code") or "DESKTOP_NO_SESSION"
        base["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return base

    step_results: List[Dict[str, Any]] = []
    executed = 0
    for step in steps:
        if not isinstance(step, dict):
            base["error"] = "Desktop 步骤格式无效（非 dict）"
            base["error_code"] = "INVALID_STEP"
            break
        desk, _heal_meta = run_desktop_step_with_optional_heal(step)
        step_results.append(desk if isinstance(desk, dict) else {})
        executed += 1
        try:
            validate_desktop_step_result(desk, str(step.get("action") or ""))
        except Exception as ve:
            base["error"] = str(ve)
            base["error_code"] = "DESKTOP_STEP_FAILED"
            base["failed_action"] = step.get("action")
            break
        st = str((desk or {}).get("status") or "").strip().lower()
        if st not in ("success", "ok", "passed"):
            base["error"] = (
                (desk or {}).get("error")
                or (desk or {}).get("warning")
                or f"桌面步骤 status={st!r} 不得当绿"
            )
            base["error_code"] = "DESKTOP_SOFT_FAIL"
            base["failed_action"] = step.get("action")
            break
    else:
        base["ok"] = True
        base["status"] = "success"

    base["steps_executed"] = executed
    base["step_results"] = step_results
    base["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return base


def _execute_mobile_branch(
    branch: Dict[str, Any],
    *,
    user_id: int = 0,
    timeout_sec: float = 600.0,
) -> Dict[str, Any]:
    """手机分支：优先指定 device_id，否则自动发现一台可用设备，走 APK job 本机执行。"""
    t0 = time.perf_counter()
    steps = branch.get("steps", [])
    name = str(branch.get("name") or "mobile")
    layer = str(branch.get("layer") or "mobile")
    dev_id = str(branch.get("device_id") or branch.get("udid") or "").strip()

    device: Optional[Dict[str, Any]] = None
    if dev_id:
        try:
            from modules.mobile.mobile_device_manager import get_device_info

            info = get_device_info(dev_id)
            device = {
                "udid": dev_id,
                "platform": "android",
                "model": info.get("model", ""),
                "screen_width": info.get("width", 1080),
                "screen_height": info.get("height", 1920),
                "is_emulator": False,
                "connection_type": "usb",
            }
        except Exception:
            device = {"udid": dev_id, "platform": "android", "model": "", "is_emulator": False}
    else:
        devs = _discover_available_devices(platform_filter="android", max_devices=1)
        if devs:
            device = devs[0]

    base: Dict[str, Any] = {
        "branch": name,
        "layer": layer,
        "ok": False,
        "steps_executed": 0,
        "elapsed_ms": 0,
    }
    if device is None:
        base["error"] = (
            f"手机分支无可用设备（device_id={dev_id or '-'} 且自动发现为空）"
        )
        base["error_code"] = "MOBILE_BRANCH_NO_DEVICE"
        base["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return base
    if not steps:
        base["error"] = "手机分支 steps 为空，不得当绿"
        base["error_code"] = "MOBILE_BRANCH_NO_STEPS"
        base["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return base

    r = _execute_steps_on_device(
        device,
        steps,
        timeout_sec=timeout_sec,
        source="cross_end_parallel",
    )
    r["branch"] = name
    r["layer"] = layer
    return r


def execute_cross_end_parallel_stage(
    stage: Dict[str, Any],
    *,
    progress_callback: Optional[Callable] = None,
    user_id: int = 0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """执行 PC + 手机跨端并行 stage（同一 stage 内 PC 与手机分支并行）。

    Returns:
        (result_dict, extracted_vars)
    """
    cfg = _parse_cross_end_parallel(stage)
    stage_id = stage.get("id", "cross-end-parallel")
    branches = cfg["branches"]
    timeout_sec = cfg["timeout_sec"]

    result: Dict[str, Any] = {
        "ok_assert": False,
        "error": None,
        "elapsed_ms": 0,
        "stage_id": stage_id,
        "layer": "cross_end_parallel",
        "executor": "cross_end_parallel",
        "branch_count": 0,
        "branch_results": [],
        "steps_executed": 0,
    }
    extracted: Dict[str, Any] = {}

    if len(branches) < 2:
        result["error"] = "跨端并行至少需要 PC 与手机两个分支"
        result["error_code"] = "CEP_NEED_TWO_BRANCHES"
        result["elapsed_ms"] = 0
        return result, extracted

    # 区分 PC 分支与手机分支
    pc_branches = [b for b in branches if (b.get("layer") or "").lower() in ("desktop", "pc", "web")]
    mob_branches = [b for b in branches if (b.get("layer") or "").lower() in ("mobile", "android", "ios")]
    # 未标注 layer 的分支按 name 猜测
    for b in branches:
        nm = str(b.get("name") or "").lower()
        if nm in ("pc", "desktop", "web"):
            pc_branches.append(b)
        elif nm in ("mobile", "android", "ios", "phone"):
            mob_branches.append(b)

    # 去重（layer 与 name 可能同时命中）
    seen: set = set()
    merged_pc: List[Dict[str, Any]] = []
    for b in pc_branches:
        k = id(b)
        if k not in seen:
            seen.add(k)
            merged_pc.append(b)
    merged_mob: List[Dict[str, Any]] = []
    for b in mob_branches:
        k = id(b)
        if k not in seen:
            seen.add(k)
            merged_mob.append(b)

    if not merged_pc or not merged_mob:
        result["error"] = "跨端并行缺少 PC 分支或手机分支（需各至少一个）"
        result["error_code"] = "CEP_MISSING_BRANCH"
        result["elapsed_ms"] = 0
        return result, extracted

    t0 = time.perf_counter()
    result["branch_count"] = len(merged_pc) + len(merged_mob)

    jobs: List[Tuple[str, Callable[[], Dict[str, Any]]]] = []
    for b in merged_pc:
        jobs.append((str(b.get("name") or "pc"), lambda bb=b: _execute_pc_branch(bb, timeout_sec=timeout_sec)))
    for b in merged_mob:
        jobs.append((
            str(b.get("name") or "mobile"),
            lambda bb=b, uid=user_id: _execute_mobile_branch(bb, user_id=uid, timeout_sec=timeout_sec),
        ))

    branch_results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(jobs), 8)
    ) as pool:
        future_map = {
            pool.submit(fn): nm for nm, fn in jobs
        }
        for future in concurrent.futures.as_completed(future_map):
            nm = future_map[future]
            try:
                br = future.result(timeout=timeout_sec + 30)
            except concurrent.futures.TimeoutError:
                br = {
                    "branch": nm, "ok": False, "status": "timeout",
                    "error": f"分支 {nm} 执行超时 ({timeout_sec}s)",
                    "error_code": "BRANCH_TIMEOUT",
                    "elapsed_ms": timeout_sec * 1000,
                    "steps_executed": 0,
                }
            except Exception as e:
                br = {
                    "branch": nm, "ok": False, "status": "error",
                    "error": str(e)[:200], "error_code": "BRANCH_EXCEPTION",
                    "elapsed_ms": 0, "steps_executed": 0,
                }
            branch_results.append(br)
            if progress_callback:
                try:
                    progress_callback(stage_id, br.get("ok", False))
                except Exception:
                    pass

    result["branch_results"] = branch_results
    result["steps_executed"] = sum(int(b.get("steps_executed") or 0) for b in branch_results)

    all_ok = all(b.get("ok") for b in branch_results)
    any_ok = any(b.get("ok") for b in branch_results)
    failed_branches = [b for b in branch_results if not b.get("ok")]

    if all_ok:
        result["ok_assert"] = True
    elif cfg["allow_partial"] and any_ok:
        result["ok_assert"] = True
        result["partial_success"] = True
        result["failed_branches"] = [b.get("branch") for b in failed_branches]
    else:
        result["ok_assert"] = False
        result["error"] = (
            f"{len(failed_branches)}/{len(branch_results)} 个分支失败: "
            + "; ".join(
                f"{b.get('branch')}: {b.get('error', 'unknown')}"
                for b in failed_branches[:3]
            )
        )
        result["error_code"] = "CEP_BRANCH_FAILED"
        result["failed_branches"] = [b.get("branch") for b in failed_branches]

    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # 变量抽取：成功分支的 result_payload.variables 并入
    if result["ok_assert"]:
        for br in branch_results:
            if not br.get("ok"):
                continue
            payload = br.get("result_payload") or {}
            vars_out = payload.get("variables") or {}
            if isinstance(vars_out, dict):
                for k, v in vars_out.items():
                    if k and v is not None:
                        extracted[f"{br.get('branch', 'b')}_{k}"] = v
                        if k not in extracted:
                            extracted[k] = v

    return result, extracted


def cross_end_parallel_summary(result: Dict[str, Any]) -> str:
    """生成跨端并行执行摘要文本。"""
    brs = result.get("branch_results") or []
    elapsed = result.get("elapsed_ms", 0)
    lines = [
        f"跨端并行: {len(brs)} 个分支, 总耗时 {elapsed:.0f}ms",
    ]
    for br in brs:
        mark = "OK" if br.get("ok") else "FAIL"
        nm = br.get("branch", "?")
        ly = br.get("layer", "")
        err = br.get("error", "")
        line = f"  [{mark}] {nm} ({ly})"
        if err:
            line += f" err={err[:80]}"
        lines.append(line)
    return "\n".join(lines)
