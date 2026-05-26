# -*- coding: utf-8 -*-
"""元素定义 JSON 结构与步骤表扁平化。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_element_definition(
    raw: Dict[str, Any],
    *,
    name: str = "",
    capture_mode: str = "cdp",
    locator_candidates: Optional[List[Dict[str, Any]]] = None,
    preview_image_b64: str = "",
) -> Dict[str, Any]:
    """从拾取 payload 构建完整 element_definition。"""
    element = raw.get("elementInfo", {}) or {}
    attrs = element.get("attributes", {}) or {}
    primary_type = str(raw.get("selector_type") or "css")
    primary_value = str(raw.get("selector_value") or raw.get("selector") or "")

    frame_chain = raw.get("frame_chain") or []
    if isinstance(frame_chain, str):
        try:
            frame_chain = json.loads(frame_chain)
        except json.JSONDecodeError:
            frame_chain = []

    cands = locator_candidates
    if cands is None:
        lc_raw = raw.get("locator_candidates")
        if isinstance(lc_raw, str):
            try:
                cands = json.loads(lc_raw)
            except json.JSONDecodeError:
                cands = []
        elif isinstance(lc_raw, list):
            cands = lc_raw
        else:
            cands = []

    tag = (element.get("tagName") or raw.get("tag_name") or "").lower()
    text = (element.get("textContent") or raw.get("text_content") or "").strip()
    auto_name = name.strip() or f"{tag}_{text[:24]}" if text else tag or "element"

    return {
        "version": 1,
        "name": auto_name,
        "capture_mode": capture_mode,
        "page_url": str(raw.get("source_url") or raw.get("page_url") or ""),
        "frame_chain": frame_chain,
        "primary": {
            "selector_type": primary_type,
            "selector_value": primary_value,
        },
        "locator_candidates": cands,
        "attributes": {
            "tag": tag,
            "id": element.get("id") or raw.get("id") or "",
            "class": element.get("className") or raw.get("class_name") or "",
            "name": attrs.get("name") or element.get("name") or "",
            "innerText": text,
            "aria-label": attrs.get("aria-label") or element.get("ariaLabel") or "",
            "role": attrs.get("role") or element.get("role") or "",
        },
        "dom_path": raw.get("dom_path") or [],
        "xpath_absolute": raw.get("xpath_absolute") or "",
        "xpath_relative": raw.get("xpath_relative") or raw.get("xpath") or "",
        "css_selector": raw.get("css_selector") or raw.get("selector") or "",
        "preview_image_b64": preview_image_b64 or raw.get("preview_image_b64") or "",
        "picked_at": _now_iso(),
        "key_candidates": raw.get("key_candidates") or [],
        "is_card_list_container": bool(raw.get("is_card_list_container")),
        "card_item_xpath": raw.get("card_item_xpath") or "",
    }


def flatten_to_step_fields(defn: Dict[str, Any]) -> Dict[str, Any]:
    """转为 test_steps 表单字段。"""
    primary = defn.get("primary") or {}
    cands = defn.get("locator_candidates") or []
    if isinstance(cands, str):
        lc = cands
    else:
        lc = json.dumps(cands, ensure_ascii=False)

    iframe_sel = ""
    frame_chain = defn.get("frame_chain") or []
    if frame_chain:
        last = frame_chain[-1] if isinstance(frame_chain[-1], dict) else {}
        iframe_sel = str(last.get("selector_value") or "")

    attrs = defn.get("attributes") or {}
    return {
        "selector_type": primary.get("selector_type") or "css",
        "selector_value": primary.get("selector_value") or "",
        "locator_candidates": lc,
        "text_content": attrs.get("innerText") or "",
        "page_name": defn.get("page_url") or "",
        "tag_name": attrs.get("tag") or "",
        "css_selector": defn.get("css_selector") or "",
        "id": attrs.get("id") or "",
        "class_name": attrs.get("class") or "",
        "iframe_selector": iframe_sel,
        "element_definition": json.dumps(defn, ensure_ascii=False),
        "is_card_list_container": bool(defn.get("is_card_list_container")),
        "card_item_xpath": defn.get("card_item_xpath") or "",
        "source_url": defn.get("page_url") or "",
        "capture_mode": defn.get("capture_mode") or "cdp",
    }
