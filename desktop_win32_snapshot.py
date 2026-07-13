# -*- coding: utf-8 -*-
"""
Win32 API 控件枚举与元素捕获：当 UIA 无法识别元素时的兜底方案。

通过 WindowFromPoint / EnumChildWindows / GetWindowText 获取原生窗口控件信息，
输出格式与 desktop_uia_snapshot.SnapshotCaptureResult 兼容。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_win32_snapshot 仅支持 Windows")

_VOLATILE_CLASS_NAMES = frozenset({
    "tooltips_class32", "msctls_statusbar32", "scrollbar",
    "msctls_trackbar32", "toolbarwindow32",
})


@dataclass
class Win32CaptureResult:
    ok: bool
    element_snapshot: Optional[Dict[str, Any]] = None
    error_code: str = ""
    message: str = ""
    screen_center: Optional[Tuple[int, int]] = None
    bounding_rect: Optional[Tuple[int, int, int, int]] = None
    element_label: str = ""
    control_type: str = ""
    window_title: str = ""
    process_name: str = ""


def _ctypes_user32():
    import ctypes
    return ctypes.windll.user32


def _ctypes_kernel32():
    import ctypes
    return ctypes.windll.kernel32


def _ctypes_psapi():
    import ctypes
    try:
        return ctypes.windll.psapi
    except Exception:
        return None


def window_from_point(x: int, y: int) -> Optional[int]:
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT(int(x), int(y))
    hwnd = int(_ctypes_user32().WindowFromPoint(pt) or 0)
    return hwnd if hwnd else None


def get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    if _ctypes_user32().GetWindowRect(int(hwnd), ctypes.byref(rect)):
        w = int(rect.right) - int(rect.left)
        h = int(rect.bottom) - int(rect.top)
        if w > 0 and h > 0:
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    return None


def get_window_text(hwnd: int) -> str:
    u = _ctypes_user32()
    length = u.GetWindowTextLengthW(int(hwnd))
    if length <= 0:
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(length + 1)
    u.GetWindowTextW(int(hwnd), buf, length + 1)
    return (buf.value or "").strip()


def get_window_class_name(hwnd: int) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    _ctypes_user32().GetClassNameW(int(hwnd), buf, 256)
    return (buf.value or "").strip()


def get_top_level_window(hwnd: int) -> int:
    u = _ctypes_user32()
    try:
        root = int(u.GetAncestor(int(hwnd), 2) or 0)
        if root:
            return root
    except Exception:
        pass
    cur = int(hwnd)
    for _ in range(64):
        parent = int(u.GetParent(cur) or 0)
        if not parent or parent == cur:
            return cur
        cur = parent
    return int(hwnd)


def get_process_name_from_hwnd(hwnd: int) -> str:
    psapi = _ctypes_psapi()
    if not psapi:
        return ""
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD()
    _ctypes_user32().GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    if not pid.value:
        return ""
    process_handle = _ctypes_kernel32().OpenProcess(0x0400 | 0x0010, False, pid.value)
    if not process_handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if psapi.GetModuleBaseNameW(process_handle, None, buf, size):
            return (buf.value or "").strip()
    finally:
        _ctypes_kernel32().CloseHandle(process_handle)
    return ""


def enumerate_child_windows(
    hwnd: int, max_depth: int = 3
) -> List[Dict[str, Any]]:
    children: List[Dict[str, Any]] = []

    def _enum(parent: int, depth: int) -> None:
        if depth > max_depth:
            return
        u = _ctypes_user32()
        import ctypes
        from ctypes import wintypes

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(child_hwnd, _lparam):
            child = int(child_hwnd)
            if not u.IsWindowVisible(child):
                return True
            rect = get_window_rect(child)
            if not rect:
                return True
            cls = get_window_class_name(child)
            if cls.lower() in _VOLATILE_CLASS_NAMES:
                return True
            text = get_window_text(child)
            node = {
                "hwnd": child,
                "control_type": _control_type_from_class(cls),
                "class_name": cls,
                "name": text,
                "automation_id": "",
                "bounding_rect": rect,
                "center": (
                    (rect[0] + rect[2]) // 2,
                    (rect[1] + rect[3]) // 2,
                ),
                "children": [],
            }
            children.append(node)
            sub = enumerate_child_windows(child, max_depth=max_depth)
            if sub:
                node["children"] = sub
            return True

        try:
            u.EnumChildWindows(int(parent), _cb, 0)
        except Exception:
            pass

    _enum(int(hwnd), 0)
    return children


def _control_type_from_class(class_name: str) -> str:
    cls = (class_name or "").strip().lower()
    mapping = {
        "button": "Button",
        "edit": "Edit",
        "static": "Text",
        "combobox": "ComboBox",
        "listbox": "ListBox",
        "listview": "ListView",
        "syslistview32": "ListItem",
        "treeview": "TreeView",
        "tabcontrol": "TabControl",
        "msctls_progress32": "ProgressBar",
        "scrollbar": "ScrollBar",
        "richedit": "Edit",
        "richedit20a": "Edit",
        "richedit20w": "Edit",
    }
    for key, value in mapping.items():
        if key in cls:
            return value
    return "Control"


def build_win32_element_tree(hwnd: int, max_depth: int = 3) -> List[Dict[str, Any]]:
    cls = get_window_class_name(hwnd)
    text = get_window_text(hwnd)
    rect = get_window_rect(hwnd)
    center = None
    if rect:
        center = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
    ct = _control_type_from_class(cls)
    title = get_window_text(get_top_level_window(hwnd))
    proc = get_process_name_from_hwnd(hwnd)

    root = {
        "control_type": ct,
        "class_name": cls,
        "name": text,
        "automation_id": "",
        "bounding_rect": rect,
        "center": center,
        "window_title": title,
        "process_name": proc,
        "children": [],
    }
    root["children"] = enumerate_child_windows(hwnd, max_depth=max_depth)
    return [root]


def get_parent_window_rect(x: int, y: int) -> Optional[Tuple[int, int, int, int]]:
    hwnd = window_from_point(int(x), int(y))
    if not hwnd:
        return None
    top = get_top_level_window(hwnd)
    return get_window_rect(top)


def capture_win32_element_at_point(
    x: int, y: int, *, max_depth: int = 3
) -> Win32CaptureResult:
    hwnd = window_from_point(int(x), int(y))
    if not hwnd:
        return Win32CaptureResult(
            ok=False,
            error_code="no_window",
            message="Win32 WindowFromPoint 未命中任何窗口",
        )

    tree = build_win32_element_tree(hwnd, max_depth=max_depth)
    if not tree:
        return Win32CaptureResult(
            ok=False,
            error_code="no_element",
            message="Win32 控件树构建失败",
        )

    root = tree[0]
    center = root.get("center")
    bounding_rect = root.get("bounding_rect")
    element_label = root.get("name") or ""
    control_type = root.get("control_type") or "Control"
    window_title = root.get("window_title") or ""
    process_name = root.get("process_name") or ""

    if center:
        cx, cy = center
    else:
        cx, cy = int(x), int(y)

    key_candidates: List[Dict[str, str]] = []
    if element_label:
        key_candidates.append({
            "property": "name",
            "value": element_label,
            "match": "equals",
        })
    cls = root.get("class_name") or ""
    if cls and not element_label:
        key_candidates.append({
            "property": "class_name",
            "value": cls,
            "match": "equals",
        })

    parent_chain: List[Dict[str, Any]] = []
    top_hwnd = get_top_level_window(hwnd)
    top_cls = get_window_class_name(top_hwnd)
    top_title = get_window_text(top_hwnd)
    top_rect = get_window_rect(top_hwnd)

    if top_title or top_cls:
        parent_chain.append({
            "control_type": "Window",
            "class_name": top_cls,
            "name": top_title,
            "automation_id": "",
            "bounding_rect": top_rect,
            "process_name": get_process_name_from_hwnd(top_hwnd),
        })

    if hwnd != top_hwnd and (element_label or cls):
        rect = get_window_rect(hwnd)
        parent_chain.append({
            "control_type": control_type,
            "class_name": cls,
            "name": element_label,
            "automation_id": "",
            "bounding_rect": rect,
        })

    if not parent_chain:
        parent_chain.append({
            "control_type": control_type,
            "class_name": cls,
            "name": element_label,
            "automation_id": "",
        })

    selector = {
        "anchor_props": control_type,
        "key_candidates": key_candidates,
        "parent_chain": parent_chain,
        "resolved_via": "win32",
    }

    if top_rect:
        selector["window_bounds"] = top_rect

    element_snapshot = {"selector": selector}

    return Win32CaptureResult(
        ok=True,
        element_snapshot=element_snapshot,
        screen_center=(cx, cy),
        bounding_rect=bounding_rect,
        element_label=element_label,
        control_type=control_type,
        window_title=window_title,
        process_name=process_name,
    )
