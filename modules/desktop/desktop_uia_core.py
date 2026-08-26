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
    50002,  # CheckBox
    50003,  # ComboBox
    50004,  # Edit
    50005,  # Hyperlink
    50006,  # Image (often clickable icons)
    50007,  # List
    50008,  # ListItem
    50010,  # MenuBar
    50011,  # MenuItem
    50013,  # RadioButton
    50018,  # Tab
    50019,  # TabItem
    50020,  # Text (links / labels often hit)
    50021,  # ToolBar
    50023,  # Tree
    50024,  # TreeItem
    50025,  # Custom (many Qt/CEF expose as Custom)
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
    "chrome_renderwidgethosthwnd",
    "chrome_widgetwin",  # 仅外壳；内部 Text/Button 类名通常不含此串
    "renderwidget",
    "legacy window",
    "corewindow",
    "webview2",
    "cefclient",
    "cefrender",
)

# CUIAutomation / CUIAutomation8（ProgID「UIAutomationCore.UIAutomation」在多数机器上无效）
_CLSID_CUIAUTOMATION = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_CLSID_CUIAUTOMATION8 = "{e22ad333-b25f-460c-83d0-0581107395c9}"
_uia_singleton: Any = None
_uia_init_failed = False


def _ensure_com_initialized():
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass


def _get_uia() -> Any:
    """
    获取 IUIAutomation 实例。

    历史 bug：CreateObject(\"UIAutomationCore.UIAutomation\") 常报「无效的类字符串」，
    导致 _get_uia() 恒为 None → 应用内元素全部识别失败；桌面图标因走 Win32 ListView 不受影响。
    """
    global _uia_singleton, _uia_init_failed
    if _uia_singleton is not None:
        return _uia_singleton
    if _uia_init_failed:
        return None
    _ensure_com_initialized()
    try:
        from comtypes import client
        from comtypes.gen.UIAutomationClient import IUIAutomation
    except Exception:
        _uia_init_failed = True
        return None

    errors: list = []
    for clsid in (_CLSID_CUIAUTOMATION8, _CLSID_CUIAUTOMATION):
        try:
            obj = client.CreateObject(clsid, interface=IUIAutomation)
            if obj is not None:
                _uia_singleton = obj
                return _uia_singleton
        except Exception as exc:
            errors.append(f"{clsid}:{exc}")
    # 少数环境仍注册了 ProgID
    for progid in ("UIAutomationClient.CUIAutomation8", "UIAutomationClient.CUIAutomation"):
        try:
            obj = client.CreateObject(progid)
            if obj is not None:
                _uia_singleton = obj
                return _uia_singleton
        except Exception as exc:
            errors.append(f"{progid}:{exc}")
    _uia_init_failed = True
    return None


def _element_from_point(uia: Any, x: int, y: int) -> Any:
    """调用 IUIAutomation::ElementFromPoint（正确方法名，不是 GetElementFromPoint）。"""
    if uia is None:
        return None
    try:
        from comtypes.gen.UIAutomationClient import tagPOINT

        pt = tagPOINT(int(x), int(y))
        if hasattr(uia, "ElementFromPoint"):
            return uia.ElementFromPoint(pt)
        if hasattr(uia, "GetElementFromPoint"):
            return uia.GetElementFromPoint(pt)
    except Exception:
        pass
    try:
        from ctypes import Structure, c_long

        class POINT(Structure):
            _fields_ = [("x", c_long), ("y", c_long)]

        pt = POINT(int(x), int(y))
        if hasattr(uia, "ElementFromPoint"):
            return uia.ElementFromPoint(pt)
        if hasattr(uia, "GetElementFromPoint"):
            return uia.GetElementFromPoint(pt)
    except Exception:
        pass
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

    def get_children(self) -> List['UIAElement']:
        """获取所有直接子元素。"""
        children: List[UIAElement] = []
        walker = self._get_tree_walker()
        if not walker:
            return children
        try:
            child = walker.GetFirstChildElement(self._element)
            while child:
                children.append(UIAElement(child, walker))
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break
        except Exception:
            pass
        return children

    def _get_automation_id(self) -> str:
        """获取 AutomationId 属性。"""
        try:
            return str(self._element.CurrentAutomationId or "").strip()
        except Exception:
            return ""

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
        """深度遍历找到最底层可交互元素（会进入容器，不跳过 Pane/Group）。"""
        if depth >= max_depth:
            return self

        if self.is_interactive() and not self.is_container():
            return self

        child = self.get_first_child()
        best_result = self

        while child:
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
                # 更小的控件优先（贴近点选目标）
                rr = result.get_bounding_rect()
                br = best_result.get_bounding_rect()
                ra = max(1, (rr[2] - rr[0]) * (rr[3] - rr[1]))
                ba = max(1, (br[2] - br[0]) * (br[3] - br[1]))
                if ra < ba:
                    result_score += 4

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

    def find_smallest_at_point(self, x: int, y: int, max_depth: int = 14) -> 'UIAElement':
        """
        RPA 风格：在命中节点子树中找包含 (x,y) 的最小元素。
        比单纯 ElementFromPoint 更能落到按钮/输入框而非整窗 Pane。
        """
        best = self
        br0 = self.get_bounding_rect()
        best_area = max(1, (br0[2] - br0[0]) * (br0[3] - br0[1]))
        best_interactive = self.is_interactive() and not self.is_container()

        def _walk(el: "UIAElement", depth: int) -> None:
            nonlocal best, best_area, best_interactive
            if depth > max_depth:
                return
            child = el.get_first_child()
            while child:
                try:
                    r = child.get_bounding_rect()
                    if r[2] - r[0] < 2 or r[3] - r[1] < 2:
                        child = child.get_next_sibling()
                        continue
                    if not (r[0] <= int(x) <= r[2] and r[1] <= int(y) <= r[3]):
                        child = child.get_next_sibling()
                        continue
                    area = max(1, (r[2] - r[0]) * (r[3] - r[1]))
                    interactive = child.is_interactive() and not child.is_container()
                    take = False
                    if area < best_area:
                        take = True
                    elif area <= best_area * 1.15 and interactive and not best_interactive:
                        take = True
                    if take and not child.is_pseudo_container():
                        best = child
                        best_area = area
                        best_interactive = interactive
                    _walk(child, depth + 1)
                except Exception:
                    pass
                child = child.get_next_sibling()

        try:
            _walk(self, 0)
        except Exception:
            pass
        return best

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

    com_element = _element_from_point(uia, int(x), int(y))
    if com_element:
        return UIAElement(com_element, walker)
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

    com_element = _element_from_point(uia, int(x), int(y))
    if com_element:
        return UIAElement(com_element, walker)
    return None


def _find_element_through_multiple_views(x: int, y: int) -> Optional[UIAElement]:
    """通过多个视图查找元素，并钻到包含坐标的最小控件（RPA 常见做法）。"""
    for walker_type in ["control", "raw", "content"]:
        element = _get_element_at_point_with_walker(x, y, walker_type)
        if not element:
            continue

        rect = element.get_bounding_rect()
        if rect[2] - rect[0] < 4 or rect[3] - rect[1] < 4:
            continue

        name = element.get_acc_name()
        cls_name = element.get_class_name()
        combined = f"{name} {cls_name}".lower()

        try:
            refined = element.find_smallest_at_point(int(x), int(y))
            if refined:
                element = refined
                rect = element.get_bounding_rect()
                name = element.get_acc_name()
                cls_name = element.get_class_name()
                combined = f"{name} {cls_name}".lower()
        except Exception:
            pass

        if not any(p in combined for p in _PSEUDO_CONTAINER_PATTERNS):
            try:
                deepest = element.find_deepest_interactive(max_depth=12)
                if deepest and deepest is not element:
                    dr = deepest.get_bounding_rect()
                    if (
                        dr[2] - dr[0] >= 4
                        and dr[3] - dr[1] >= 4
                        and dr[0] <= int(x) <= dr[2]
                        and dr[1] <= int(y) <= dr[3]
                    ):
                        dcomb = f"{deepest.get_acc_name()} {deepest.get_class_name()}".lower()
                        if not any(p in dcomb for p in _PSEUDO_CONTAINER_PATTERNS):
                            da = (dr[2] - dr[0]) * (dr[3] - dr[1])
                            ea = max(1, (rect[2] - rect[0]) * (rect[3] - rect[1]))
                            if da > 0 and da <= ea:
                                return deepest
            except Exception:
                pass
            return element

        # 伪容器：尝试 deepest / smallest
        try:
            deepest = element.find_deepest_interactive()
            if deepest and deepest is not element:
                deepest_rect = deepest.get_bounding_rect()
                if deepest_rect[2] - deepest_rect[0] >= 4 and deepest_rect[3] - deepest_rect[1] >= 4:
                    deepest_combined = (
                        f"{deepest.get_acc_name()} {deepest.get_class_name()}".lower()
                    )
                    if not any(p in deepest_combined for p in _PSEUDO_CONTAINER_PATTERNS):
                        return deepest
        except Exception:
            pass
        continue

    return None


def wake_accessibility_around_point(x: int, y: int, *, max_children: int = 80) -> bool:
    """
    对已运行的 Chromium/WebView/Electron 进程：主动查询 UIA 属性以唤醒无障碍树。

    Chromium 常在「检测到无障碍客户端」后才暴露内部 Document/控件；
    用户正常点击打开应用后，本函数让捕获器像 Narrator 一样点亮树，无需重启进程。
    """
    _ensure_com_initialized()
    uia = _get_uia()
    if not uia:
        return False

    warmed = False
    for walker_type in ("raw", "control", "content"):
        el = _get_element_at_point_with_walker(int(x), int(y), walker_type)
        if not el:
            continue
        try:
            # 读取关键属性即可触发 Chromium enable accessibility
            _ = el.get_acc_name()
            _ = el.get_control_type()
            _ = el.get_class_name()
            _ = el.get_bounding_rect()
            warmed = True
        except Exception:
            pass

        # 浅层遍历子树，迫使渲染进程构建 AX tree
        n = 0
        try:
            child = el.get_first_child()
            while child is not None and n < max_children:
                try:
                    _ = child.get_acc_name()
                    _ = child.get_control_type()
                    _ = child.get_bounding_rect()
                    warmed = True
                    # 再下一层一点
                    grand = child.get_first_child()
                    if grand is not None:
                        _ = grand.get_acc_name()
                        _ = grand.get_bounding_rect()
                        n += 1
                except Exception:
                    pass
                n += 1
                try:
                    child = child.get_next_sibling()
                except Exception:
                    break
        except Exception:
            pass

    return warmed


def capture_element_at_point(x: int, y: int) -> Dict[str, Any]:
    """捕获屏幕坐标处的元素信息（支持多视图遍历；伪容器时先唤醒无障碍再试）。"""
    element = _find_element_through_multiple_views(x, y)

    # 命中渲染壳：先唤醒再捕一次（已打开的 Electron/WebView2 常见）
    if element is None or element.is_pseudo_container():
        try:
            wake_accessibility_around_point(int(x), int(y))
        except Exception:
            pass
        element = _find_element_through_multiple_views(x, y)

    if not element:
        return {
            "ok": False,
            "error": "未找到元素",
        }

    # 仍落在伪容器上：视为未命中内部元素，交给 Win32/OCR/视觉兜底
    if element.is_pseudo_container():
        # 再试 deepest interactive（唤醒后可能已有子节点）
        try:
            deepest = element.find_deepest_interactive(max_depth=12)
            if deepest and deepest is not element and not deepest.is_pseudo_container():
                element = deepest
            else:
                return {
                    "ok": False,
                    "error": "fake_container",
                    "bounding_rect": element.get_bounding_rect(),
                    "element_label": element.get_acc_name(),
                    "control_type": element.get_control_type(),
                    "class_name": element.get_class_name(),
                }
        except Exception:
            return {
                "ok": False,
                "error": "fake_container",
                "bounding_rect": element.get_bounding_rect(),
                "element_label": element.get_acc_name(),
                "control_type": element.get_control_type(),
                "class_name": element.get_class_name(),
            }

    rect = element.get_bounding_rect()
    acc_name = element.get_acc_name()
    control_type = element.get_control_type()
    class_name = element.get_class_name()
    selector = build_selector_from_element(element)

    return {
        "ok": True,
        "bounding_rect": rect,
        "element_label": acc_name,
        "control_type": control_type,
        "class_name": class_name,
        "selector": selector,
    }


def dump_foreground_uia_tree(max_depth: int = 4, max_nodes: int = 120) -> List[Dict[str, Any]]:
    """导出前台窗口 UIA 树，供 inspect / 调试。"""
    _ensure_com_initialized()
    uia = _get_uia()
    if not uia:
        return []
    walker = _get_tree_walker(uia, "control")
    if not walker:
        return []

    try:
        import ctypes

        hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        if not hwnd:
            root_el = UIAElement(uia.GetRootElement(), walker)
        else:
            # ElementFromHandle 在 comtypes UIA 上可用
            try:
                com_el = uia.ElementFromHandle(hwnd)
            except Exception:
                com_el = uia.GetRootElement()
            root_el = UIAElement(com_el, walker)
    except Exception:
        return []

    nodes: List[Dict[str, Any]] = []

    def walk(el: UIAElement, depth: int) -> None:
        if len(nodes) >= max_nodes or depth > max_depth:
            return
        rect = el.get_bounding_rect()
        nodes.append(
            {
                "name": el.get_acc_name(),
                "control_type": el.get_control_type(),
                "class_name": el.get_class_name(),
                "rect": rect,
                "depth": depth,
                "interactive": el.is_interactive(),
            }
        )
        if depth >= max_depth:
            return
        child = el.get_first_child()
        while child and len(nodes) < max_nodes:
            walk(child, depth + 1)
            child = child.get_next_sibling()

    walk(root_el, 0)
    return nodes


def find_element_by_acc_name(acc_name: str) -> Dict[str, Any]:
    """从桌面根按 accessibility name 查找元素（供 hybrid locator 回放调用）。"""
    name = (acc_name or "").strip()
    if not name:
        return {"ok": False, "error": "empty_acc_name"}

    _ensure_com_initialized()
    uia = _get_uia()
    if not uia:
        return {"ok": False, "error": "no_uia"}

    walker = _get_tree_walker(uia)
    if not walker:
        return {"ok": False, "error": "no_walker"}

    try:
        root = UIAElement(uia.GetRootElement(), walker)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    found = root.find_element_by_acc_name(name)
    if not found:
        return {"ok": False, "error": "not_found"}

    rect = found.get_bounding_rect()
    if rect[2] - rect[0] < 1 or rect[3] - rect[1] < 1:
        return {"ok": False, "error": "invalid_rect"}

    return {
        "ok": True,
        "bounding_rect": rect,
        "element_label": found.get_acc_name(),
        "control_type": found.get_control_type(),
        "class_name": found.get_class_name(),
        "selector": build_selector_from_element(found),
    }


def find_elements_by_description(
    description: str,
    root: Optional[Any] = None,
    max_depth: int = 12,
    max_results: int = 20,
) -> List[Dict[str, Any]]:
    """按自然语言描述查找 UIA 元素（支持模糊匹配 + 深度递归）。

    供 UnifiedElementLocator 使用。返回列表，每项包含:
        x, y, width, height, score, strategy, name, control_type, automation_id
    """
    name = (description or "").strip()
    if not name:
        return []

    _ensure_com_initialized()
    uia = _get_uia()
    if not uia:
        return []

    walker = _get_tree_walker(uia)
    if not walker:
        return []

    if root is None:
        try:
            root = UIAElement(uia.GetRootElement(), walker)
        except Exception:
            return []

    keywords = _extract_search_keywords(name)
    results: List[Dict[str, Any]] = []
    _collect_elements_deep(root, name, keywords, results, depth=0, max_depth=max_depth, max_results=max_results)
    results.sort(key=lambda r: r.get("score", 0), reverse=True)
    return results


def _extract_search_keywords(description: str) -> List[str]:
    """从描述中提取搜索关键词。"""
    import re
    text = description.strip()
    expanded = [text]
    mapping = {
        "登录": ["登录", "登陆", "login", "sign in", "submit", "确定"],
        "确定": ["确定", "确认", "ok", "yes"],
        "取消": ["取消", "cancel", "close", "关闭"],
        "搜索": ["搜索", "查找", "search", "find"],
        "保存": ["保存", "save"],
        "删除": ["删除", "delete", "remove"],
        "添加": ["添加", "新增", "add", "new"],
        "发送": ["发送", "提交", "send", "submit"],
        "下载": ["下载", "download", "export"],
        "上传": ["上传", "upload", "import"],
        "编辑": ["编辑", "修改", "edit"],
        "返回": ["返回", "后退", "back"],
        "下一步": ["下一步", "next"],
        "开始": ["开始", "start", "run"],
        "停止": ["停止", "stop"],
        "刷新": ["刷新", "refresh", "reload"],
        "关闭": ["关闭", "close", "exit"],
    }
    for key, aliases in mapping.items():
        if key in text.lower():
            expanded.extend(aliases)
            break
    expanded.append(text.lower())
    tokens = re.split(r"[\s,，。.!！?？;；:：、\(\)（）\[\]【】]+", text)
    for t in tokens:
        t = t.strip()
        if len(t) >= 2 and t.lower() not in [e.lower() for e in expanded]:
            expanded.append(t)
    return list(dict.fromkeys([e for e in expanded if e]))


def _collect_elements_deep(
    element: "UIAElement",
    description: str,
    keywords: List[str],
    results: List[Dict[str, Any]],
    depth: int = 0,
    max_depth: int = 12,
    max_results: int = 20,
):
    """深度递归收集匹配的元素。"""
    if len(results) >= max_results:
        return
    if depth > max_depth:
        return
    try:
        acc_name = element.get_acc_name() or ""
        class_name = element.get_class_name() or ""
        ctrl_type = element.get_control_type() or ""
        automation_id = ""
        try:
            automation_id = element._get_automation_id() or ""
        except Exception:
            pass
        rect = element.get_bounding_rect()
        if rect:
            x1, y1, x2, y2 = rect
            w = max(1, int(x2 - x1))
            h = max(1, int(y2 - y1))
            text_lower = f"{acc_name} {class_name} {automation_id}".lower()
            desc_lower = description.lower()
            score = 0.0
            strategy = "fuzzy"
            if acc_name and acc_name.lower() == desc_lower:
                score = 1.0
                strategy = "exact"
            elif acc_name and desc_lower in acc_name.lower():
                score = 0.9
                strategy = "exact"
            elif acc_name and acc_name.lower() in desc_lower:
                score = 0.75
                strategy = "exact"
            else:
                keyword_hits = 0
                for kw in keywords:
                    if kw and kw.lower() in text_lower:
                        keyword_hits += 1
                if keyword_hits > 0:
                    score = min(0.85, 0.3 + 0.15 * keyword_hits)
                    strategy = "keyword"
                elif acc_name and len(acc_name) >= 2:
                    score = 0.3
                    strategy = "name_only"
                elif automation_id and len(automation_id) >= 2:
                    score = 0.25
                    strategy = "automation_id"
            if score >= 0.25:
                results.append({
                    "x": int(x1),
                    "y": int(y1),
                    "width": w,
                    "height": h,
                    "score": score,
                    "strategy": strategy,
                    "name": acc_name or class_name,
                    "control_type": ctrl_type,
                    "automation_id": automation_id,
                    "depth": depth,
                })
    except Exception:
        pass
    try:
        children = element.get_children() or []
        for child in children:
            _collect_elements_deep(child, description, keywords, results, depth + 1, max_depth, max_results)
    except Exception:
        pass
