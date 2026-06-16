# -*- coding: utf-8 -*-
"""Mobile Vision Probe：截图 + 控件树 → 可执行 Android 步骤。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _flatten_a11y_tree(node: Any, out: List[Dict[str, Any]], depth: int = 0, limit: int = 80) -> None:
    if not isinstance(node, dict) or len(out) >= limit or depth > 10:
        return
    text = (node.get("text") or node.get("content_desc") or "").strip()
    rid = (node.get("resource_id") or "").strip()
    bounds = node.get("bounds")
    if text or rid:
        entry: Dict[str, Any] = {"index": len(out) + 1}
        if rid:
            entry["resource_id"] = rid
        if text:
            entry["label"] = text[:120]
        if isinstance(bounds, list) and len(bounds) >= 4:
            entry["bounds"] = bounds
        out.append(entry)
    children = node.get("children")
    if isinstance(children, list):
        for ch in children:
            _flatten_a11y_tree(ch, out, depth + 1, limit)


def build_mobile_probe_snapshot(a11y_tree: Any) -> str:
    """压缩控件树为 LIVE snapshot 文本（供 LLM 约束选择器）。"""
    registry: List[Dict[str, Any]] = []
    root = a11y_tree
    if isinstance(a11y_tree, dict) and "tree" in a11y_tree:
        root = a11y_tree.get("tree")
    _flatten_a11y_tree(root, registry)
    lines = []
    for item in registry:
        parts = [f"[{item['index']}]"]
        if item.get("resource_id"):
            parts.append(f"id={item['resource_id']}")
        if item.get("label"):
            parts.append(f"label={item['label']}")
        if item.get("bounds"):
            parts.append(f"bounds={item['bounds']}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def execute_mobile_vision_probe(data: dict, *, user_id: int) -> Dict[str, Any]:
    data = data or {}
    goal = (data.get("goal") or "").strip()
    if not goal:
        return {"success": False, "error": "goal 不能为空", "_http": 400}

    snapshot = build_mobile_probe_snapshot(data.get("a11y_tree"))
    screenshot_b64 = (data.get("screenshot_base64") or data.get("screenshot_b64") or "").strip()
    vision_hint = ""
    if screenshot_b64:
        try:
            import base64
            from ai_vision_local import vision_describe, vision_enabled

            if vision_enabled():
                png = base64.b64decode(screenshot_b64)
                vision_hint = vision_describe(
                    png,
                    "Briefly describe this Android app screen: visible buttons, titles, input fields. "
                    "Reply in Chinese, max 400 chars.",
                )
        except Exception as exc:
            vision_hint = f"(vision skipped: {exc})"

    from ai_local_inference import local_ai_service
    from app import _get_active_local_model, _resolve_inference_profile

    selected_model = (data.get("model") or "").strip() or _get_active_local_model()
    profile, legacy_model = _resolve_inference_profile(selected_model)
    mem_block = ""
    if vision_hint:
        mem_block = f"\nVision summary of current screen:\n{vision_hint}\n"

    try:
        generated = local_ai_service.generate_case_and_steps(
            goal,
            (data.get("project_name") or "").strip(),
            model=legacy_model,
            profile=profile,
            page_snapshot=snapshot or None,
            probe_registry=None,
            memory_context=mem_block or None,
            platform_type="android",
        )
    except ValueError as e:
        return {"success": False, "error": str(e), "_http": 503}
    except Exception as e:
        return {"success": False, "error": str(e), "_http": 500}

    return {
        "success": True,
        "plan": generated,
        "probe_snapshot_lines": len(snapshot.splitlines()) if snapshot else 0,
        "vision_hint": vision_hint[:500] if vision_hint else "",
    }
