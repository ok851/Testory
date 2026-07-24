from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from logger import uat_logger

_PLATFORM_SKILL_HINT: Dict[str, str] = {
    "web": "testory-web-browser",
    "desktop": "testory-windows-desktop",
    "mobile": "testory-android-mobile",
}

_DEFAULT_SYSTEM_PROMPTS: Dict[str, str] = {
    "web": (
        "你是 Testory 浏览器端测试代理。请按用户指令操作已连接的网页（CDP 模式），"
        "在回复末尾用 `[RESULT]` 标记执行结论：ok 或 fail，并附简要说明。"
    ),
    "desktop": (
        "你是 Testory 桌面端测试代理。请按用户指令操作 Windows 桌面应用（UIA/ORB 模式），"
        "在回复末尾用 `[RESULT]` 标记执行结论：ok 或 fail，并附简要说明。"
    ),
    "mobile": (
        "你是 Testory 移动端测试代理。请按用户指令操作已配对的 Android 设备（ADB bridge 模式），"
        "在回复末尾用 `[RESULT]` 标记执行结论：ok 或 fail，并附简要说明。"
    ),
}


def hermes_execute_available(platform_type: str = "web") -> bool:
    plat = (platform_type or "web").strip().lower()
    if plat not in _PLATFORM_SKILL_HINT:
        return False
    try:
        from hermes_gateway_client import HermesGatewayClient

        client = HermesGatewayClient()
        if not client.is_configured():
            return False
        return client.health_check(timeout_sec=2.5)
    except Exception:
        return False


def mobile_bridge_available() -> bool:
    try:
        from mobile_sync_store import _DEVICE_TOKENS, _load_persisted

        _load_persisted()
        return bool(_DEVICE_TOKENS)
    except Exception:
        return False


def _build_hermes_instruction(
    stage: Dict[str, Any],
    context: Any,
    platform_type: str,
) -> str:
    plat = (platform_type or "web").strip().lower()
    skill_hint = _PLATFORM_SKILL_HINT.get(plat, "")
    stage_label = stage.get("label") or stage.get("id", "unknown")

    lines: List[str] = []
    lines.append(f"【Testory 平台上下文 platform={plat} skill={skill_hint} stage={stage_label}】")
    lines.append("")

    action = stage.get("action", {})
    if isinstance(action, dict) and action:
        action_type = action.get("type") or action.get("action", "")
        if action_type:
            lines.append(f"执行动作: {action_type}")
        for k, v in action.items():
            if k in ("type", "action"):
                continue
            if isinstance(v, str) and v.startswith("{{"):
                continue
            lines.append(f"  {k}: {v}")
    else:
        desc = stage.get("description") or stage.get("step", "") or str(action)
        lines.append(f"任务描述: {desc}")

    steps = stage.get("steps", [])
    if isinstance(steps, list) and steps:
        lines.append("")
        lines.append("具体步骤:")
        for i, s in enumerate(steps, 1):
            if isinstance(s, dict):
                a = s.get("action") or s.get("type", "")
                sel = s.get("selector", "")
                val = s.get("value", "")
                url = s.get("url", "")
                desc_s = f"{a}"
                if url:
                    desc_s += f" {url}"
                if sel:
                    desc_s += f" → {sel}"
                if val:
                    desc_s += f" = {val}"
                lines.append(f"  {i}. {desc_s}")
            else:
                lines.append(f"  {i}. {s}")

    vars_stored = stage.get("vars_to_store") or {}
    if isinstance(vars_stored, dict) and vars_stored:
        lines.append("")
        lines.append("需要提取的变量:")
        for vk, vv in vars_stored.items():
            lines.append(f"  {vk} (来自 {vv})")

    lines.append("")
    lines.append(
        f"请严格在 {platform_type} 平台上执行上述操作。"
        "完成后在回复末尾标注 [RESULT] ok 或 [RESULT] fail，并附简要说明。"
    )

    return "\n".join(lines)


def _parse_hermes_result(raw_response: str) -> Dict[str, Any]:
    """解析 Hermes 回复。默认失败：仅显式 ok / [RESULT] ok 才通过。"""
    result: Dict[str, Any] = {
        "ok_assert": False,
        "error": None,
        "raw": raw_response,
    }

    if not raw_response:
        result["error"] = "Hermes 返回为空"
        return result

    try:
        data = json.loads(raw_response)
        if isinstance(data, dict):
            if data.get("ok") is True or data.get("ok_assert") is True:
                result["ok_assert"] = True
            elif data.get("ok") is False or data.get("ok_assert") is False:
                result["ok_assert"] = False
                result["error"] = data.get("error", "Hermes 返回失败")
            else:
                result["error"] = data.get("error") or "Hermes JSON 未声明 ok/ok_assert，默认失败"
            if data.get("result"):
                result["result"] = data["result"]
            result["summary"] = raw_response.strip()[:600]
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    lst = raw_response.strip().lower()
    if "[result] fail" in lst or "[result]failed" in lst or "[result] fail" in lst:
        result["ok_assert"] = False
        idx = lst.rfind("[result]")
        if idx >= 0:
            result["error"] = raw_response[idx:].strip()[:500]
        else:
            result["error"] = "Hermes [RESULT] fail"
        result["summary"] = raw_response.strip()[:600]
        return result

    if "[result] ok" in lst or "[result]ok" in lst or "[result] pass" in lst:
        result["ok_assert"] = True
        result["summary"] = raw_response.strip()[:600]
        return result

    result["error"] = "Hermes 回复未包含 [RESULT] ok，默认失败（防假绿）"
    result["summary"] = raw_response.strip()[:600]
    return result


def hermes_execute_stage(
    stage: Dict[str, Any],
    context: Any,
    platform_type: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    plat = (platform_type or "web").strip().lower()
    stage_id = stage.get("id") or stage.get("stage_id", "unknown")
    resolved_stage = context.resolve_deep(dict(stage)) if hasattr(context, "resolve_deep") else dict(stage)

    instruction = _build_hermes_instruction(resolved_stage, context, plat)

    sys_prompt = _DEFAULT_SYSTEM_PROMPTS.get(plat, "")

    result: Dict[str, Any] = {
        "ok_assert": False,
        "error": None,
        "elapsed_ms": 0,
        "stage_id": stage_id,
        "layer": plat,
        "executor": "hermes",
    }

    t0 = time.perf_counter()
    try:
        from hermes_gateway_client import HermesGatewayClient

        client = HermesGatewayClient()

        old_sys = os.environ.get("HERMES_EXECUTE_SYSTEM_PROMPT", "")
        if sys_prompt:
            os.environ["HERMES_EXECUTE_SYSTEM_PROMPT"] = sys_prompt

        try:
            raw = client.execute_user_instruction(instruction, session_id=stage_id)
            parsed = _parse_hermes_result(raw)
            result["ok_assert"] = bool(parsed.get("ok_assert"))
            if parsed.get("error"):
                result["error"] = parsed["error"]
            elif not result["ok_assert"]:
                result["error"] = "Hermes 未明确成功"
            result["summary"] = parsed.get("summary", (raw or "")[:600])
            result["raw_response"] = parsed.get("raw", raw)
        finally:
            if old_sys:
                os.environ["HERMES_EXECUTE_SYSTEM_PROMPT"] = old_sys
            elif "HERMES_EXECUTE_SYSTEM_PROMPT" in os.environ:
                del os.environ["HERMES_EXECUTE_SYSTEM_PROMPT"]

    except Exception as e:
        result["ok_assert"] = False
        result["error"] = str(e)
        uat_logger.warning("hermes_execute_stage 失败 stage=%s layer=%s: %s", stage_id, plat, e)

    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result, {}
