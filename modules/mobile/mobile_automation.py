# -*- coding: utf-8 -*-
"""
Android 移动端步骤定义与单步执行辅助（Appium / UiAutomator2）。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

from modules.mobile.mobile_env_config import mobile_runtime_available, mobile_runtime_unavailable_reason

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

_MOBILE_ACTIONS = frozenset({
    "open_app",
    "close_app",
    "tap",
    "input_text",
    "swipe",
    "wait",
    "assert_text",
    "assert_element",
    "screenshot",
    "tap_image",
    "wait_image",
    "assert_image",
    "ai_tap",
    "ai_input",
    "assert_vision",
    "wait_vision",
    "extract_vision",
})

_MOBILE_ONLY_ACTIONS = frozenset({"open_app", "close_app", "tap", "input_text"})

_WEB_ALIAS_TO_MOBILE = {
    "click": "tap",
    "input": "input_text",
    "fill": "input_text",
    "verify": "assert_element",
    "assert": "assert_text",
}

_STRATEGIES = frozenset({
    "id",
    "accessibility_id",
    "xpath",
    "class_name",
    "android_uiautomator",
    "css",  # 映射为 id 或 accessibility_id 的别名
    "visual_template",
    "viewport_coord",
})


def parse_mobile_spec(raw: Any) -> Dict[str, Any]:
    """解析 mobile_spec JSON 字符串或 dict。"""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def normalize_mobile_action(action: str) -> str:
    """将 Web 别名动作映射为移动端动作。"""
    act = (action or "").strip().lower()
    return _WEB_ALIAS_TO_MOBILE.get(act, act)


def normalize_strategy(step: Dict[str, Any]) -> str:
    """解析定位策略，默认 accessibility_id。"""
    raw = (
        (step.get("strategy") or step.get("selector_type") or "accessibility_id")
        .strip()
        .lower()
    )
    if raw in ("css", "name"):
        return "accessibility_id"
    if raw in ("coord", "coordinates", "xy"):
        return "viewport_coord"
    if raw in _STRATEGIES:
        return raw
    return "accessibility_id"


def parse_tap_coordinates(step: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """从 mobile_spec 或 viewport_coord 解析坐标点击。"""
    spec = step.get("mobile_spec")
    if isinstance(spec, str) and spec.strip():
        spec = parse_mobile_spec(spec)
    if isinstance(spec, dict):
        for xk, yk in (("tap_x", "tap_y"), ("x", "y")):
            try:
                if spec.get(xk) is not None and spec.get(yk) is not None:
                    return int(spec[xk]), int(spec[yk])
            except (TypeError, ValueError):
                pass
    strategy = normalize_strategy(step)
    if strategy != "viewport_coord":
        return None
    raw = (step.get("selector_value") or "").strip()
    if not raw:
        return None
    parts = re.split(r"[,;\s]+", raw)
    if len(parts) >= 2:
        try:
            return int(float(parts[0])), int(float(parts[1]))
        except (TypeError, ValueError):
            return None
    return None


def validate_step_for_mobile(action: str) -> Optional[str]:
    """校验移动端步骤 action。"""
    act = normalize_mobile_action(action)
    if not act:
        return "步骤 action 不能为空"
    if act not in _MOBILE_ACTIONS:
        return f"不支持的 Android 动作：{act}"
    return None


def mobile_action_requires_locator(action: str) -> bool:
    act = normalize_mobile_action(action)
    return act in (
        "tap",
        "input_text",
        "assert_text",
        "assert_element",
        "tap_image",
        "wait_image",
        "assert_image",
    )


def prepare_mobile_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """归一化步骤字段供 MobileExecutor 使用。"""
    out = dict(step)
    out["action"] = normalize_mobile_action(out.get("action") or "")
    out["automation_layer"] = "android"
    out["strategy"] = normalize_strategy(out)
    ms = step.get("mobile_spec")
    out["mobile_spec"] = parse_mobile_spec(ms) if ms else {}
    return out


def _screenshot_dir() -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "static", "mobile_screenshots")
    os.makedirs(base, exist_ok=True)
    return base


def save_screenshot_bytes(png: bytes, prefix: str = "mobile") -> str:
    """保存截图并返回 Web 路径。"""
    fname = f"{prefix}_{int(time.time() * 1000)}.png"
    out_path = os.path.join(_screenshot_dir(), fname)
    with open(out_path, "wb") as f:
        f.write(png)
    return f"/static/mobile_screenshots/{fname}"


def sync_mobile_execute_step(step: Dict[str, Any], executor: Any) -> Dict[str, Any]:
    """
    通过 MobileExecutor 执行单步（供 step_executor / factory 调用）。

    Args:
        step: 步骤 dict
        executor: MobileExecutor 实例（须已 connect）
    """
    if not mobile_runtime_available():
        reason = mobile_runtime_unavailable_reason() or "移动端不可用"
        return {"status": "error", "error": reason, "description": step.get("description") or ""}

    action = normalize_mobile_action(step.get("action") or "")
    if action in _MOBILE_ACTIONS and action in (
        "ai_tap", "ai_input", "assert_vision", "wait_vision", "extract_vision",
    ):
        from modules.mobile.mobile_agent_client import agent_replay_step, mobile_agent_enabled
        from modules.mobile.mobile_device_manager import get_connected_udid

        if not mobile_agent_enabled():
            return {
                "status": "error",
                "error": "视觉步骤需要 Mobile Agent Gateway（MOBILE_AGENT_GATEWAY_URL）",
                "description": step.get("description") or "",
            }
        udid = ""
        if executor is not None:
            udid = (getattr(executor, "connected_udid", None) or "").strip()
        if not udid:
            udid = (get_connected_udid() or "").strip()
        if not udid:
            return {"status": "error", "error": "未连接 Android 设备", "description": step.get("description") or ""}
        j = agent_replay_step(udid, step, step_index=0)
        result = (j or {}).get("result") or {}
        if (j or {}).get("success") is False or result.get("status") == "error":
            return {
                "status": "error",
                "error": result.get("error") or (j or {}).get("error") or "视觉步骤执行失败",
                "description": step.get("description") or "",
                "action": action,
            }
        return {
            "status": "success",
            "action": action,
            "description": step.get("description") or "",
            "message": result.get("message") or result.get("data") or "",
            "screenshot": result.get("screenshot") or "",
        }

    prepared = prepare_mobile_step(step)
    err = validate_step_for_mobile(prepared.get("action") or "")
    if err:
        return {
            "status": "error",
            "error": err,
            "description": prepared.get("description") or "",
        }
    return executor.execute_step(prepared)


def validate_mobile_step_result(result: Any, action: str) -> Dict[str, Any]:
    """统一移动端步骤成功闸门。"""
    act = normalize_mobile_action(action)
    if not isinstance(result, dict):
        raise RuntimeError(
            f"移动端步骤返回无效结果（期望 dict，得到 {type(result).__name__}）"
        )
    status = str(result.get("status") or "success").strip().lower()
    if status not in ("success", "ok", "passed"):
        raise RuntimeError(result.get("error") or "移动端步骤执行失败")
    uat_logger.info(
        "移动端步骤完成: action=%s desc=%s",
        act,
        (result.get("description") or "")[:80],
    )
    return result
