# -*- coding: utf-8
"""Hermes 自愈桥接：运行时 selector 恢复与 memory 闭环。"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple

from logger import uat_logger


def hermes_heal_enabled() -> bool:
    if os.environ.get("AI_HERMES_HEAL_ENABLE", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        from agent_gateway_client import agent_gateway_configured

        return agent_gateway_configured()
    except ImportError:
        return False


def hermes_locator_resolve_enabled() -> bool:
    if os.environ.get("AI_HERMES_LOCATOR_ENABLE", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    if os.environ.get("AI_HERMES_LOCATOR_ENABLE", "").strip().lower() in ("1", "true", "yes", "on"):
        return hermes_heal_enabled()
    return hermes_heal_enabled() and not _legacy_locator_llm_enabled()


def _legacy_locator_llm_enabled() -> bool:
    return os.environ.get("AI_LOCATOR_RESOLVE_LEGACY_LLM", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _legacy_selector_llm_enabled() -> bool:
    return os.environ.get("AI_SELECTOR_RECOVERY_LEGACY_LLM", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


async def try_recover_selector_with_hermes(
    page: Any,
    description: str,
    action: str,
    failed_selector: str,
    registry_lines: str = "",
) -> Optional[Tuple[str, str]]:
    """通过 Hermes Agent 推断新选择器（JSON 回复）。"""
    if not hermes_heal_enabled():
        return None
    from agent_gateway_client import get_agent_gateway_client
    from ai_selector_recovery import _apply_llm_choice, _extract_json_obj, _collect_registry_main_frame

    cap = int(os.environ.get("AI_SELECTOR_RECOVERY_MAX_NODES", "100") or "100")
    registry = await _collect_registry_main_frame(page, min(200, max(20, cap)))
    if not registry:
        return None
    if not registry_lines:
        from ai_selector_recovery import _registry_lines

        max_lines = int(os.environ.get("AI_SELECTOR_RECOVERY_MAX_LINES", "80") or "80")
        registry_lines = _registry_lines(registry, max(10, min(120, max_lines)))

    instruction = (
        f"测试步骤自愈：action={action}，描述={description}，失败选择器={failed_selector}。\n"
        f"可交互控件列表：\n{registry_lines}\n\n"
        "请只回复一个 JSON："
        '{"probe_index": N} 或 {"selector_type":"css","selector_value":"..."} '
        "必须使用列表中的控件。"
    )
    client = get_agent_gateway_client()
    raw = client.execute_user_instruction(instruction)
    try:
        payload = json.loads(raw) if raw.strip().startswith("{") else None
        if isinstance(payload, dict) and payload.get("ok") is False:
            return None
    except json.JSONDecodeError:
        payload = None
    text = raw if not isinstance(payload, dict) else raw
    data = _extract_json_obj(text)
    if not data:
        return None
    resolved = _apply_llm_choice(registry, data)
    if resolved:
        uat_logger.info("[AI_HERMES_HEAL] recovered selector via Hermes")
        sync_repair_to_memory(description, action, failed_selector, resolved[0], resolved[1])
    return resolved


def sync_repair_to_memory(
    description: str,
    action: str,
    old_selector: str,
    new_selector: str,
    selector_type: str,
) -> None:
    """成功修复后写入平台 memory（与 Hermes memory 互补）。"""
    if os.environ.get("LOCAL_MEMORY_ENABLE", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return
    try:
        from ai_memory_store import ingest_repair_case

        ingest_repair_case(
            0,
            "selector_recovery",
            {
                "step_description": description,
                "action": action,
                "failed_selector": old_selector,
                "recovered_selector": new_selector,
                "selector_type": selector_type,
                "source": "hermes_heal",
            },
        )
    except Exception as e:
        uat_logger.debug("hermes heal memory sync skipped: %s", e)
