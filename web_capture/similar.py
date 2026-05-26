# -*- coding: utf-8 -*-
"""相似元素批量查询。"""

from __future__ import annotations

from typing import Any, Dict, List

from web_capture.validator import evaluate_locator_async


async def find_similar_elements_async(
    page,
    selector_type: str,
    selector_value: str,
    *,
    max_items: int = 50,
) -> Dict[str, Any]:
    result = await evaluate_locator_async(
        page, selector_type, selector_value, max_preview=max_items
    )
    items: List[Dict[str, Any]] = []
    for p in result.get("elements_preview") or []:
        items.append(
            {
                "index": p.get("index", 0),
                "text": p.get("text") or "",
                "bbox": p.get("bbox"),
            }
        )
    return {
        "success": result.get("success", False),
        "count": result.get("match_count", 0),
        "items": items,
        "message": result.get("message") or "",
    }
