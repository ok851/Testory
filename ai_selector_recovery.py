"""
当主选择器与 locator_candidates 均失败时，基于**当前页主文档 DOM 摘要** + 已配置的 LLM 推断备选定位。

对标 Skyvern 类产品的「自然语言目标 + 页面理解」兜底能力；本平台使用**可交互控件列表**而非视觉模型，
成本低、易私有化，复杂 iframe 场景需在步骤描述中说明或后续扩展多 frame 采集。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from logger import uat_logger


def load_active_profile_for_inference() -> Dict[str, Any]:
    """读取 ai_model_registry.json 中当前 profile；无文件时退回本地 Ollama。"""
    default_model = os.environ.get("LOCAL_LLM_MODEL_MID", "llama3:8b-instruct")
    path = os.path.join(os.path.dirname(__file__), "ai_model_registry.json")
    if not os.path.isfile(path):
        return {
            "provider": "ollama",
            "api_style": "ollama",
            "model_id": default_model,
            "api_key": "",
            "base_url": "",
        }
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {
            "provider": "ollama",
            "api_style": "ollama",
            "model_id": default_model,
            "api_key": "",
            "base_url": "",
        }
    profiles = raw.get("profiles") or []
    aid = (raw.get("active_profile_id") or "").strip()
    for p in profiles:
        if isinstance(p, dict) and p.get("id") == aid:
            return p
    if profiles and isinstance(profiles[0], dict):
        return profiles[0]
    mid = (raw.get("active_local_model") or default_model).strip() or default_model
    return {
        "provider": "ollama",
        "api_style": "ollama",
        "model_id": mid,
        "api_key": "",
        "base_url": "",
    }


async def _collect_registry_main_frame(page: Any, cap: int) -> List[Dict[str, Any]]:
    from ai_page_probe import (
        _COLLECT_INTERACTIVE_JS,
        _COLLECT_INTERACTIVE_JS_FLAT,
        _recommended_selector,
    )

    rows: Any = []
    try:
        rows = await page.evaluate(_COLLECT_INTERACTIVE_JS, cap)
    except Exception:
        try:
            rows = await page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT, cap)
        except Exception:
            rows = []
    if not isinstance(rows, list):
        return []
    registry: List[Dict[str, Any]] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        rec, rty = _recommended_selector(raw)
        registry.append(
            {
                "i": i,
                "frame": "main",
                "tag": raw.get("tag") or "",
                "id": raw.get("id") or "",
                "name": raw.get("name") or "",
                "typ": raw.get("typ") or "",
                "ph": raw.get("ph") or "",
                "al": raw.get("al") or "",
                "rid": raw.get("rid") or "",
                "txt": raw.get("txt") or "",
                "href": (raw.get("href") or "")[:80],
                "css": raw.get("css") or "",
                "testid": raw.get("testid") or "",
                "recommended_selector": rec,
                "recommended_selector_type": rty,
            }
        )
    return registry


def _registry_lines(registry: List[Dict[str, Any]], max_lines: int) -> str:
    lines: List[str] = []
    for e in registry[:max_lines]:
        txt = str(e.get("txt") or "")[:48]
        lines.append(
            f"[{e['i']}] <{e.get('tag')}> "
            f"rec=({e.get('recommended_selector_type')}){e.get('recommended_selector')} "
            f"id={e.get('id')} ph={e.get('ph')} txt={txt}"
        )
    return "\n".join(lines)


def _extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        out = json.loads(t)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", t)
        if m:
            try:
                out = json.loads(m.group(0))
                return out if isinstance(out, dict) else None
            except json.JSONDecodeError:
                pass
    return None


def _apply_llm_choice(
    registry: List[Dict[str, Any]], data: Dict[str, Any]
) -> Optional[Tuple[str, str]]:
    if "probe_index" in data:
        try:
            ix = int(data["probe_index"])
        except (TypeError, ValueError):
            return None
        for e in registry:
            if int(e.get("i", -1)) == ix:
                rec = (e.get("recommended_selector") or "").strip()
                rty = (e.get("recommended_selector_type") or "css").strip().lower()
                if rec:
                    return rec, rty
        return None
    sv = (data.get("selector_value") or data.get("selector") or "").strip()
    st = (data.get("selector_type") or "css").strip().lower()
    allowed = frozenset(
        (
            "css",
            "xpath",
            "text",
            "label",
            "placeholder",
            "partial_text",
            "id",
            "name",
            "title",
            "alt",
            "aria",
            "data",
        )
    )
    if sv and st in allowed:
        return sv, st
    return None


async def try_recover_selector_with_vision(
    page: Any,
    description: str,
    action: str,
    failed_selector: str,
    registry: List[Dict[str, Any]],
    lines: str,
) -> Optional[Tuple[str, str]]:
    if os.environ.get("LOCAL_VISION_RECOVERY", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return None
    from ai_vision_local import vision_describe, vision_enabled

    if not vision_enabled():
        return None
    desc = (description or "").strip()
    if not desc or not registry:
        return None
    import asyncio

    try:
        shot = await page.screenshot(type="png")
    except Exception as e:
        uat_logger.warning(f"[AI_RECOVERY_VISION] screenshot failed: {e}")
        return None
    prompt = (
        "You see a full-page screenshot of a web application. The UI automation could not use this "
        f"selector: {failed_selector!s}\n"
        f"Step goal (may be Chinese): {desc}\n"
        f"Action type: {action}\n\n"
        "Below is a list of interactive controls detected on the same page. Choose the one [n] that best "
        f"matches the goal.\n{lines}\n\n"
        "Reply with ONLY one JSON object, no markdown, e.g. "
        '{"probe_index": 3} or {"selector_type":"css","selector_value":"#login"} with values consistent '
        "with the list above."
    )
    try:
        raw = await asyncio.to_thread(vision_describe, shot, prompt)
    except Exception as e:
        uat_logger.warning(f"[AI_RECOVERY_VISION] vision call failed: {e}")
        return None
    data = _extract_json_obj(raw)
    if not data:
        uat_logger.warning("[AI_RECOVERY_VISION] no JSON in model output")
        return None
    resolved = _apply_llm_choice(registry, data)
    if not resolved:
        uat_logger.warning("[AI_RECOVERY_VISION] choice did not map to a selector")
        return None
    uat_logger.info(
        f"[AI_RECOVERY_VISION] type={resolved[1]} selector={resolved[0][:160]!r}"
    )
    return resolved


async def try_recover_selector_with_llm(
    page: Any, description: str, action: str, failed_selector: str
) -> Optional[Tuple[str, str]]:
    if os.environ.get("AI_SELECTOR_FALLBACK", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return None
    desc = (description or "").strip()
    if not desc:
        return None

    cap = int(os.environ.get("AI_SELECTOR_RECOVERY_MAX_NODES", "100") or "100")
    cap = min(200, max(20, cap))
    registry = await _collect_registry_main_frame(page, cap)
    if not registry:
        uat_logger.warning("[AI_RECOVERY] 当前页主文档未采集到可交互控件，跳过 LLM 兜底")
        return None

    max_lines = int(os.environ.get("AI_SELECTOR_RECOVERY_MAX_LINES", "80") or "80")
    lines = _registry_lines(registry, max(10, min(120, max_lines)))
    prompt = (
        "You are a test automation assistant. Pick the best matching control for the step.\n"
        f"Action type: {action}\n"
        f"Step description (may be Chinese): {desc}\n"
        f"Failed selector (avoid reusing unless no alternative): {failed_selector}\n\n"
        "Interactive controls (main frame only):\n"
        f"{lines}\n\n"
        "Reply with ONLY one JSON object, no markdown code fences:\n"
        'Prefer {"probe_index": <integer matching [n] above>}.\n'
        'Or {"selector_value":"...","selector_type":"css|text|xpath|label|placeholder|partial_text"} '
        "only using plausible values from the list."
    )

    profile = load_active_profile_for_inference()
    from ai_local_inference import local_ai_service
    from ai_multi_provider import dispatch_chat

    try:
        raw = await asyncio.to_thread(dispatch_chat, prompt, profile, local_ai_service)
    except Exception as e:
        uat_logger.warning(f"[AI_RECOVERY] LLM 调用失败: {e}")
        return await try_recover_selector_with_vision(
            page, desc, action, failed_selector, registry, lines
        )

    data = _extract_json_obj(raw)
    if not data:
        uat_logger.warning("[AI_RECOVERY] 无法解析 LLM 返回 JSON")
        return await try_recover_selector_with_vision(
            page, desc, action, failed_selector, registry, lines
        )
    resolved = _apply_llm_choice(registry, data)
    if not resolved:
        uat_logger.warning("[AI_RECOVERY] LLM 返回未映射到有效选择器")
        return await try_recover_selector_with_vision(
            page, desc, action, failed_selector, registry, lines
        )
    uat_logger.info(
        f"[AI_RECOVERY] LLM 兜底定位: type={resolved[1]} selector={resolved[0][:160]!r}"
    )
    return resolved
