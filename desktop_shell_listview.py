# -*- coding: utf-8 -*-
"""
桌面 SysListView32 后台消息点击：不移动物理光标、不受前台窗口遮挡。

适用于桌面图标（ListItem）类步骤；通过 ListView 消息直接双击图标。
Win10/11 上 ListView 项文本常为空，需配合 UIA/视觉坐标走坐标兜底。
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

if sys.platform != "win32":
    raise RuntimeError("desktop_shell_listview 仅支持 Windows")

LVM_GETITEMCOUNT = 0x1004
LVM_GETITEMTEXTW = 0x1073
LVM_GETITEMRECT = 0x100E
LVIR_BOUNDS = 0
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002


def shell_message_enabled() -> bool:
    raw = (os.environ.get("DESKTOP_SHELL_MESSAGE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass
class ShellIconTarget:
    listview_hwnd: int
    index: int
    icon_name: str
    client_x: int
    client_y: int
    screen_x: int
    screen_y: int


def _user32():
    import ctypes

    return ctypes.windll.user32


def _match_icon_name(actual: str, expected: str) -> bool:
    a = (actual or "").strip()
    e = (expected or "").strip()
    if not a or not e:
        return False
    if a == e:
        return True
    al, el = a.lower(), e.lower()
    if al == el:
        return True
    if el in al or al in el:
        return True
    return False


def icon_name_from_step(step: dict) -> str:
    from desktop_hybrid_locator import element_snapshot_for_step, _effect_keyword_from_step

    kw = _effect_keyword_from_step(step)
    if kw:
        return kw
    snap = element_snapshot_for_step(step)
    if not snap:
        return ""
    sel = snap.get("selector") or snap
    for cand in sel.get("key_candidates") or []:
        prop = (cand.get("property") or "").strip().lower()
        if prop in ("uia-name", "name"):
            val = (cand.get("value") or "").strip()
            if val and val not in ("桌面", "Desktop", "桌面 1"):
                return val
    chain = sel.get("parent_chain") or []
    if chain:
        nm = (chain[-1].get("name") or "").strip()
        if nm:
            return nm
    return ""


def is_desktop_listitem_step(step: dict) -> bool:
    from desktop_hybrid_locator import element_snapshot_for_step

    if not icon_name_from_step(step):
        return False
    snap = element_snapshot_for_step(step)
    if snap:
        sel = snap.get("selector") or snap
        anchor = (sel.get("anchor_props") or "").lower()
        if "listitem" in anchor:
            return True
        for node in sel.get("parent_chain") or []:
            if (node.get("class_name") or "").lower() == "syslistview32":
                return True
    spec = step.get("desktop_spec")
    if isinstance(spec, dict) and (
        spec.get("hybrid_capture") or spec.get("desktop_shell")
    ):
        return True
    if isinstance(spec, str) and spec.strip():
        try:
            import json

            sd = json.loads(spec)
            if sd.get("hybrid_capture") or sd.get("desktop_shell"):
                return True
        except Exception:
            pass
    desc = (step.get("description") or "").lower()
    return "listitem" in desc or "控制面板" in desc or "桌面" in desc


def _find_listview_in_parent(parent_hwnd: int) -> int:
    user32 = _user32()
    shell = int(user32.FindWindowExW(int(parent_hwnd), 0, "SHELLDLL_DefView", None) or 0)
    if not shell:
        return 0
    lv = int(user32.FindWindowExW(shell, 0, "SysListView32", None) or 0)
    return lv


def get_desktop_listview_hwnd() -> int:
    """查找桌面 SysListView32（Progman 或可见 WorkerW）。"""
    user32 = _user32()
    progman = int(user32.FindWindowW("Progman", None) or 0)
    if progman:
        lv = _find_listview_in_parent(progman)
        if lv:
            return lv

    found: List[int] = []

    import ctypes
    from ctypes import wintypes

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @WNDENUMPROC
    def _enum_worker(hwnd, _lparam):
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(int(hwnd), buf, 256)
            if (buf.value or "").strip() != "WorkerW":
                return True
            lv = _find_listview_in_parent(int(hwnd))
            if lv:
                found.append(lv)
                return False
        except Exception:
            pass
        return True

    user32.EnumWindows(_enum_worker, 0)
    return int(found[0]) if found else 0


def _get_listview_item_text(listview_hwnd: int, index: int) -> str:
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    text_buf = ctypes.create_unicode_buffer(512)

    class LVITEMW(ctypes.Structure):
        _fields_ = [
            ("mask", wintypes.UINT),
            ("iItem", ctypes.c_int),
            ("iSubItem", ctypes.c_int),
            ("state", wintypes.UINT),
            ("stateMask", wintypes.UINT),
            ("pszText", ctypes.c_void_p),
            ("cchTextMax", ctypes.c_int),
            ("iImage", ctypes.c_int),
            ("lParam", ctypes.c_longlong),
            ("iIndent", ctypes.c_int),
        ]

    item = LVITEMW()
    item.mask = 0x0001  # LVIF_TEXT
    item.iItem = int(index)
    item.iSubItem = 0
    item.pszText = ctypes.cast(text_buf, ctypes.c_void_p)
    item.cchTextMax = 511
    user32.SendMessageW(int(listview_hwnd), LVM_GETITEMTEXTW, int(index), ctypes.byref(item))
    return (text_buf.value or "").strip()


def _get_listview_item_center(listview_hwnd: int, index: int) -> Tuple[int, int]:
    import ctypes

    user32 = _user32()

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rect = RECT()
    rect.left = LVIR_BOUNDS
    if not user32.SendMessageW(int(listview_hwnd), LVM_GETITEMRECT, int(index), ctypes.byref(rect)):
        raise RuntimeError(f"无法获取 ListView 项 #{index} 的矩形")
    cx = int((rect.left + rect.right) // 2)
    cy = int((rect.top + rect.bottom) // 2)
    return cx, cy


def _screen_to_client(hwnd: int, sx: int, sy: int) -> Tuple[int, int]:
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT(int(sx), int(sy))
    if not _user32().ScreenToClient(int(hwnd), ctypes.byref(pt)):
        return int(sx), int(sy)
    return int(pt.x), int(pt.y)


def _client_to_screen(hwnd: int, cx: int, cy: int) -> Tuple[int, int]:
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT(int(cx), int(cy))
    if not _user32().ClientToScreen(int(hwnd), ctypes.byref(pt)):
        return int(cx), int(cy)
    return int(pt.x), int(pt.y)


def find_icon_index_by_name(listview_hwnd: int, target_name: str) -> int:
    user32 = _user32()
    count = int(user32.SendMessageW(int(listview_hwnd), LVM_GETITEMCOUNT, 0, 0) or 0)
    for i in range(count):
        text = _get_listview_item_text(listview_hwnd, i)
        if _match_icon_name(text, target_name):
            return i
    return -1


def resolve_shell_listview_at_screen(
    screen_x: int,
    screen_y: int,
    *,
    icon_name: str = "",
    listview_hwnd: int = 0,
) -> Optional[ShellIconTarget]:
    """
    按屏幕坐标向桌面 SysListView32 发送消息（Win10/11 图标名常为空，需 UIA/视觉坐标兜底）。
    """
    lv = int(listview_hwnd or 0) or get_desktop_listview_hwnd()
    if not lv:
        logger.info("shell_listview: 未找到桌面 SysListView32")
        return None
    cx, cy = _screen_to_client(lv, int(screen_x), int(screen_y))
    sx, sy = _client_to_screen(lv, cx, cy)
    return ShellIconTarget(
        listview_hwnd=lv,
        index=-1,
        icon_name=(icon_name or "").strip(),
        client_x=cx,
        client_y=cy,
        screen_x=sx,
        screen_y=sy,
    )


def resolve_shell_listview_icon(icon_name: str) -> Optional[ShellIconTarget]:
    name = (icon_name or "").strip()
    if not name:
        return None
    lv = get_desktop_listview_hwnd()
    if not lv:
        logger.info("shell_listview: 按名称查找失败，未找到 ListView hwnd")
        return None
    idx = find_icon_index_by_name(lv, name)
    if idx < 0:
        logger.info(
            "shell_listview: ListView hwnd=%s 中未按名称命中「%s」（Win10/11 项文本可能为空）",
            lv,
            name,
        )
        return None
    try:
        cx, cy = _get_listview_item_center(lv, idx)
    except RuntimeError as exc:
        logger.info("shell_listview: 名称命中 index=%s 但无法取矩形: %s", idx, exc)
        return None
    sx, sy = _client_to_screen(lv, cx, cy)
    return ShellIconTarget(
        listview_hwnd=lv,
        index=idx,
        icon_name=name,
        client_x=cx,
        client_y=cy,
        screen_x=sx,
        screen_y=sy,
    )


def _post_lparam(cx: int, cy: int) -> int:
    return (int(cy) << 16) | (int(cx) & 0xFFFF)


def post_listview_pointer(
    listview_hwnd: int,
    client_x: int,
    client_y: int,
    action: str,
) -> None:
    """向 SysListView32 客户区坐标发送鼠标消息（PostMessage，不移动光标）。"""
    user32 = _user32()
    hwnd = int(listview_hwnd)
    lp = _post_lparam(client_x, client_y)
    act = (action or "click").strip().lower()

    if act == "double_click":
        gap = max(0.12, min(0.45, float(os.environ.get("DESKTOP_SHELL_DBLCLICK_GAP", "0.18") or 0.18)))
        for i in range(2):
            user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
            time.sleep(0.03)
            user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
            if i == 0:
                time.sleep(gap)
        user32.PostMessageW(hwnd, WM_LBUTTONDBLCLK, MK_LBUTTON, lp)
    elif act == "right_click":
        user32.PostMessageW(hwnd, WM_RBUTTONDOWN, MK_RBUTTON, lp)
        time.sleep(0.03)
        user32.PostMessageW(hwnd, WM_RBUTTONUP, 0, lp)
    else:
        user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.03)
        user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def execute_shell_listview_action(
    step: dict,
    action: str,
    *,
    target: Optional[ShellIconTarget] = None,
) -> ShellIconTarget:
    resolved = target
    if not resolved:
        name = icon_name_from_step(step)
        if not name:
            raise RuntimeError("桌面 ListView 消息点击缺少图标名称")
        resolved = resolve_shell_listview_icon(name)
        if not resolved:
            raise RuntimeError(f"桌面 ListView 中未找到图标「{name}」")
    post_listview_pointer(
        resolved.listview_hwnd,
        resolved.client_x,
        resolved.client_y,
        action,
    )
    return resolved


def try_resolve_shell_listview_step(step: dict) -> Optional[ShellIconTarget]:
    if not shell_message_enabled():
        return None
    if not is_desktop_listitem_step(step):
        return None
    name = icon_name_from_step(step)
    if not name:
        return None
    return resolve_shell_listview_icon(name)
