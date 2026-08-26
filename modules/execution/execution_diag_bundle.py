"""
执行失败诊断包：聚合页面信号 + 可选 LLM 结构化缺陷草稿。
与 playwright_automation.sync_gather_failure_signals 配合；后续可接 CDP Log / Network（Chrome-only）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from modules.core.logger import uat_logger


def ai_failure_diag_llm_enabled() -> bool:
    return os.environ.get("AI_FAILURE_DIAG_ENABLE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def build_failure_bundle(
    failed_step: Dict[str, Any],
    exception_message: str,
    signals: Dict[str, Any],
) -> Dict[str, Any]:
    """供入库 / API 返回的稳定结构（不含 LLM）。"""
    return {
        "schema_version": 1,
        "failed_step": failed_step or {},
        "exception_message": (exception_message or "").strip()[:4000],
        "page_url": (signals.get("diagnostics") or {}).get("url"),
        "page_title": (signals.get("diagnostics") or {}).get("title"),
        "dom_signals": signals.get("domSignals") or {},
        "diagnostics": signals.get("diagnostics") or {},
        "recent_browser_events": signals.get("recent_browser_events") or [],
        "cdp": signals.get("cdp"),
    }


def classify_failure_with_llm(
    bundle: Dict[str, Any],
    *,
    force: bool = False,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """返回 (缺陷草稿 dict 或 None, 警告文案)。force=True 时忽略 AI_FAILURE_DIAG_ENABLE。"""
    warns: List[str] = []
    if not force and not ai_failure_diag_llm_enabled():
        return None, warns

    from modules.ai.ai_selector_recovery import _extract_json_obj, load_active_profile_for_inference
    from modules.ai.ai_local_inference import local_ai_service
    from modules.ai.ai_multi_provider import dispatch_chat

    prompt = (
        "You are a QA engineer. Given an automation failure bundle (JSON), reply with ONE JSON object only, "
        "no markdown. Schema:\n"
        '{"category":"locator|timing|app_bug|environment|data|unknown",'
        '"severity":"blocker|major|minor|trivial",'
        '"summary":"<short zh or en>",'
        '"user_visible_symptoms":["..."],'
        '"suspected_root_causes":["..."],'
        '"recommended_next_checks":["..."],'
        '"defect_title":"<one line>",'
        '"reproduction_outline":["high-level step strings"]}\n\n'
        "Bundle:\n"
        + json.dumps(bundle, ensure_ascii=False)[:12000]
    )
    profile = load_active_profile_for_inference()
    try:
        raw = dispatch_chat(prompt, profile, local_ai_service)
    except Exception as e:
        uat_logger.warning("[FAILURE_DIAG] LLM failed: %s", e)
        warns.append(str(e))
        return None, warns

    data = _extract_json_obj(raw)
    if not isinstance(data, dict):
        warns.append("缺陷分类：模型输出无法解析为 JSON")
        return None, warns
    return data, warns


def merge_bundle_and_draft(
    bundle: Dict[str, Any], draft: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    out = dict(bundle)
    out["llm_defect_draft"] = draft
    return out
