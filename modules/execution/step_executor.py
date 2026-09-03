# -*- coding: utf-8 -*-
"""
统一步骤路由器：按 automation_layer 分发 Web（Playwright）、桌面（pywinauto）与 Android（Appium）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from modules.desktop.desktop_automation import (
    normalize_automation_layer,
    sync_desktop_execute_step,
    validate_step_for_layer,
)
from modules.desktop.desktop_runtime import (
    desktop_runtime_available,
    desktop_runtime_unavailable_reason,
    parse_desktop_spec,
)

try:
    from modules.mobile.mobile_automation import parse_mobile_spec, sync_mobile_execute_step, validate_mobile_step_result
except ImportError:
    parse_mobile_spec = None  # type: ignore
    sync_mobile_execute_step = None  # type: ignore
    validate_mobile_step_result = None  # type: ignore

try:
    from modules.web.playwright_automation import resolve_playwright_headless
except ImportError:

    def resolve_playwright_headless(requested: bool = True) -> bool:
        return requested


def case_steps_include_desktop(steps: List[Dict[str, Any]]) -> bool:
    return any(normalize_automation_layer(s) == "desktop" for s in (steps or []))


def case_steps_include_android(steps: List[Dict[str, Any]]) -> bool:
    return any(normalize_automation_layer(s) == "android" for s in (steps or []))


def case_steps_include_web(steps: List[Dict[str, Any]]) -> bool:
    """用例是否包含 Web 步骤（纯桌面/Android 用例为 False，不应启动 Playwright）。"""
    return any(normalize_automation_layer(s) == "web" for s in (steps or []))


def ensure_mixed_run_environment(steps: List[Dict[str, Any]]) -> Optional[str]:
    """
    混排用例运行前检查。返回 None 表示通过；否则为错误/警告文案。

    生产策略：允许 Web / Desktop / Android 在同一用例内**串行**混排（与 AI 多端联动一致）。
    仅校验各端运行时是否可用；跨端桥接步骤（extract_otp / api_call）不额外要求手机环境预检
    （由桌面执行器内部按需调用）。
    """
    has_desktop = case_steps_include_desktop(steps)
    has_android = case_steps_include_android(steps)
    has_web = case_steps_include_web(steps)

    if has_android:
        try:
            from modules.mobile.mobile_env_config import mobile_runtime_unavailable_reason

            reason = mobile_runtime_unavailable_reason()
            if reason:
                return reason
        except ImportError:
            return "移动端模块未安装"

    if has_desktop:
        if not desktop_runtime_available():
            detail = desktop_runtime_unavailable_reason()
            base = (
                "用例包含桌面自动化步骤，但当前环境不支持（需 Windows 且已安装 opencv-python、mss、numpy）。"
            )
            return detail or base
        if resolve_playwright_headless(True) and has_web:
            return (
                "用例包含桌面与 Web 混排步骤：请将 PLAYWRIGHT_HEADLESS 设为 0，"
                "并在有交互桌面的用户会话中运行平台。"
            )
    return None


def enrich_execution_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """将数据库步骤格式补充 automation_layer / desktop_spec / mobile_spec / cross_end_spec 到执行脚本 dict。"""
    out = dict(step)
    out["automation_layer"] = normalize_automation_layer(step)
    ds = step.get("desktop_spec")
    if ds and not isinstance(ds, dict):
        out["desktop_spec"] = parse_desktop_spec(ds)
    elif isinstance(ds, dict):
        out["desktop_spec"] = ds
    else:
        out["desktop_spec"] = {}
    ms = step.get("mobile_spec")
    if parse_mobile_spec:
        if ms and not isinstance(ms, dict):
            out["mobile_spec"] = parse_mobile_spec(ms)
        elif isinstance(ms, dict):
            out["mobile_spec"] = ms
        else:
            out["mobile_spec"] = {}
        out["strategy"] = (
            (step.get("strategy") or step.get("selector_type") or "accessibility_id").strip()
            or "accessibility_id"
        )
    # 跨端 spec 解析
    ces = step.get("cross_end_spec")
    if ces and not isinstance(ces, dict):
        try:
            out["cross_end_spec"] = json.loads(ces) if isinstance(ces, str) else {}
        except Exception:
            out["cross_end_spec"] = {}
    elif isinstance(ces, dict):
        out["cross_end_spec"] = ces
    else:
        out["cross_end_spec"] = {}
    # 变量替换：把 input_value 中的 {{var}} 占位符替换为实际值
    _resolve_step_vars_in_place(out)
    return out


# 跨端用例回放时的运行时变量存储
_runtime_case_vars: Dict[str, str] = {}


def set_case_var(key: str, value: str) -> None:
    if key and value is not None:
        _runtime_case_vars[key] = str(value)


def get_case_var(key: str) -> str:
    return _runtime_case_vars.get(key, "")


def clear_case_vars() -> None:
    _runtime_case_vars.clear()


def _resolve_step_vars_in_place(step: Dict[str, Any]) -> None:
    """把 input_value / cross_end_spec 中的 {{var}} 占位符替换为运行时变量值。"""
    import re as _re
    pattern = _re.compile(r"\{\{(\w+)\}\}")

    def _replace(val: Any) -> Any:
        if isinstance(val, str) and "{{" in val:
            return pattern.sub(
                lambda m: _runtime_case_vars.get(m.group(1), m.group(0)),
                val,
            )
        return val

    if step.get("input_value"):
        step["input_value"] = _replace(step["input_value"])
    ces = step.get("cross_end_spec")
    if isinstance(ces, dict):
        for k, v in list(ces.items()):
            ces[k] = _replace(v)


def is_desktop_step(step: Dict[str, Any]) -> bool:
    return normalize_automation_layer(step) == "desktop"


def is_mobile_step(step: Dict[str, Any]) -> bool:
    return normalize_automation_layer(step) == "android"


def convert_db_step_to_flow_step(step: Dict[str, Any]) -> Any:
    """
    将数据库 test_steps 记录转换为 FlowStep 内部 DSL。
    
    供 ExecutorFactory 和新引擎路由使用。
    """
    try:
        from mobile_engine.engine_interface import (
            FlowStep,
            LocatorInfo,
            LocatorStrategy,
        )
    except ImportError:
        raise RuntimeError("移动引擎模块未安装")

    action = (step.get("action") or "").strip().lower()
    sel_type = (step.get("selector_type") or step.get("strategy") or "").strip()
    sel_value = (step.get("selector_value") or "").strip()

    # action 别名映射
    alias_map = {
        "click": "tap",
        "fill": "input",
        "open_app": "launch_app",
        "close_app": "stop_app",
        "verify": "assert",
        "assert_text": "assert",
        "tap_image": "tap",
        "wait_image": "wait",
    }
    mapped_action = alias_map.get(action, action)

    # 构建 LocatorInfo
    locator = None
    if sel_value:
        strat = LocatorStrategy.ACCESSIBILITY_ID
        if sel_type in ("text", "name"):
            strat = LocatorStrategy.TEXT
        elif sel_type in ("id", "css"):
            strat = LocatorStrategy.ID
        elif sel_type in ("xpath",):
            strat = LocatorStrategy.SEMANTIC
        elif sel_type in ("visual", "visual_template"):
            strat = LocatorStrategy.VISUAL
        elif sel_type in ("semantic",):
            strat = LocatorStrategy.SEMANTIC

        locator = LocatorInfo(
            strategy=strat,
            value=sel_value,
            semantic_desc=step.get("description", ""),
            visual_template_path=step.get("visual_template_path", ""),
        )

    # 解析坐标 (mobile_spec)
    tap_x = None
    tap_y = None
    ms = step.get("mobile_spec") or {}
    if isinstance(ms, str):
        try:
            import json

            ms = json.loads(ms)
        except Exception:
            ms = {}
    if isinstance(ms, dict):
        try:
            if ms.get("x") is not None and ms.get("y") is not None:
                tap_x = int(ms["x"])
                tap_y = int(ms["y"])
            elif ms.get("tap_x") is not None and ms.get("tap_y") is not None:
                tap_x = int(ms["tap_x"])
                tap_y = int(ms["tap_y"])
        except (ValueError, TypeError):
            pass

    return FlowStep(
        action=mapped_action,
        description=step.get("description") or "",
        locator=locator,
        input_value=(step.get("input_value") or ""),
        swipe_direction=(step.get("swipe_direction") or ""),
        swipe_start=(step.get("swipe_start") or ""),
        swipe_end=(step.get("swipe_end") or ""),
        swipe_duration_ms=int(step.get("swipe_duration_ms") or 400),
        assert_type=step.get("compare_type") or "visible",
        wait_timeout_ms=int(step.get("wait_timeout_ms") or 10000),
        maestro_label=step.get("maestro_label") or "",
        maestro_optional=bool(step.get("maestro_optional", False)),
        maestro_retry=int(step.get("maestro_retry") or 0),
        tap_x=tap_x,
        tap_y=tap_y,
    )


DESKTOP_POINTER_ACTIONS = frozenset({"click", "double_click", "right_click"})


def validate_desktop_step_result(result: Any, action: str) -> Dict[str, Any]:
    """
    统一桌面步骤成功闸门：status 成功；指针步骤另要求 verified 与 pointer_executed。
    所有执行入口（单用例、批量、脚本）应调用此函数。
    """
    act = (action or "").strip().lower()
    if not isinstance(result, dict):
        raise RuntimeError(
            f"桌面步骤返回无效结果（期望 dict，得到 {type(result).__name__}）"
        )
    status = str(result.get("status") or "").strip().lower()
    if not status:
        # 兼容仅返回 ok/success 布尔、无 status 字段的旧结果
        if result.get("success") is True or result.get("ok") is True:
            status = "success"
        elif result.get("success") is False or result.get("ok") is False:
            raise RuntimeError(result.get("error") or "桌面步骤执行失败")
        else:
            raise RuntimeError(
                result.get("error")
                or "桌面步骤未返回 status，禁止默认成功（fail-closed）"
            )
    if status not in ("success", "ok", "passed", "warning"):
        raise RuntimeError(result.get("error") or "桌面步骤执行失败")
    if act in DESKTOP_POINTER_ACTIONS:
        if not result.get("verified"):
            raise RuntimeError(
                result.get("error")
                or result.get("warning")
                or "桌面指针步骤未通过执行校验（可能未命中目标控件）"
            )
        if not result.get("pointer_executed"):
            raise RuntimeError(
                result.get("error")
                or "桌面指针步骤未真正执行（pointer_executed=false）"
            )
    if status == "warning":
        if result.get("warning"):
            uat_logger.warning("桌面步骤警告: %s", result.get("warning"))
        if result.get("error"):
            uat_logger.error("桌面步骤错误: %s", result.get("error"))
    return result


def _apply_tree_verification(step: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """回放树级校验：步骤带录制期 verification 快照时，动作后从 UIA 树复核节点状态。

    原则：UIA 树是真实性/稳定性根本，视觉不参与判定。
    - 默认告警不阻断（result["tree_verify"]="warn" + warning 文本），防树抖动误杀
    - verification.strict=True 时返回 error_code=TREE_VERIFY_FAILED（阻断）
    """
    v = step.get("verification")
    if not isinstance(v, dict) or not v:
        return result
    if not isinstance(result, dict):
        return result
    strict = bool(v.get("strict"))
    check: Dict[str, Any] = {}
    try:
        layer = normalize_automation_layer(step)
        if layer == "android":
            from modules.mobile.mobile_cross_end_tools import _verify_after_action

            anchor = step.get("uia_anchor")
            serial = ""
            ms = step.get("mobile_spec")
            if isinstance(ms, dict):
                serial = str(ms.get("serial") or step.get("serial") or "")
            else:
                serial = str(step.get("serial") or "")
            check = _verify_after_action(
                serial, anchor if isinstance(anchor, dict) else None, user_id=0
            )
        elif layer == "desktop":
            from modules.desktop.windows_desktop_tools import _build_desktop_uia_anchor

            x = result.get("x")
            y = result.get("y")
            if x and y:
                check = _build_desktop_uia_anchor(int(x), int(y))
    except Exception:
        check = {}
    if not check:
        return result
    recorded_found = bool(v.get("found"))
    actual_found = bool(check.get("found"))
    if recorded_found and not actual_found:
        msg = (
            f"树级校验未通过：录制时节点存在（{v.get('matched_via') or '-'}），"
            f"回放后未命中（{check.get('matched_via') or '-'}）"
        )
        if strict:
            result["error_code"] = "TREE_VERIFY_FAILED"
            result["error"] = result.get("error") or msg
            result["status"] = "error"
        else:
            result["warning"] = (str(result.get("warning") or "") + "；" + msg).strip("；")
            result["tree_verify"] = "warn"
        return result
    result["tree_verify"] = "ok"
    return result


def sync_execute_step_by_layer(
    step: Dict[str, Any],
    *,
    web_executor: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    同步执行单步（桌面/Android 直接执行；Web 由调用方传入 web_executor）。
    """
    layer = normalize_automation_layer(step)
    action = (step.get("action") or "").strip()
    err = validate_step_for_layer(action, layer)
    if err:
        raise ValueError(err)

    if layer == "desktop":
        result = sync_desktop_execute_step(step)
        # 跨端步骤 extract_otp：把返回的 sms_otp 写入运行时变量
        if action == "extract_otp" and isinstance(result, dict):
            otp = result.get("sms_otp") or ""
            if otp:
                set_case_var("sms_otp", str(otp))
        return _apply_tree_verification(step, validate_desktop_step_result(result, action))

    if layer == "android":
        if not sync_mobile_execute_step or not validate_mobile_step_result:
            raise RuntimeError("移动端模块未安装")
        from modules.mobile.mobile_executor import get_mobile_executor

        exec_step = enrich_execution_step(step)
        result = sync_mobile_execute_step(exec_step, get_mobile_executor())
        if action == "extract_otp" and isinstance(result, dict):
            otp = result.get("sms_otp") or ""
            if otp:
                set_case_var("sms_otp", str(otp))
        return _apply_tree_verification(
            exec_step, validate_mobile_step_result(result, action)
        )

    if web_executor:
        web_executor(step)
        return {"status": "delegated_web"}
    raise ValueError("Web 步骤需要提供 web_executor 回调")


async def async_execute_step_by_layer(
    step: Dict[str, Any],
    automation: Any,
) -> List[Dict[str, Any]]:
    """
    批量/Playwright 路径：桌面/Android 步骤走 sync；Web 步骤走 automation.execute_single_step。
    """
    import asyncio

    exec_step = enrich_execution_step(step)
    action = (exec_step.get("action") or "").strip()
    err = validate_step_for_layer(action, normalize_automation_layer(exec_step))
    if err:
        raise ValueError(err)

    if is_desktop_step(exec_step):
        result = await asyncio.to_thread(sync_desktop_execute_step, exec_step)
        # 跨端步骤 extract_otp：把返回的 sms_otp 写入运行时变量
        if action == "extract_otp" and isinstance(result, dict):
            otp = result.get("sms_otp") or ""
            if otp:
                set_case_var("sms_otp", str(otp))
        return [validate_desktop_step_result(result, action)]

    if is_mobile_step(exec_step):
        if not sync_mobile_execute_step or not validate_mobile_step_result:
            raise RuntimeError("移动端模块未安装")
        from modules.mobile.mobile_executor import get_mobile_executor

        result = await asyncio.to_thread(
            sync_mobile_execute_step, exec_step, get_mobile_executor()
        )
        if action == "extract_otp" and isinstance(result, dict):
            otp = result.get("sms_otp") or ""
            if otp:
                set_case_var("sms_otp", str(otp))
        return [validate_mobile_step_result(result, action)]

    results = await automation.execute_single_step(exec_step)
    return results if isinstance(results, list) else [results]
