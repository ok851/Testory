# -*- coding: utf-8 -*-
"""移动端 UI 树结构化感知（对齐桌面 UIA 树 / Web DOM 快照）。

让 agent 从「看截图」升级为「读控件结构」，是移动端智能化的核心：

- ``get_mobile_ui_tree``：三级获取 UI 树 XML
  1. 手机 APK RPC（``plugin_rpc.get_page_source`` → uiautomator dump 兜底）
  2. APK HTTP 通道（``mobile_agent_client.agent_page_source``）
  3. 纯 ADB ``uiautomator dump``（无 APK 也能用）
- ``parse_ui_tree_to_compact_text``：归一化为 agent 可读的紧凑文本
  （对齐桌面 ``dump_foreground_uia_tree`` 的 node/类名/文本/rect/可交互格式）
- ``find_node_at_point`` / ``find_node_by_text`` / ``find_node_by_resource_id``：节点定位
- ``suggest_locator_from_node``：生成 locator 建议（供 mobile_tap 等动作回放）
"""

from __future__ import annotations

import html
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

try:
    from mobile_env_config import adb_path as _env_adb_path

    def _adb_path() -> str:
        return _env_adb_path()

except Exception:  # pragma: no cover
    def _adb_path() -> str:
        return "adb"


_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_DEFAULT_MAX_NODES = 80


# ────────────────────────────────────────────────────────────
# XML 解析基础
# ────────────────────────────────────────────────────────────

def parse_bounds(bounds: Any) -> Tuple[int, int, int, int]:
    """解析 uiautomator bounds 字符串 '[x1,y1][x2,y2]' → (x1,y1,x2,y2)。"""
    m = _BOUNDS_RE.search(str(bounds or ""))
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return 0, 0, 0, 0


def _to_bool(v: Any) -> bool:
    return str(v or "").strip().lower() in ("true", "1", "yes")


def _iter_node_dicts(xml: str) -> List[Dict[str, Any]]:
    """把 uiautomator XML 解析为节点 dict 列表（含深度）。"""
    nodes: List[Dict[str, Any]] = []
    if not xml or not xml.strip():
        return nodes
    root: Optional[ET.Element] = None
    try:
        root = ET.fromstring(xml)
    except Exception:
        # 容错：部分 dump 含非法转义，先尝试 html.unescape
        try:
            root = ET.fromstring(html.unescape(xml))
        except Exception:
            return nodes
    if root is None:
        return nodes

    stack: List[Tuple[ET.Element, int]] = [(root, 0)]
    while stack:
        el, depth = stack.pop()
        bounds = parse_bounds(el.get("bounds"))
        attrs = el.attrib
        node: Dict[str, Any] = {
            "class": attrs.get("class", ""),
            "text": attrs.get("text", ""),
            "resource_id": attrs.get("resource-id", ""),
            "content_desc": attrs.get("content-desc", ""),
            "package": attrs.get("package", ""),
            "bounds": bounds,
            "bounds_arr": list(bounds),
            "clickable": _to_bool(attrs.get("clickable")),
            "focusable": _to_bool(attrs.get("focusable")),
            "checked": _to_bool(attrs.get("checked")),
            "enabled": _to_bool(attrs.get("enabled", "true")),
            "visible": _to_bool(attrs.get("visible-to-user", "true")),
            "depth": depth,
            "index": attrs.get("index", ""),
        }
        # 面积：用于 find_node_at_point 选最小命中
        w = max(0, bounds[2] - bounds[0])
        h = max(0, bounds[3] - bounds[1])
        node["area"] = w * h
        nodes.append(node)
        # 逆序 push 子节点，保证同层顺序稳定
        children = list(el)
        for child in reversed(children):
            stack.append((child, depth + 1))
    return nodes


def _is_meaningful(node: Dict[str, Any]) -> bool:
    """是否值得展示给 agent：有文本 / 可点击 / 有资源 id / 有内容描述。"""
    if node.get("text") and str(node["text"]).strip():
        return True
    if node.get("resource_id") and str(node["resource_id"]).strip():
        return True
    if node.get("content_desc") and str(node["content_desc"]).strip():
        return True
    if node.get("clickable"):
        return True
    return False


# ────────────────────────────────────────────────────────────
# 紧凑文本归一化（agent 读结构）
# ────────────────────────────────────────────────────────────

def parse_ui_tree_to_compact_text(
    xml: str,
    *,
    max_nodes: int = _DEFAULT_MAX_NODES,
    include_all: bool = False,
) -> str:
    """把 uiautomator XML 归一化为紧凑文本，供 agent 直接阅读。

    格式（对齐桌面 UIA 树的 name/control_type/rect/interactive）::

        [0] FrameLayout bounds=(0,0,1080,1920)
          [3] Button 登录 resource=com.demo:id/login bounds=(100,200,300,280) clickable

    只保留有意义的节点（文本/可点击/resource-id），避免把容器噪声塞给模型。
    """
    nodes = _iter_node_dicts(xml)
    lines: List[str] = []
    count = 0
    for i, node in enumerate(nodes):
        if count >= max_nodes:
            lines.append(f"...（已截断，共 {len(nodes)} 节点，仅显示前 {count} 个）")
            break
        if not include_all and not _is_meaningful(node):
            continue
        parts: List[str] = []
        cls = str(node["class"] or "").split(".")[-1] or "Node"
        parts.append(cls)
        if node["text"]:
            parts.append(f"「{node['text']}」")
        if node["content_desc"]:
            parts.append(f"desc={node['content_desc']}")
        if node["resource_id"]:
            parts.append(f"id={node['resource_id']}")
        if node["clickable"]:
            parts.append("clickable")
        if node["enabled"] is False:
            parts.append("disabled")
        b = node["bounds"]
        if b[2] > b[0] and b[3] > b[1]:
            parts.append(f"bounds=({b[0]},{b[1]},{b[2]},{b[3]})")
        indent = "  " * min(node["depth"], 6)
        lines.append(f"{indent}[{count}] {' '.join(parts)}")
        count += 1
    return "\n".join(lines) or "（无可见节点）"


# ────────────────────────────────────────────────────────────
# 节点定位
# ────────────────────────────────────────────────────────────

def find_node_at_point(xml: str, x: int, y: int) -> Optional[Dict[str, Any]]:
    """按坐标找「包含该点且面积最小」的节点（与测试 test_mobile_ui_probe 对齐）。"""
    best: Optional[Dict[str, Any]] = None
    for node in _iter_node_dicts(xml):
        b = node["bounds"]
        if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            if best is None or node["area"] < best["area"]:
                best = node
    return best


def find_node_by_text(
    xml: str,
    text: str,
    *,
    fuzzy: bool = True,
) -> Optional[Dict[str, Any]]:
    """按文本匹配节点（优先精确，其次包含）。"""
    want = (text or "").strip()
    if not want:
        return None
    nodes = _iter_node_dicts(xml)
    for node in nodes:
        if str(node["text"] or "").strip() == want:
            return node
    if fuzzy:
        for node in nodes:
            if want in str(node["text"] or ""):
                return node
            if want in str(node["content_desc"] or ""):
                return node
    return None


def find_node_by_resource_id(xml: str, resource_id: str) -> Optional[Dict[str, Any]]:
    want = (resource_id or "").strip()
    if not want:
        return None
    for node in _iter_node_dicts(xml):
        if str(node["resource_id"] or "").strip() == want:
            return node
    return None


def find_node_by_content_desc(xml: str, desc: str) -> Optional[Dict[str, Any]]:
    want = (desc or "").strip()
    if not want:
        return None
    for node in _iter_node_dicts(xml):
        if str(node["content_desc"] or "").strip() == want:
            return node
    return None


def locate_center(node: Optional[Dict[str, Any]]) -> Optional[Tuple[int, int]]:
    """返回节点中心坐标（用于 tap）。"""
    if not node:
        return None
    b = node.get("bounds") or (0, 0, 0, 0)
    if b[2] <= b[0] or b[3] <= b[1]:
        return None
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


def suggest_locator_from_node(node: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """从节点生成回放用 locator 建议（与测试 test_mobile_ui_probe 对齐）。"""
    if not node:
        return {"strategy": "xpath", "selector_value": ""}
    rid = str(node.get("resource_id") or "").strip()
    if rid:
        return {"strategy": "id", "selector_value": rid}
    text = str(node.get("text") or "").strip()
    if text:
        return {"strategy": "text", "selector_value": text}
    desc = str(node.get("content_desc") or "").strip()
    if desc:
        return {"strategy": "accessibility_id", "selector_value": desc}
    cls = str(node.get("class") or "").strip()
    b = node.get("bounds") or (0, 0, 0, 0)
    return {"strategy": "xpath", "selector_value": f"//{cls}[@bounds='[{b[0]},{b[1]}][{b[2]},{b[3]}]']"}


# ────────────────────────────────────────────────────────────
# UI 树获取（三级兜底）
# ────────────────────────────────────────────────────────────

def _adb_uiautomator_dump(serial: str) -> str:
    """纯 ADB uiautomator dump → 读取本地 XML。返回 XML 字符串，失败返回空串。"""
    remote = "/sdcard/testory_uidump.xml"
    cmd = [_adb_path()]
    if serial:
        cmd.extend(["-s", serial])
    try:
        subprocess.run(
            cmd + ["shell", "uiautomator", "dump", remote],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        time.sleep(0.3)
        proc = subprocess.run(
            cmd + ["exec-out", "cat", remote],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def get_mobile_ui_tree(
    serial: str = "",
    *,
    user_id: int = 0,
    max_nodes: int = _DEFAULT_MAX_NODES,
    timeout_sec: float = 15.0,
) -> Dict[str, Any]:
    """三级获取移动端 UI 树：RPC → APK HTTP → ADB dump。

    返回：{success, serial, xml, compact_text, node_count, nodes(精简), source}
    """
    if not (serial or "").strip() and user_id is not None:
        try:
            from mobile_scrcpy_vision import get_device_serial_for_user

            serial = get_device_serial_for_user(int(user_id or 0))
        except Exception:
            serial = ""
    serial = (serial or "").strip()

    xml = ""
    source = ""

    # 一级：手机 APK RPC（plugin_rpc.get_page_source 自带 uiautomator dump 兜底）
    try:
        from mobile_automation_gateway.plugin_rpc import get_page_source

        res = get_page_source(serial)
        if isinstance(res, dict):
            xml = str(res.get("xml") or res.get("page_source") or "").strip()
        if xml:
            source = "plugin_rpc"
    except Exception:
        pass

    # 二级：APK HTTP 通道
    if not xml:
        try:
            from mobile_agent_client import agent_page_source

            res = agent_page_source(serial)
            if isinstance(res, dict) and res.get("success"):
                xml = str(res.get("xml") or res.get("page_source") or "").strip()
            if xml:
                source = "agent_http"
        except Exception:
            pass

    # 三级：纯 ADB uiautomator dump
    if not xml:
        xml = _adb_uiautomator_dump(serial)
        if xml:
            source = "adb_dump"

    if not xml:
        return {
            "success": False,
            "error": "无法获取移动端 UI 树（RPC/APK HTTP/ADB dump 均失败）",
            "error_code": "UI_TREE_UNAVAILABLE",
            "serial": serial,
        }

    nodes = _iter_node_dicts(xml)
    compact_text = parse_ui_tree_to_compact_text(xml, max_nodes=max_nodes)
    # 精简节点列表（只保留有意义的，最多 40 个，避免回包过大）
    slim: List[Dict[str, Any]] = []
    for n in nodes:
        if len(slim) >= 40:
            break
        if not _is_meaningful(n):
            continue
        slim.append(
            {
                "class": n["class"],
                "text": n["text"],
                "resource_id": n["resource_id"],
                "content_desc": n["content_desc"],
                "bounds": n["bounds_arr"],
                "clickable": n["clickable"],
                "depth": n["depth"],
            }
        )
    return {
        "success": True,
        "ok": True,
        "serial": serial,
        "source": source,
        "xml": xml[:20000],
        "compact_text": compact_text,
        "node_count": len(nodes),
        "nodes": slim,
    }


def get_screen_size(serial: str = "") -> Tuple[int, int]:
    """设备物理分辨率（供 scrcpy 注入坐标换算）。"""
    try:
        from mobile_adb_control import adb_get_screen_size

        return adb_get_screen_size(serial)
    except Exception:
        return 1080, 1920
