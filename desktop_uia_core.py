# -*- coding: utf-8 -*-
"""
UIAutomationCore 原生接口封装（直接调用 Windows UIAutomation COM API）。
支持CEF/Chromium应用的控件树深度遍历，提取acc-name等关键属性。
"""

from __future__ import annotations

import sys
import pythoncom
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_uia_core 仅支持 Windows")

_CONTROL_TYPE_MAP = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "List",
    50008: "ListItem",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
    50039: "SemanticZoom",
    50040: "AppBar",
}

_INTERACTIVE_CONTROL_TYPES = {
    50000,  # Button
    50003,  # ComboBox
    50004,  # Edit
    50005,  # Hyperlink
    50007,  # List
    50008,  # ListItem
    50010,  # MenuBar
    50011,  # MenuItem
    50013,  # RadioButton
    50018,  # Tab
    50019,  # TabItem
    50021,  # ToolBar
    50023,  # Tree
    50024,  # TreeItem
    50029,  # DataItem
}

_CONTAINER_CONTROL_TYPES = {
    50022,  # ToolTip
    50026,  # Group
    50032,  # Window
    50033,  # Pane
    50036,  # Table
    50037,  # TitleBar
}

_PSEUDO_CONTAINER_PATTERNS = (
    "chrome",
    "renderwidget",
    "legacy window",
    "widgetwin",
    "corewindow",
    "webview",
    "directui",
    "cef",
    "electron",
    "chromium",
    "tabwindowclass",
)


def _ensure_com_initialized():
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass


def _get_uia() -> Any:
    _ensure_com_initialized()
    try:
        from comtypes import client
        return client.CreateObject("UIAutomationCore.UIAutomation")
    except Exception:
        return None


def _get_tree_walker(uia: Any, walker_type: str = "control") -> Any:
    try:
        if walker_type == "raw":
            return uia.RawViewWalker
        elif walker_type == "content":
            return uia.ContentViewWalker
        else:
            return uia.ControlViewWalker
    except Exception:
        try:
            return uia.ControlViewWalker
        except Exception:
            return None


class UIAElement:
    """UIAutomation元素封装"""

    def __init__(self, com_element: Any, tree_walker: Any = None):
        self._element = com_element
        self._tree_walker = tree_walker

    def _get_tree_walker(self) -> Any:
        if self._tree_walker:
            return self._tree_walker
        uia = _get_uia()
        if uia:
            return _get_tree_walker(uia)
        return None

    def get_acc_name(self) -> str:
        """获取可访问性名称（acc-name）"""
        try:
            return str(self._element.CurrentName or "").strip()
        except Exception:
            return ""

    def get_control_type(self) -> str:
        """获取控件类型"""
        try:
            ct = self._element.CurrentControlType
            return _CONTROL_TYPE_MAP.get(ct, str(ct))
        except Exception:
            return ""

    def get_control_type_id(self) -> int:
        """获取控件类型ID"""
        try:
            return self._element.CurrentControlType
        except Exception:
            return 0

    def get_class_name(self) -> str:
        """获取类名"""
        try:
            return str(self._element.CurrentClassName or "").strip()
        except Exception:
            return ""

    def get_bounding_rect(self) -> Tuple[int, int, int, int]:
        """获取边界矩形"""
        try:
            rect = self._element.CurrentBoundingRectangle
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            return (0, 0, 0, 0)

    def get_is_enabled(self) -> bool:
        """获取是否启用"""
        try:
            return bool(self._element.CurrentIsEnabled)
        except Exception:
            return True

    def get_runtime_id(self) -> str:
        """获取运行时ID"""
        try:
            rid = self._element.GetRuntimeId()
            return ",".join(str(i) for i in rid)
        except Exception:
            return ""

    def get_first_child(self) -> Optional['UIAElement']:
        """获取第一个子元素"""
        walker = self._get_tree_walker()
        if not walker:
            return None
        try:
            child = walker.GetFirstChildElement(self._element)
            return UIAElement(child, walker) if child else None
        except Exception:
            return None

    def get_next_sibling(self) -> Optional['UIAElement']:
        """获取下一个兄弟元素"""
        walker = self._get_tree_walker()
        if not walker:
            return None
        try:
            sibling = walker.GetNextSiblingElement(self._element)
            return UIAElement(sibling, walker) if sibling else None
        except Exception:
            return None

    def get_parent(self) -> Optional['UIAElement']:
        """获取父元素"""
        walker = self._get_tree_walker()
        if not walker:
            return None
        try:
            parent = walker.GetParentElement(self._element)
            return UIAElement(parent, walker) if parent else None
        except Exception:
            return None

    def is_interactive(self) -> bool:
        """判断是否为可交互控件"""
        ct_id = self.get_control_type_id()
        return ct_id in _INTERACTIVE_CONTROL_TYPES

    def is_container(self) -> bool:
        """判断是否为容器控件"""
        ct_id = self.get_control_type_id()
        return ct_id in _CONTAINER_CONTROL_TYPES

    def is_pseudo_container(self) -> bool:
        """判断是否为伪容器（CEF/Chromium渲染容器）"""
        name = self.get_acc_name().lower()
        cls = self.get_class_name().lower()
        ct = self.get_control_type().lower()
        combined = f"{name} {cls} {ct}"
        return any(p in combined for p in _PSEUDO_CONTAINER_PATTERNS)

    def get_full_path(self) -> List[Dict[str, Any]]:
        """获取从桌面到目标元素的完整路径"""
        path = []
        current: Optional[UIAElement] = self

        while current:
            name = current.get_acc_name()
            control_type = current.get_control_type()
            class_name = current.get_class_name()
            rect = current.get_bounding_rect()

            node = {
                "name": name,
                "control_type": control_type,
                "class_name": class_name,
                "rect": rect,
            }

            path.insert(0, node)
            current = current.get_parent()

        return path

    def find_deepest_interactive(self, max_depth: int = 10, depth: int = 0) -> 'UIAElement':
        """深度遍历找到最底层可交互元素"""
        if depth >= max_depth:
            return self

        if self.is_interactive():
            return self

        child = self.get_first_child()
        best_result = self

        while child:
            if child.is_container() and not child.is_pseudo_container():
                child = child.get_next_sibling()
                continue

            try:
                result = child.find_deepest_interactive(max_depth, depth + 1)

                result_ct = result.get_control_type_id()
                best_ct = best_result.get_control_type_id()

                result_score = 0
                best_score = 0

                if result_ct in _INTERACTIVE_CONTROL_TYPES:
                    result_score += 10
                if result_ct in (50000, 50004, 50008):
                    result_score += 5
                if result.get_acc_name():
                    result_score += 3

                if best_ct in _INTERACTIVE_CONTROL_TYPES:
                    best_score += 10
                if best_ct in (50000, 50004, 50008):
                    best_score += 5
                if best_result.get_acc_name():
                    best_score += 3

                if result_score > best_score:
                    best_result = result
            except Exception:
                pass

            child = child.get_next_sibling()

        return best_result

    def find_element_by_acc_name(self, acc_name: str) -> Optional['UIAElement']:
        """通过acc-name查找子元素"""
        walker = self._get_tree_walker()
        if not walker:
            return None

        def _search(element: UIAElement) -> Optional[UIAElement]:
            if element.get_acc_name() == acc_name:
                return element

            child = element.get_first_child()
            while child:
                result = _search(child)
                if result:
                    return result
                child = child.get_next_sibling()

            return None

        return _search(self)


def get_element_at_point(x: int, y: int) -> Optional[UIAElement]:
    """从屏幕坐标获取UIA元素"""
    _ensure_com_initialized()
    uia = _get_uia()
    if not uia:
        return None

    walker = _get_tree_walker(uia)
    if not walker:
        return None

    try:
        from ctypes import Structure, c_long
        class POINT(Structure):
            _fields_ = [("x", c_long), ("y", c_long)]

        point = POINT(x, y)
        com_element = uia.GetElementFromPoint(point)

        if com_element:
            return UIAElement(com_element, walker)
    except Exception:
        try:
            com_element = uia.GetElementFromPoint(x, y)
            if com_element:
                return UIAElement(com_element, walker)
        except Exception:
            pass

    return None


def find_deepest_interactive_at_point(x: int, y: int, max_depth: int = 10) -> Optional[UIAElement]:
    """从屏幕坐标获取最底层可交互元素"""
    element = get_element_at_point(x, y)
    if not element:
        return None
    return element.find_deepest_interactive(max_depth)


def build_selector_from_element(element: UIAElement) -> Dict[str, Any]:
    """从UIA元素构建精确选择器"""
    path = element.get_full_path()
    acc_name = element.get_acc_name()
    control_type = element.get_control_type()

    selector = {
        "key_candidates": [],
        "parent_chain": [],
        "resolved_via": "uia",
    }

    if acc_name:
        selector["key_candidates"].append({
            "property": "uia-acc-name",
            "value": acc_name,
            "match": "equals",
        })

    selector["key_candidates"].append({
        "property": "uia-control-type",
        "value": control_type,
        "match": "equals",
    })

    meaningful_nodes = []
    for node in path[:-1]:
        ct = node.get("control_type", "")
        if ct and ct not in ("Pane", "Group", "Document"):
            meaningful_nodes.append({
                "control_type": ct,
                "name": node.get("name", ""),
            })

    selector["parent_chain"] = meaningful_nodes[:5]

    return selector


def _get_element_at_point_with_walker(x: int, y: int, walker_type: str = "control") -> Optional[UIAElement]:
    """使用指定视图从屏幕坐标获取UIA元素"""
    _ensure_com_initialized()
    uia = _get_uia()
    if not uia:
        return None

    walker = _get_tree_walker(uia, walker_type)
    if not walker:
        return None

    # 优先使用 comtypes 自动生成的 tagPOINT 类型，与接口签名完全匹配
    try:
        from comtypes.gen.UIAutomationClient import tagPOINT
        point = tagPOINT(x, y)
        com_element = uia.GetElementFromPoint(point)
        if com_element:
            return UIAElement(com_element, walker)
    except Exception:
        pass

    # 回退：使用 ctypes POINT 结构体（直接传值，不要用 byref）
    try:
        from ctypes import Structure, c_long
        class POINT(Structure):
            _fields_ = [("x", c_long), ("y", c_long)]
        point = POINT(x, y)
        com_element = uia.GetElementFromPoint(point)
        if com_element:
            return UIAElement(com_element, walker)
    except Exception:
        pass

    return None


def _find_element_through_multiple_views(x: int, y: int) -> Optional[UIAElement]:
    """通过多个视图查找元素（Control View → Raw View → Content View）"""
    for walker_type in ["control", "raw", "content"]:
        element = _get_element_at_point_with_walker(x, y, walker_type)
        if not element:
            continue

        rect = element.get_bounding_rect()
        # 跳过无效 rect
        if rect[2] - rect[0] < 4 or rect[3] - rect[1] < 4:
            continue

        name = element.get_acc_name()
        cls_name = element.get_class_name()
        combined = f"{name} {cls_name}".lower()

        # 如果不是伪容器，直接返回
        if not any(p in combined for p in _PSEUDO_CONTAINER_PATTERNS):
            return element

        # 如果是伪容器，尝试查找最深层可交互元素
        deepest = element.find_deepest_interactive()
        if deepest and deepest is not element:
            deepest_rect = deepest.get_bounding_rect()
            if deepest_rect[2] - deepest_rect[0] >= 4 and deepest_rect[3] - deepest_rect[1] >= 4:
                deepest_name = deepest.get_acc_name()
                deepest_cls = deepest.get_class_name()
                deepest_combined = f"{deepest_name} {deepest_cls}".lower()
                if not any(p in deepest_combined for p in _PSEUDO_CONTAINER_PATTERNS):
                    return deepest

        # 如果当前视图没有有效元素，尝试下一个视图
        continue

    return None


def capture_element_at_point(x: int, y: int) -> Dict[str, Any]:
    """捕获屏幕坐标处的元素信息（支持多视图遍历）"""
    element = _find_element_through_multiple_views(x, y)
    if not element:
        return {
            "ok": False,
            "error": "未找到元素",
        }

    rect = element.get_bounding_rect()
    acc_name = element.get_acc_name()
    control_type = element.get_control_type()
    selector = build_selector_from_element(element)

    return {
        "ok": True,
        "bounding_rect": rect,
        "element_label": acc_name,
        "control_type": control_type,
        "selector": selector,
    }
