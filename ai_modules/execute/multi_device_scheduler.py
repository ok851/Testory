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
        from mobile_device_manager import (
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
        from mobile_sync_store import enqueue_run_job, wait_for_run_job

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
