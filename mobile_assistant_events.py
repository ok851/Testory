# -*- coding: utf-8 -*-
"""
Testory 移动端助手事件 → 用例步骤归一化（v2）。

Inspired by SoloPi:
  1. 分辨率自适应坐标（Resolution-adaptive viewport coordinates）
  2. 操作耗时记录（Action duration tracking）
  3. 增强的定位器建议优先级（Improved locator suggestion priority）
"""
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
    """
    Inspired by SoloPi: locator priority — id > accessibility_id > xpath > text > class.
    """
    rid = (node.get("resource_id") or node.get("resource-id") or "").strip()
    if rid:
        return "id", rid
    desc = (node.get("content_desc") or node.get("content-desc") or "").strip()
    if desc:
        return "accessibility_id", desc
    xpath = (node.get("xpath") or "").strip()
    if xpath:
        return "xpath", xpath
    text = (node.get("text") or "").strip()
    if text:
        return "android_uiautomator", f'new UiSelector().text("{text}")'
    cls = (node.get("class") or node.get("class_name") or "").strip()
    if cls:
        return "android_uiautomator", f'new UiSelector().className("{cls}")'
    return "accessibility_id", ""


def normalize_assistant_event(
    event: Dict[str, Any],
    screen_width: int = 0,
    screen_height: int = 0,
) -> Dict[str, Any]:
    """
    将助手 WebSocket 事件转为步骤字段草案（v2）。

    Inspired by SoloPi:
      - 支持百分比坐标（rx, ry），实现跨分辨率回放
      - 记录操作耗时（ts_start / ts_end）
      - 增强的定位器回退策略

    支持 type: click | scroll | capture | input | dialog | long-press
    """
    etype = (event.get("type") or event.get("action") or "").strip().lower()
    node = event.get("node") if isinstance(event.get("node"), dict) else {}
    op_node = event.get("operation_node") if isinstance(event.get("operation_node"), dict) else {}
    if not node and op_node:
        node = op_node
    bounds = event.get("bounds") or node.get("bounds") or op_node.get("bounds")
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

    pkg = (event.get("package") or event.get("app_package") or "").strip()
    if pkg and pkg != "com.testory.assistant":
        mobile_spec["context_package"] = pkg

    if bounds is not None:
        mobile_spec["bounds"] = bounds

    # SoloPi: 节点内相对坐标（跨分辨率回放）
    if event.get("node_rx") is not None and event.get("node_ry") is not None:
        try:
            mobile_spec["node_rx"] = float(event["node_rx"])
            mobile_spec["node_ry"] = float(event["node_ry"])
        except (TypeError, ValueError):
            pass

    if event.get("action_duration_ms") is not None:
        try:
            mobile_spec["action_duration_ms"] = int(event["action_duration_ms"])
        except (TypeError, ValueError):
            pass

    if op_node:
        mobile_spec["operation_node"] = op_node
    if isinstance(event.get("local_click_pos"), dict):
        mobile_spec["local_click_pos"] = event["local_click_pos"]
    ss = event.get("screen_size")
    if isinstance(ss, dict):
        try:
            mobile_spec["screen_width"] = int(ss.get("width") or 0)
            mobile_spec["screen_height"] = int(ss.get("height") or 0)
        except (TypeError, ValueError):
            pass

    # Inspired by SoloPi: resolution-adaptive coordinates
    if cx or cy:
        mobile_spec["viewport_coord"] = {"x": cx, "y": cy}
        # 添加百分比坐标，支持跨分辨率回放
        if screen_width > 0 and screen_height > 0:
            rx = round(cx / screen_width, 4)
            ry = round(cy / screen_height, 4)
            if 0 <= rx <= 1.0 and 0 <= ry <= 1.0:
                mobile_spec["viewport_coord"]["rx"] = rx
                mobile_spec["viewport_coord"]["ry"] = ry

    # Inspired by SoloPi: 记录操作耗时
    if event.get("ts_start") and event.get("ts_end"):
        try:
            duration = int(event["ts_end"]) - int(event["ts_start"])
            mobile_spec["action_duration_ms"] = max(0, duration)
        except (TypeError, ValueError):
            pass

    # 记录屏幕尺寸（用于回放时调整坐标）
    if screen_width > 0 and screen_height > 0:
        mobile_spec["screen_width"] = screen_width
        mobile_spec["screen_height"] = screen_height

    if etype in ("dialog",):
        return {
            "action": "tap",
            "selector_type": "",
            "selector_value": "",
            "description": event.get("description") or "处理系统弹窗",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    if etype in ("open_app", "launch_app", "app_switch"):
        app_pkg = pkg or str(event.get("input_value") or event.get("app_package") or "")
        if app_pkg:
            mobile_spec["app_package"] = app_pkg
            mobile_spec["appPackage"] = app_pkg
        return {
            "action": "open_app",
            "selector_type": "",
            "selector_value": "",
            "input_value": app_pkg,
            "description": event.get("description") or f"打开应用 {app_pkg}",
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
            coord_dict = {"x": cx, "y": cy}
            if screen_width > 0 and screen_height > 0:
                coord_dict["rx"] = round(cx / screen_width, 4)
                coord_dict["ry"] = round(cy / screen_height, 4)
            sval = json.dumps(coord_dict, ensure_ascii=False)
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
        # Inspired by SoloPi: 滑动也记录百分比坐标
        if screen_width > 0 and screen_height > 0:
            mobile_spec.update({
                "rx1": round(x1 / screen_width, 4),
                "ry1": round(y1 / screen_height, 4),
                "rx2": round(x2 / screen_width, 4),
                "ry2": round(y2 / screen_height, 4),
            })
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

    # 默认：未知事件类型，尝试映射为点击
    if cx or cy:
        coord_dict = {"x": cx, "y": cy}
        if screen_width > 0 and screen_height > 0:
            coord_dict["rx"] = round(cx / screen_width, 4)
            coord_dict["ry"] = round(cy / screen_height, 4)
        return {
            "action": "tap",
            "selector_type": "viewport_coord",
            "selector_value": json.dumps(coord_dict, ensure_ascii=False),
            "description": event.get("description") or f"操作 ({cx},{cy})",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    return {
        "action": "tap",
        "selector_type": stype,
        "selector_value": sval,
        "description": event.get("description") or "未知操作",
        "automation_layer": "android",
        "mobile_spec": mobile_spec,
    }
