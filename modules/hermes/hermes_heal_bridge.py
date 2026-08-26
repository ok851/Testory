# -*- coding: utf-8
"""Hermes 自愈桥接：运行时 selector 恢复与 memory 闭环。"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple, List

from modules.core.logger import uat_logger


def hermes_heal_enabled() -> bool:
    if os.environ.get("AI_HERMES_HEAL_ENABLE", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        from modules.ai.agent_gateway_client import agent_gateway_configured

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


def _persist_to_locator_candidates(
    step: Optional[Dict[str, Any]],
    selector_value: str,
    selector_type: str,
    source: str,
) -> None:
    """将自愈成功的选择器持久化到步骤的 locator_candidates 字段。"""
    if not isinstance(step, dict):
        return
    candidate = {
        "selector_type": selector_type,
        "selector_value": selector_value,
        "score": 100,
        "source": source,
    }
    existing = step.get("locator_candidates")
    if isinstance(existing, list):
        existing.insert(0, candidate)
    else:
        step["locator_candidates"] = [candidate]


async def try_recover_selector_with_hermes(
    page: Any,
    description: str,
    action: str,
    failed_selector: str,
    registry_lines: str = "",
    step: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, str]]:
    """通过 Hermes Agent 推断新选择器（JSON 回复）。"""
    if not hermes_heal_enabled():
        return None
    from modules.ai.agent_gateway_client import get_agent_gateway_client
    from modules.ai.ai_selector_recovery import _apply_llm_choice, _extract_json_obj, _collect_registry_with_frames

    cap = int(os.environ.get("AI_SELECTOR_RECOVERY_MAX_NODES", "100") or "100")
    registry = await _collect_registry_with_frames(page, min(200, max(20, cap)))
    if not registry:
        uat_logger.warning("[AI_HERMES_HEAL] 当前页主文档未采集到可交互控件，将尝试视觉定位兜底")
        return None
    if not registry_lines:
        from modules.ai.ai_selector_recovery import _registry_lines

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
        _persist_to_locator_candidates(step, resolved[0], resolved[1], "hermes_heal")
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
        from modules.ai.ai_memory_store import ingest_repair_case

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


def build_vlm_ground_heal_candidate(description: str) -> Optional[Dict[str, Any]]:
    """DOM/heal 无 probe 时建议 Tier4 vlm_ground 候选。"""
    desc = (description or "").strip()
    if not desc:
        return None
    try:
        from modules.ai.ai_vision_grounding import locator_tier_vlm_enabled
        from modules.web.locator_tier_utils import build_vlm_ground_candidate

        if not locator_tier_vlm_enabled():
            return None
        return build_vlm_ground_candidate(desc)
    except Exception as e:
        uat_logger.debug("vlm_ground heal candidate: %s", e)
        return None


def merge_vlm_ground_into_locator_candidates(
    locator_candidates_raw: Any,
    description: str,
) -> Any:
    """将 vlm_ground 候选合并进步骤 locator_candidates（就地返回 JSON 字符串或原值）。"""
    cand = build_vlm_ground_heal_candidate(description)
    if not cand:
        return locator_candidates_raw
    try:
        from modules.web.locator_tier_utils import merge_candidates_json

        if locator_candidates_raw:
            lc_str = (
                locator_candidates_raw
                if isinstance(locator_candidates_raw, str)
                else json.dumps(locator_candidates_raw, ensure_ascii=False)
            )
            return merge_candidates_json(lc_str, [cand])
        return json.dumps([cand], ensure_ascii=False)
    except Exception as e:
        uat_logger.debug("merge vlm_ground heal: %s", e)
        return locator_candidates_raw


def apply_vlm_ground_heal_to_step(step: Dict[str, Any]) -> bool:
    """为单步写入 vlm_ground 候选（有 description 且尚无 vlm 项时）。"""
    if not isinstance(step, dict):
        return False
    desc = (
        (step.get("description") or "")
        or (step.get("locate_prompt") or "")
    ).strip()
    if not desc:
        return False
    merged = merge_vlm_ground_into_locator_candidates(step.get("locator_candidates"), desc)
    if merged == step.get("locator_candidates"):
        return False
    step["locator_candidates"] = merged
    return True


def apply_vlm_ground_heal_to_steps(steps: List[Dict[str, Any]]) -> int:
    """批量为步骤附加 vlm_ground 自愈候选。返回修改步数。"""
    n = 0
    for st in steps or []:
        if isinstance(st, dict) and apply_vlm_ground_heal_to_step(st):
            n += 1
    return n
