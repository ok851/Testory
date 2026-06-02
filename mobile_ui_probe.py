# -*- coding: utf-8 -*-
"""通过 adb uiautomator dump 在坐标处探测 UI 节点。"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from mobile_env_config import adb_path

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _run_adb(udid: str, *args: str, timeout: int = 25) -> subprocess.CompletedProcess:
    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _parse_bounds(raw: str) -> Optional[Tuple[int, int, int, int]]:
    m = _BOUNDS_RE.search(raw or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def _node_area(bounds: Tuple[int, int, int, int]) -> int:
    l, t, r, b = bounds
    return max(0, r - l) * max(0, b - t)


def _point_in_bounds(x: int, y: int, bounds: Tuple[int, int, int, int]) -> bool:
    l, t, r, b = bounds
    return l <= x <= r and t <= y <= b


def dump_ui_xml(udid: str = "") -> Optional[str]:
    """拉取当前界面 uiautomator XML 文本。"""
    remote = "/sdcard/testory_uidump.xml"
    proc = _run_adb(udid, "shell", "uiautomator", "dump", remote)
    if proc.returncode != 0:
        return None
    fd, local = tempfile.mkstemp(suffix=".xml", prefix="testory_uidump_")
    os.close(fd)
    try:
        pull = _run_adb(udid, "pull", remote, local)
        if pull.returncode != 0 or not os.path.isfile(local):
            return None
        with open(local, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    finally:
        try:
            os.remove(local)
        except OSError:
            pass
        _run_adb(udid, "shell", "rm", "-f", remote)


def find_node_at_point(
    xml_text: str, x: int, y: int, *, prefer_clickable: bool = True
) -> Optional[Dict[str, Any]]:
    """在 hierarchy 中找包含 (x,y) 的最小面积节点。"""
    if not xml_text or not xml_text.strip():
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    hits: List[Dict[str, Any]] = []
    for el in root.iter():
        bounds_raw = el.attrib.get("bounds") or ""
        bounds = _parse_bounds(bounds_raw)
        if not bounds or not _point_in_bounds(x, y, bounds):
            continue
        clickable = (el.attrib.get("clickable") or "").lower() == "true"
        enabled = (el.attrib.get("enabled") or "true").lower() != "false"
        node = {
            "class": el.attrib.get("class") or "",
            "resource_id": el.attrib.get("resource-id") or "",
            "text": el.attrib.get("text") or "",
            "content_desc": el.attrib.get("content-desc") or "",
            "package": el.attrib.get("package") or "",
            "clickable": clickable,
            "enabled": enabled,
            "bounds": list(bounds),
            "area": _node_area(bounds),
        }
        hits.append(node)

    if not hits:
        return None
    if prefer_clickable:
        clickable_hits = [h for h in hits if h.get("clickable")]
        if clickable_hits:
            hits = clickable_hits
    hits.sort(key=lambda h: (h.get("area") or 0))
    return hits[0]


def suggest_locator_from_node(node: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """根据节点属性给出推荐定位。"""
    rid = (node.get("resource_id") or "").strip()
    if rid and rid != "null":
        return {"strategy": "id", "selector_type": "id", "selector_value": rid}

    desc = (node.get("content_desc") or "").strip()
    if desc:
        return {
            "strategy": "accessibility_id",
            "selector_type": "accessibility_id",
            "selector_value": desc,
        }

    text = (node.get("text") or "").strip()
    if text:
        esc = text.replace('"', '\\"')
        return {
            "strategy": "android_uiautomator",
            "selector_type": "android_uiautomator",
            "selector_value": f'new UiSelector().text("{esc}")',
        }
    return None


def pick_at_point(udid: str, x: int, y: int, *, half_size: int = 40) -> Dict[str, Any]:
    """
    在设备坐标 (x,y) 拾取元素与可选图像模板，供前端写入步骤。
    """
    from mobile_device_manager import capture_screenshot_png
    from mobile_image_engine import build_visual_template_json

    x, y = int(x), int(y)
    xml_text = dump_ui_xml(udid)
    node = find_node_at_point(xml_text, x, y) if xml_text else None
    locator = suggest_locator_from_node(node) if node else None

    png = capture_screenshot_png(udid)
    visual_value = ""
    if png:
        try:
            visual_value = build_visual_template_json(png, x, y, half_size=half_size)
        except Exception:
            visual_value = ""

    coord_value = f"{x},{y}"
    suggestions: List[Dict[str, Any]] = []

    if locator:
        label = (
            (node or {}).get("text")
            or (node or {}).get("content_desc")
            or locator.get("selector_value")
            or "元素"
        )
        suggestions.append({
            "kind": "element",
            "action": "tap",
            "selector_type": locator["selector_type"],
            "selector_value": locator["selector_value"],
            "strategy": locator["strategy"],
            "description": f"点击「{str(label)[:40]}」",
            "automation_layer": "android",
        })

    suggestions.append({
        "kind": "coord",
        "action": "tap",
        "selector_type": "viewport_coord",
        "selector_value": coord_value,
        "strategy": "viewport_coord",
        "description": f"坐标点击 ({x}, {y})",
        "automation_layer": "android",
        "mobile_spec": {"tap_x": x, "tap_y": y},
    })

    if visual_value:
        suggestions.append({
            "kind": "image",
            "action": "tap_image",
            "selector_type": "visual_template",
            "selector_value": visual_value,
            "strategy": "visual_template",
            "description": f"图像识别点击 ({x}, {y})",
            "automation_layer": "android",
            "mobile_spec": {"anchor_x": x, "anchor_y": y},
        })

    return {
        "x": x,
        "y": y,
        "node": node,
        "locator": locator,
        "suggestions": suggestions,
        "default_kind": "element" if locator else ("image" if visual_value else "coord"),
    }
