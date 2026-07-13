# -*- coding: utf-8 -*-
"""
系统弹窗/模态框识别与元素提取。

支持 Windows 原生对话框 (#32770)、MessageBox、文件对话框等。
输出格式与 desktop_uia_snapshot.SnapshotCaptureResult 兼容。
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_dialog_handler 仅支持 Windows")

_DIALOG_CLASS = "#32770"

_BUTTON_CLASSES = frozenset({"Button", "button"})
_STATIC_CLASSES = frozenset({"Static", "static"})
_EDIT_CLASSES = frozenset({"Edit", "edit", "RichEdit20A", "RichEdit20W", "RichEdit"})
_COMBOBOX_CLASSES = frozenset({"ComboBox", "combobox"})

_BUTTON_KEYWORDS = {
    "ok": ["确定", "OK", "是", "Yes", "确认"],
    "cancel": ["取消", "Cancel", "否", "No"],
    "yes": ["是", "Yes"],
    "no": ["否", "No"],
    "retry": ["重试", "Retry"],
    "abort": ["中止", "Abort"],
    "ignore": ["忽略", "Ignore"],
    "close": ["关闭", "Close"],
    "save": ["保存", "Save"],
    "open": ["打开", "Open"],
}


@dataclass
class DialogCaptureResult:
    ok: bool
    dialog_type: str = ""
    dialog_title: str = ""
    element_snapshot: Optional[Dict[str, Any]] = None
    bounding_rect: Optional[Tuple[int, int, int, int]] = None
    screen_center: Optional[Tuple[int, int]] = None
    element_label: str = ""
    control_type: str = ""
    buttons: List[Dict[str, Any]] = None
    static_texts: List[str] = None
    edit_fields: List[Dict[str, Any]] = None
    error_code: str = ""
    message: str = ""

    def __post_init__(self):
        if self.buttons is None:
            self.buttons = []
        if self.static_texts is None:
            self.static_texts = []
        if self.edit_fields is None:
            self.edit_fields = []


def _user32():
    import ctypes
    return ctypes.windll.user32


def _get_class_name(hwnd: int) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(int(hwnd), buf, 256)
    return (buf.value or "").strip()


def _get_window_text(hwnd: int) -> str:
    u = _user32()
    length = u.GetWindowTextLengthW(int(hwnd))
    if length <= 0:
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(length + 1)
    u.GetWindowTextW(int(hwnd), buf, length + 1)
    return (buf.value or "").strip()


def _get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    import ctypes
    from ctypes import wintypes
    rect = wintypes.RECT()
    if _user32().GetWindowRect(int(hwnd), ctypes.byref(rect)):
        w = int(rect.right) - int(rect.left)
        h = int(rect.bottom) - int(rect.top)
        if w > 0 and h > 0:
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    return None


def _enum_top_level_windows() -> List[Tuple[int, str, str]]:
    import ctypes
    from ctypes import wintypes
    u = _user32()
    result: List[Tuple[int, str, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        owner = u.GetWindow(int(hwnd), 4)
        if owner:
            return True
        cls_name = _get_class_name(int(hwnd))
        title = _get_window_text(int(hwnd))
        if cls_name or title:
            result.append((int(hwnd), title, cls_name))
        return True

    u.EnumWindows(_cb, 0)
    return result


def _get_direct_children(hwnd: int) -> List[Tuple[int, str, str, str]]:
    import ctypes
    from ctypes import wintypes
    u = _user32()
    children: List[Tuple[int, str, str, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(child_hwnd, _lparam):
        child = int(child_hwnd)
        if not u.IsWindowVisible(child):
            return True
        cls_name = _get_class_name(child)
        text = _get_window_text(child)
        children.append((child, text, cls_name, ""))
        return True

    u.EnumChildWindows(int(hwnd), _cb, 0)
    return children


def _classify_dialog(title: str, class_name: str, children: list) -> str:
    if class_name != _DIALOG_CLASS:
        return "generic_window"
    title_lower = title.lower() if title else ""
    if "打开" in title or "open" in title_lower:
        return "file_open"
    if "另存为" in title or "save as" in title_lower or "保存" in title:
        return "file_save"
    if "打印" in title or "print" in title_lower:
        return "print"
    if "颜色" in title or "color" in title_lower:
        return "color"
    if "字体" in title or "font" in title_lower:
        return "font"
    if "属性" in title or "properties" in title_lower:
        return "properties"
    for _h, text, cls_name, _ in children:
        if cls_name in _EDIT_CLASSES and text:
            return "input_dialog"
    for _h, text, cls_name, _ in children:
        if cls_name in _STATIC_CLASSES and ("错误" in text or "error" in text.lower()):
            return "error"
        if cls_name in _STATIC_CLASSES and ("警告" in text or "warning" in text.lower()):
            return "warning"
        if cls_name in _STATIC_CLASSES and ("提示" in text or "info" in text.lower()):
            return "info"
    has_buttons = any(cls_name in _BUTTON_CLASSES for _h, _t, cls_name, _ in children)
    static_count = sum(1 for _h, _t, cls_name, _ in children if cls_name in _STATIC_CLASSES)
    if has_buttons and static_count > 0:
        return "message_box"
    return "dialog"


def detect_system_dialog() -> Optional[DialogCaptureResult]:
    for hwnd, title, cls_name in _enum_top_level_windows():
        if cls_name == _DIALOG_CLASS:
            children = _get_direct_children(hwnd)
            dlg_type = _classify_dialog(title, cls_name, children)
            buttons: List[Dict[str, Any]] = []
            static_texts: List[str] = []
            edit_fields: List[Dict[str, Any]] = []
            for ch, text, ch_cls, _ in children:
                rect = _get_window_rect(ch)
                if ch_cls in _BUTTON_CLASSES and text:
                    buttons.append({
                        "hwnd": ch,
                        "text": text,
                        "button_type": _guess_button_type(text),
                        "rect": rect,
                    })
                elif ch_cls in _STATIC_CLASSES and text:
                    text_clean = text.strip()
                    if text_clean and len(text_clean) > 1:
                        static_texts.append(text_clean)
                elif ch_cls in _EDIT_CLASSES:
                    edit_fields.append({
                        "hwnd": ch,
                        "text": text,
                        "rect": rect,
                    })
            rect = _get_window_rect(hwnd)
            center = None
            if rect:
                center = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
            return DialogCaptureResult(
                ok=True,
                dialog_type=dlg_type,
                dialog_title=title,
                bounding_rect=rect,
                screen_center=center,
                element_label=title or dlg_type,
                control_type="Dialog",
                buttons=buttons,
                static_texts=static_texts,
                edit_fields=edit_fields,
            )
    return None


def get_dialog_info() -> Optional[Dict[str, Any]]:
    result = detect_system_dialog()
    if not result or not result.ok:
        return None
    return {
        "dialog_type": result.dialog_type,
        "dialog_title": result.dialog_title,
        "bounding_rect": result.bounding_rect,
        "buttons": [
            {"text": b["text"], "button_type": b["button_type"], "rect": b["rect"]}
            for b in result.buttons
        ],
        "static_texts": result.static_texts,
        "edit_fields": [
            {"text": e["text"], "rect": e["rect"]}
            for e in result.edit_fields
        ],
    }


def _guess_button_type(text: str) -> str:
    text_lower = text.strip().lower()
    for btn_type, keywords in _BUTTON_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return btn_type
    return "custom"


def build_dialog_element_tree(
    dialog_result: DialogCaptureResult,
) -> Dict[str, Any]:
    parent_chain: List[Dict[str, Any]] = []
    if dialog_result.dialog_title:
        parent_chain.append({
            "control_type": "Dialog",
            "class_name": _DIALOG_CLASS,
            "name": dialog_result.dialog_title,
            "automation_id": "",
            "dialog_type": dialog_result.dialog_type,
        })
    else:
        parent_chain.append({
            "control_type": "Dialog",
            "class_name": _DIALOG_CLASS,
            "name": dialog_result.dialog_type,
            "automation_id": "",
            "dialog_type": dialog_result.dialog_type,
        })

    key_candidates: List[Dict[str, str]] = []
    if dialog_result.dialog_title:
        key_candidates.append({
            "property": "name",
            "value": dialog_result.dialog_title,
            "match": "contains",
        })
    key_candidates.append({
        "property": "class_name",
        "value": _DIALOG_CLASS,
        "match": "equals",
    })

    selector = {
        "anchor_props": "Dialog",
        "key_candidates": key_candidates,
        "parent_chain": parent_chain,
        "resolved_via": "dialog",
        "dialog_info": {
            "dialog_type": dialog_result.dialog_type,
            "buttons": [
                {"text": b["text"], "button_type": b["button_type"]}
                for b in dialog_result.buttons
            ],
            "static_texts": dialog_result.static_texts,
        },
    }

    if dialog_result.bounding_rect:
        selector["window_bounds"] = dialog_result.bounding_rect

    return {"selector": selector}


def capture_dialog_element_at_point(
    x: int, y: int,
) -> Optional[DialogCaptureResult]:
    dialog = detect_system_dialog()
    if not dialog or not dialog.ok:
        return None
    if dialog.bounding_rect:
        l, t, r, b = dialog.bounding_rect
        if not (l <= int(x) <= r and t <= int(y) <= b):
            return None
    element_snapshot = build_dialog_element_tree(dialog)
    dialog.element_snapshot = element_snapshot
    return dialog


def try_click_dialog_button(
    button_text: str,
    button_type: str = "",
    *,
    timeout: float = 3.0,
) -> bool:
    dialog = detect_system_dialog()
    if not dialog or not dialog.ok:
        return False
    for btn in dialog.buttons:
        if button_text.lower() in (btn.get("text") or "").lower():
            if button_type and btn.get("button_type") != button_type:
                continue
            rect = btn.get("rect")
            if rect:
                cx = (rect[0] + rect[2]) // 2
                cy = (rect[1] + rect[3]) // 2
                try:
                    from desktop_input import message_click_at_screen
                    message_click_at_screen(cx, cy)
                    time.sleep(0.2)
                    return True
                except Exception:
                    pass
    return False
