# -*- coding: utf-8 -*-
"""定位器唯一性测试与元素校验。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from web_capture.playwright_locator import convert_selector


async def _resolve_locator(page, selector_type: str, selector_value: str):
    from playwright.async_api import Locator

    sv, st = convert_selector(selector_value, selector_type)
    st = (st or "css").lower()
    if st == "xpath":
        return page.locator(f"xpath={sv}")
    if st == "partial_text":
        return page.get_by_text(sv, exact=False)
    if st == "text":
        return page.get_by_text(sv, exact=True)
    if st == "name":
        return page.locator(f'[name="{sv}"]')
    if st == "aria":
        if sv.startswith("role="):
            parts = sv.split("[", 1)
            role = parts[0].replace("role=", "").strip()
            if len(parts) > 1 and "name=" in parts[1]:
                nm = parts[1].split("name=", 1)[1].rstrip("]")
                return page.get_by_role(role, name=nm)
            return page.get_by_role(role)
        if sv.startswith("name="):
            return page.get_by_label(sv.replace("name=", "", 1))
        return page.get_by_label(sv)
    if st == "placeholder":
        return page.get_by_placeholder(sv)
    if st == "label":
        return page.get_by_label(sv)
    if st == "title":
        return page.get_by_title(sv)
    if st == "alt":
        return page.get_by_alt_text(sv)
    return page.locator(sv)


async def evaluate_locator_async(
    page,
    selector_type: str,
    selector_value: str,
    *,
    max_preview: int = 8,
) -> Dict[str, Any]:
    try:
        loc = await _resolve_locator(page, selector_type, selector_value)
        count = await loc.count()
        preview: List[Dict[str, Any]] = []
        for i in range(min(count, max_preview)):
            item = loc.nth(i)
            try:
                text = (await item.inner_text(timeout=2000))[:80]
            except Exception:
                text = ""
            try:
                box = await item.bounding_box(timeout=2000)
            except Exception:
                box = None
            preview.append({"index": i, "text": text, "bbox": box})
        return {
            "success": True,
            "match_count": count,
            "unique": count == 1,
            "exists": count >= 1,
            "elements_preview": preview,
            "message": f"已找到 {count} 个元素" if count else "未找到元素",
        }
    except Exception as exc:
        return {
            "success": False,
            "match_count": 0,
            "unique": False,
            "exists": False,
            "elements_preview": [],
            "message": str(exc) or "定位失败",
            "error": str(exc),
        }


async def verify_element_async(
    page,
    selector_type: str,
    selector_value: str,
) -> Dict[str, Any]:
    test = await evaluate_locator_async(page, selector_type, selector_value, max_preview=1)
    if not test.get("exists"):
        return {
            "success": True,
            "exists": False,
            "visible": False,
            "enabled": False,
            "message": test.get("message") or "元素不存在",
        }
    try:
        loc = await _resolve_locator(page, selector_type, selector_value)
        first = loc.first
        visible = await first.is_visible(timeout=3000)
        enabled = await first.is_enabled(timeout=3000)
        return {
            "success": True,
            "exists": True,
            "visible": visible,
            "enabled": enabled,
            "message": "元素存在"
            + ("且可见" if visible else "但不可见")
            + ("且可用" if enabled else "但不可用"),
        }
    except Exception as exc:
        return {
            "success": False,
            "exists": test.get("exists"),
            "visible": False,
            "enabled": False,
            "message": str(exc),
            "error": str(exc),
        }


def sync_test_locator(page, selector_type: str, selector_value: str) -> Dict[str, Any]:
    return asyncio.run(evaluate_locator_async(page, selector_type, selector_value))


def sync_verify_element(page, selector_type: str, selector_value: str) -> Dict[str, Any]:
    return asyncio.run(verify_element_async(page, selector_type, selector_value))
