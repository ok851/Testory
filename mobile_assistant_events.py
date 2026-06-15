# -*- coding: utf-8 -*-
"""Testory 移动端助手事件 → 用例步骤归一化。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple


def _bounds_center(bounds: Any) -> Tuple[int, int]:
    if isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
        l, t, r, b = [int(x) for x in bounds[:4]]
        return (l + r) // 2, (t + b) // 2
    if isinstance(bounds, dict):
        l = int(bounds.get("left") or bounds.get("l") or 0)
        t = int(bounds.get("top") or bounds.get("t") or 0)
        r = int(bounds.get("right") or bounds.get("r") or l)
        b = int(bounds.get("bottom") or bounds.get("b") or t)
        return (l + r) // 2, (t + b) // 2
    return 0, 0


def suggest_locator_from_node(node: Dict[str, Any]) -> Tuple[str, str]:
    rid = (node.get("resource_id") or node.get("resource-id") or "").strip()
    if rid:
        return "id", rid
    desc = (node.get("content_desc") or node.get("content-desc") or "").strip()
    if desc:
        return "accessibility_id", desc
    text = (node.get("text") or "").strip()
    if text:
        return "android_uiautomator", f'new UiSelector().text("{text}")'
    cls = (node.get("class") or node.get("class_name") or "").strip()
    if cls:
        return "android_uiautomator", f'new UiSelector().className("{cls}")'
    return "accessibility_id", ""


def normalize_assistant_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    将助手 WebSocket 事件转为步骤字段草案。
    支持 type: click | scroll | capture | input
    """
    etype = (event.get("type") or event.get("action") or "").strip().lower()
    node = event.get("node") if isinstance(event.get("node"), dict) else {}
    bounds = event.get("bounds") or node.get("bounds")
    cx, cy = _bounds_center(bounds)
    if event.get("x") is not None and event.get("y") is not None:
        try:
            cx, cy = int(event["x"]), int(event["y"])
        except (TypeError, ValueError):
            pass

    stype, sval = suggest_locator_from_node(node)
    if event.get("selector_type"):
        stype = str(event.get("selector_type"))
    if event.get("selector_value"):
        sval = str(event.get("selector_value"))

    mobile_spec: Dict[str, Any] = {"source": "assistant"}
    if bounds is not None:
        mobile_spec["bounds"] = bounds
    if cx or cy:
        mobile_spec["viewport_coord"] = {"x": cx, "y": cy}

    if etype in ("dialog",):
        return {
            "action": "tap",
            "selector_type": "",
            "selector_value": "",
            "description": event.get("description") or "处理系统弹窗",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    if etype in ("click", "tap", "view_clicked", "capture", "long-press"):
        action = "tap" if etype != "long-press" else "long_press"
        description = (
            event.get("description")
            or node.get("text")
            or node.get("content_desc")
            or f"点击 ({cx},{cy})"
        )
        if not sval and (cx or cy):
            stype = "viewport_coord"
            sval = json.dumps({"x": cx, "y": cy}, ensure_ascii=False)
        return {
            "action": action,
            "selector_type": stype,
            "selector_value": sval,
            "description": str(description),
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    if etype in ("scroll", "swipe", "view_scrolled"):
        x1 = int(event.get("x1") or event.get("from_x") or cx)
        y1 = int(event.get("y1") or event.get("from_y") or cy)
        x2 = int(event.get("x2") or event.get("to_x") or x1)
        y2 = int(event.get("y2") or event.get("to_y") or y1)
        mobile_spec.update({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        return {
            "action": "swipe",
            "selector_type": "",
            "selector_value": "",
            "description": event.get("description") or f"滑动 ({x1},{y1})→({x2},{y2})",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    if etype in ("input", "text_changed", "type"):
        text = str(event.get("text") or event.get("input_value") or "")
        return {
            "action": "input_text",
            "selector_type": stype,
            "selector_value": sval,
            "input_value": text,
            "description": event.get("description") or f"输入 {text[:24]}",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    return {
        "action": "tap",
        "selector_type": stype,
        "selector_value": sval,
        "description": event.get("description") or "助手事件",
        "automation_layer": "android",
        "mobile_spec": mobile_spec,
    }
