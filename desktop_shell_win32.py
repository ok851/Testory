# -*- coding: utf-8 -*-
"""
桌面图标层纯 Win32 命中（ListView LVM_*），供录制悬停高亮使用。
不经过 pywinauto/UIA，避免按住 Ctrl 时鼠标卡顿。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_shell_win32 仅支持 Windows")

_ICON_CACHE: Dict[str, object] = {"ts": 0.0, "bounds": []}
_ICON_CACHE_LOCK = threading.Lock()
_CACHE_TTL = float(os.environ.get("DESKTOP_WIN32_CACHE_SEC", "30") or "30")

# 桌面顶层类名（Win10/11 光标在桌面时常为 WorkerW 而非 Progman）
_DESKTOP_ROOT_CLASSES = frozenset({"Progman", "WorkerW"})

LVM_GETITEMCOUNT = 0x1004
LVM_GETITEMRECT = 0x100E
LVIR_BOUNDS = 0

# 单格图标合理上限，防止异常矩形盖住整屏
_MAX_ICON_W = int(os.environ.get("DESKTOP_ICON_MAX_W", "320") or "320")
_MAX_ICON_H = int(os.environ.get("DESKTOP_ICON_MAX_H", "320") or "320")
# 控件悬停高亮上限（仍拒绝接近全屏的矩形）
_MAX_HOVER_W = int(os.environ.get("DESKTOP_HOVER_MAX_W", "520") or "520")
_MAX_HOVER_H = int(os.environ.get("DESKTOP_HOVER_MAX_H", "420") or "420")


def _user32():
    import ctypes

    return ctypes.windll.user32


def _screen_size() -> Tuple[int, int]:
    u = _user32()
    return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))


def is_desktop_root_hwnd(hwnd: int) -> bool:
    if not hwnd:
        return False
    import ctypes

    buf = ctypes.create_unicode_buffer(256)
    if not _user32().GetClassNameW(int(hwnd), buf, 256):
        return False
    return (buf.value or "").strip() in _DESKTOP_ROOT_CLASSES


def sanitize_highlight_rect(
    rect: Optional[Tuple[int, int, int, int]],
    *,
    max_w: Optional[int] = None,
    max_h: Optional[int] = None,
) -> Optional[Tuple[int, int, int, int]]:
    """拒绝全屏级矩形，避免 Tk 顶置窗口盖住桌面导致黑屏。"""
    if not rect:
        return None
    left, top, right, bottom = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
    w, h = right - left, bottom - top
    if w < 6 or h < 6:
        return None
    cap_w = int(max_w if max_w is not None else _MAX_ICON_W)
    cap_h = int(max_h if max_h is not None else _MAX_ICON_H)
    if w > cap_w or h > cap_h:
        return None
    sw, sh = _screen_size()
    if sw > 0 and sh > 0 and (w > sw * 0.42 or h > sh * 0.42):
        return None
    return left, top, right, bottom


def sanitize_icon_rect(
    rect: Optional[Tuple[int, int, int, int]],
) -> Optional[Tuple[int, int, int, int]]:
    return sanitize_highlight_rect(rect, max_w=_MAX_ICON_W, max_h=_MAX_ICON_H)


def sanitize_hover_rect(
    rect: Optional[Tuple[int, int, int, int]],
) -> Optional[Tuple[int, int, int, int]]:
    return sanitize_highlight_rect(rect, max_w=_MAX_HOVER_W, max_h=_MAX_HOVER_H)


def _listview_under_defview(parent_hwnd: int) -> int:
    u = _user32()
    defview = int(u.FindWindowExW(int(parent_hwnd), 0, "SHELLDLL_DefView", None) or 0)
    if not defview:
        return 0
    lv = int(u.FindWindowExW(defview, 0, "SysListView32", "FolderView") or 0)
    if not lv:
        lv = int(u.FindWindowExW(defview, 0, "SysListView32", None) or 0)
    return lv


def _find_desktop_listview_hwnd() -> int:
    u = _user32()
    import ctypes
    from ctypes import wintypes

    found: List[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(int(hwnd), buf, 256)
        if (buf.value or "").strip() == "WorkerW":
            lv = _listview_under_defview(int(hwnd))
            if lv:
                found.append(int(lv))
                return False
        return True

    try:
        u.EnumWindows(_enum, 0)
    except Exception:
        pass
    if found:
        return found[0]

    progman = int(u.FindWindowW("Progman", None) or 0)
    if progman:
        lv = _listview_under_defview(progman)
        if lv:
            return lv
    return 0


def _collect_bounds_sync() -> List[Dict[str, int]]:
    import ctypes

    lv = _find_desktop_listview_hwnd()
    if not lv:
        return []

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    u = _user32()
    count = int(u.SendMessageW(lv, LVM_GETITEMCOUNT, 0, 0) or 0)
    rows: List[Dict[str, int]] = []
    for idx in range(count):
        rc = RECT()
        rc.left = LVIR_BOUNDS
        if not u.SendMessageW(lv, LVM_GETITEMRECT, idx, ctypes.byref(rc)):
            continue
        if rc.right <= rc.left or rc.bottom <= rc.top:
            continue
        tl = POINT(rc.left, rc.top)
        br = POINT(rc.right, rc.bottom)
        u.ClientToScreen(lv, ctypes.byref(tl))
        u.ClientToScreen(lv, ctypes.byref(br))
        row = {
            "left": int(tl.x),
            "top": int(tl.y),
            "right": int(br.x),
            "bottom": int(br.y),
        }
        if sanitize_icon_rect(
            (row["left"], row["top"], row["right"], row["bottom"])
        ):
            rows.append(row)
    return rows


def refresh_win32_desktop_icon_cache(
    *,
    force: bool = False,
    allow_sync: bool = True,
) -> List[Dict[str, int]]:
    now = time.time()
    with _ICON_CACHE_LOCK:
        if (
            not force
            and _ICON_CACHE["bounds"]
            and now - float(_ICON_CACHE["ts"] or 0) < _CACHE_TTL
        ):
            return list(_ICON_CACHE["bounds"])  # type: ignore[arg-type]
    if not allow_sync and not force:
        schedule_win32_desktop_icon_cache_refresh()
        with _ICON_CACHE_LOCK:
            return list(_ICON_CACHE["bounds"] or [])  # type: ignore[arg-type]
    bounds = _collect_bounds_sync()
    with _ICON_CACHE_LOCK:
        _ICON_CACHE["ts"] = time.time()
        _ICON_CACHE["bounds"] = bounds
    return bounds


def schedule_win32_desktop_icon_cache_refresh() -> None:
    def _worker() -> None:
        try:
            refresh_win32_desktop_icon_cache(force=True, allow_sync=True)
        except Exception:
            pass

    threading.Thread(
        target=_worker, daemon=True, name="win32-desktop-icons"
    ).start()


def _point_in_rect(x: int, y: int, b: Dict[str, int]) -> bool:
    return (
        int(b["left"]) <= int(x) <= int(b["right"])
        and int(b["top"]) <= int(y) <= int(b["bottom"])
    )


def desktop_icon_rect_at_win32(
    x: int,
    y: int,
    *,
    allow_sync_build: bool = False,
) -> Optional[Tuple[int, int, int, int]]:
    """屏幕坐标命中桌面图标矩形；UI 线程调用时 allow_sync_build 必须为 False。"""
    bounds = refresh_win32_desktop_icon_cache(allow_sync=allow_sync_build)
    if not bounds:
        if not allow_sync_build:
            schedule_win32_desktop_icon_cache_refresh()
        return None
    best: Optional[Dict[str, int]] = None
    best_area: Optional[int] = None
    for b in bounds:
        if not _point_in_rect(x, y, b):
            continue
        area = max(
            1,
            (int(b["right"]) - int(b["left"])) * (int(b["bottom"]) - int(b["top"])),
        )
        if best is None or area < (best_area or area + 1):
            best = b
            best_area = area
    if not best:
        return None
    return sanitize_icon_rect(
        (
            int(best["left"]),
            int(best["top"]),
            int(best["right"]),
            int(best["bottom"]),
        )
    )


def hwnd_under_cursor(
    x: int,
    y: int,
    exclude: Optional[set] = None,
) -> Optional[int]:
    """屏幕坐标下最深层可见 HWND（Win32，无 UIA）。"""
    ex = exclude or set()
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    u = _user32()
    pt = POINT(int(x), int(y))
    h = int(u.WindowFromPoint(pt) or 0)
    if not h or h in ex:
        return None
    for _ in range(48):
        if h in ex or not u.IsWindow(h):
            return None
        client = POINT(int(x), int(y))
        if not u.ScreenToClient(int(h), ctypes.byref(client)):
            break
        child = int(u.ChildWindowFromPoint(int(h), client) or 0)
        if not child or child == h or child in ex:
            break
        h = child
    root = int(u.GetAncestor(int(h), 2) or h)
    if root in ex:
        return None
    return int(h)


def hwnd_screen_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """非桌面区域悬停：高亮光标下控件外框（Win32，无 UIA）。"""
    if not hwnd or is_desktop_root_hwnd(hwnd):
        return None
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rc = RECT()
    if not _user32().GetWindowRect(int(hwnd), ctypes.byref(rc)):
        return None
    if rc.right <= rc.left or rc.bottom <= rc.top:
        return None
    return sanitize_hover_rect(
        (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))
    )


def control_rect_at_screen_point(
    x: int,
    y: int,
    exclude: Optional[set] = None,
) -> Optional[Tuple[int, int, int, int]]:
    """悬停高亮：桌面仅图标格；其它区域用光标下子窗口矩形。"""
    ex = exclude or set()
    root = hwnd_under_cursor(x, y, ex)
    if not root:
        return None
    if is_desktop_root_hwnd(root):
        return desktop_icon_rect_at_win32(x, y, allow_sync_build=False)
    return hwnd_screen_rect(root)
