"""
执行前定位器解析：在已有页面快照（inspect / 主会话探测）上，将模糊或过宽的步骤选择器
收敛为具体 CSS/XPath/text，并可选写入 locator_candidates。

与 ai_selector_recovery（运行失败后再推断）互补；由环境变量 AI_LOCATOR_RESOLVE_ENABLE 控制，
或通过 resolve_plan_steps_locators_with_snapshot(..., force=True) 强制启用（CLI / 预览接口）。
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from logger import uat_logger


def ai_locator_resolve_enabled() -> bool:
    return os.environ.get("AI_LOCATOR_RESOLVE_ENABLE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _step_needs_locator_resolution(step: Dict[str, Any]) -> bool:
    from ai_step_normalization import is_overly_broad_css_selector

    action = (step.get("action") or "").strip().lower()
    if action not in ("click", "input", "fill", "verify"):
        return False
    sv = (step.get("selector_value") or step.get("selector") or "").strip()
    st = (step.get("selector_type") or "css").strip().lower()
    if not sv:
        return True
    if st == "css" and is_overly_broad_css_selector(sv):
        return True
    return False


def resolve_plan_steps_locators_with_snapshot(
    steps: List[Any], snap: Dict[str, Any], *, force: bool = False
) -> Tuple[List[Any], List[str]]:
    """force=True 时忽略 AI_LOCATOR_RESOLVE_ENABLE（供定位管家 CLI / 预览接口独立验证）。"""
    warnings: List[str] = []
    if not isinstance(steps, list) or not isinstance(snap, dict):
        return steps, warnings
    if not force and not ai_locator_resolve_enabled():
        return steps, warnings

    from ai_page_probe import build_locator_candidates_from_probe_entry, probe_registry_from_interactive_snapshot
    from ai_selector_recovery import (
        _apply_llm_choice,
        _extract_json_obj,
        _registry_lines,
        load_active_profile_for_inference,
    )
    from ai_local_inference import local_ai_service
    from ai_multi_provider import dispatch_chat

    _, registry, _url = probe_registry_from_interactive_snapshot(snap)
    if not registry:
        warnings.append("定位器解析：当前快照无控件列表，已跳过。")
        return steps, warnings

    need: List[Tuple[int, Dict[str, Any]]] = []
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            continue
        if _step_needs_locator_resolution(raw):
            need.append((i, raw))

    if not need:
        return steps, warnings

    max_lines = int(os.environ.get("AI_LOCATOR_RESOLVE_MAX_REGISTRY_LINES", "90") or "90")
    max_lines = min(120, max(20, max_lines))
    lines = _registry_lines(registry, max_lines)

    step_lines: List[str] = []
    for i, s in need[:40]:
        step_lines.append(
            json.dumps(
                {
                    "index": i,
                    "action": (s.get("action") or "").strip(),
                    "description": (s.get("description") or "").strip()[:400],
                    "selector_value": (s.get("selector_value") or s.get("selector") or "").strip(),
                    "selector_type": (s.get("selector_type") or "css").strip(),
                },
                ensure_ascii=False,
            )
        )

    prompt = (
        "You map UI automation steps to concrete selectors using ONLY the control list below.\n"
        "Return exactly one JSON object, no markdown. Schema:\n"
        '{"updates":[{"index":<int>,"selector_type":"css|xpath|text|partial_text",'
        '"selector_value":"<string>","probe_index":<optional int matching [n] in list>}]}\n'
        "Rules: Never output a bare single tag as selector_value (e.g. \"button\" alone). "
        "Prefer probe_index when one row clearly matches the step description. "
        "selector_value must be usable with selector_type.\n\n"
        "Steps to fix (index is 0-based in the plan steps array):\n"
        + "\n".join(step_lines)
        + "\n\nInteractive controls:\n"
        + lines
    )

    profile = load_active_profile_for_inference()
    try:
        raw = dispatch_chat(prompt, profile, local_ai_service)
    except Exception as e:
        uat_logger.warning(f"[AI_LOCATOR_RESOLVE] LLM failed: {e}")
        warnings.append(f"定位器解析失败（LLM）：{e}")
        return steps, warnings

    data = _extract_json_obj(raw)
    if not data:
        uat_logger.warning("[AI_LOCATOR_RESOLVE] unparsable JSON from model")
        warnings.append("定位器解析：模型返回无法解析为 JSON，已跳过。")
        return steps, warnings

    updates = data.get("updates")
    if not isinstance(updates, list):
        warnings.append("定位器解析：JSON 中无 updates 数组，已跳过。")
        return steps, warnings

    out_steps: List[Any] = deepcopy(steps)
    applied = 0
    for u in updates:
        if not isinstance(u, dict):
            continue
        try:
            ix = int(u.get("index", -1))
        except (TypeError, ValueError):
            continue
        if ix < 0 or ix >= len(out_steps) or not isinstance(out_steps[ix], dict):
            continue
        row = out_steps[ix]
        if not _step_needs_locator_resolution(row):
            continue

        resolved: Optional[Tuple[str, str]] = None
        if u.get("probe_index") is not None:
            try:
                resolved = _apply_llm_choice(registry, {"probe_index": int(u["probe_index"])})
            except (TypeError, ValueError):
                resolved = None
        if not resolved:
            resolved = _apply_llm_choice(
                registry,
                {
                    "selector_value": (u.get("selector_value") or "").strip(),
                    "selector_type": (u.get("selector_type") or "css").strip(),
                },
            )
        if not resolved:
            continue

        row["selector_value"] = resolved[0]
        row["selector_type"] = resolved[1]
        pi = u.get("probe_index")
        if pi is not None:
            try:
                pi_int = int(pi)
            except (TypeError, ValueError):
                pi_int = None
            if pi_int is not None:
                for ent in registry:
                    if int(ent.get("i", -1)) == pi_int:
                        lc_str = build_locator_candidates_from_probe_entry(ent)
                        if lc_str:
                            try:
                                row["locator_candidates"] = json.loads(lc_str)
                            except json.JSONDecodeError:
                                pass
                        break
        applied += 1

    if applied:
        uat_logger.info(f"[AI_LOCATOR_RESOLVE] applied {applied} step selector(s)")
    else:
        warnings.append("定位器解析：模型未给出可用映射，步骤未改写。")

    return out_steps, warnings
