# -*- coding: utf-8 -*-
"""
桌面捕获高亮：四条实心细边框（非 Layered、非全屏）。

禁止使用：
- 全屏 Tk / transparentcolor（色键失效会整屏变黑）
- WS_EX_LAYERED / UpdateLayeredWindow（部分 GPU/DWM 下会黑屏卡死）

仅创建 4 个 WS_POPUP 细条；鼠标命中穿透，绝不盖住桌面。
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Optional, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_highlight_win32 仅支持 Windows")

Rect = Tuple[int, int, int, int]

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_HIDE = 0
# 64 位下必须用指针宽度的 -1，裸 int -1 会导致 SetWindowPos 失败
HWND_TOPMOST = wintypes.HWND(-1)
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
ERROR_CLASS_ALREADY_EXISTS = 1410
RDW_INVALIDATE = 0x0001
RDW_ERASE = 0x0004
RDW_UPDATENOW = 0x0100
RDW_FRAME = 0x0400

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
)


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_char * 32),
    ]


user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.EndPaint.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
user32.SetWindowPos.restype = wintypes.BOOL


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


_brush_by_hwnd: dict = {}
_wndproc_ref = None


def _rgb(r: int, g: int, b: int) -> int:
    return (b << 16) | (g << 8) | r


def _highlight_wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_NCHITTEST:
        return HTTRANSPARENT
    if msg == WM_ERASEBKGND:
        return 1
    if msg == WM_PAINT:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            brush = _brush_by_hwnd.get(int(hwnd)) or gdi32.GetStockObject(0)
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            user32.FillRect(hdc, ctypes.byref(rc), brush)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))


def _ensure_window_class(class_name: str) -> None:
    global _wndproc_ref
    if _wndproc_ref is None:
        _wndproc_ref = WNDPROC(_highlight_wnd_proc)

    wc = WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = _wndproc_ref
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.hIcon = 0
    wc.hCursor = 0
    wc.hbrBackground = 0
    wc.lpszMenuName = None
    wc.lpszClassName = class_name

    atom = user32.RegisterClassW(ctypes.byref(wc))
    if atom:
        return
    err = int(kernel32.GetLastError() or 0)
    if err == ERROR_CLASS_ALREADY_EXISTS:
        return
    raise OSError(f"RegisterClassW failed: {err}")


def _screen_size() -> Tuple[int, int]:
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def rect_is_near_fullscreen(rect: Rect, *, coverage: float = 0.72) -> bool:
    """接近整屏的框不画（避免把主窗口当元素）。"""
    l, t, r, b = rect
    w = abs(int(r) - int(l))
    h = abs(int(b) - int(t))
    if w < 4 or h < 4:
        return True
    sw, sh = _screen_size()
    if sw <= 0 or sh <= 0:
        return False
    if w >= int(sw * 0.95) and h >= int(sh * 0.95):
        return True
    if (w * h) >= int(sw * sh * coverage):
        return True
    return False


class Win32HighlightBorder:
    """四边框高亮：非 Layered、鼠标穿透、绝不铺满屏幕。"""

    _CLASS = "TestoryHighlightBorderSolidV2"

    def __init__(self, *, thickness: int = 3, color_rgb: Tuple[int, int, int] = (34, 197, 94)):
        self._thickness = max(2, min(6, int(thickness)))
        self._color = color_rgb
        self._hwnds: list = []
        self._lock = threading.Lock()
        self._visible = False
        self._last: Optional[Rect] = None
        self._brush = gdi32.CreateSolidBrush(_rgb(*color_rgb))
        self._hinstance = kernel32.GetModuleHandleW(None)
        _ensure_window_class(self._CLASS)

    def _create_strip(self) -> int:
        ex = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
        hwnd = user32.CreateWindowExW(
            ex,
            self._CLASS,
            "",
            WS_POPUP,
            -32000,
            -32000,
            1,
            1,
            0,
            0,
            self._hinstance,
            None,
        )
        if not hwnd:
            return 0
        hwnd_i = int(hwnd)
        _brush_by_hwnd[hwnd_i] = self._brush
        user32.ShowWindow(hwnd_i, SW_HIDE)
        return hwnd_i

    def _ensure_hwnds(self) -> None:
        with self._lock:
            while len(self._hwnds) < 4:
                h = self._create_strip()
                if not h:
                    break
                self._hwnds.append(h)

    def show(self, rect: Rect) -> None:
        if rect_is_near_fullscreen(rect):
            self.hide()
            return
        l, t, r, b = rect
        x0, y0 = min(int(l), int(r)), min(int(t), int(b))
        x1, y1 = max(int(l), int(r)), max(int(t), int(b))
        pad = 1
        x0 -= pad
        y0 -= pad
        x1 += pad
        y1 += pad
        framed = (x0, y0, x1, y1)
        if self._visible and self._last == framed:
            return

        self._ensure_hwnds()
        if len(self._hwnds) < 4:
            return

        th = self._thickness
        w = max(1, x1 - x0)
        h = max(1, y1 - y0)
        sw, sh = _screen_size()
        w = min(w, max(sw, 1))
        h = min(h, max(sh, 1))
        geos = [
            (x0, y0, w, th),
            (x0, y1 - th, w, th),
            (x0, y0, th, h),
            (x1 - th, y0, th, h),
        ]
        with self._lock:
            shown = 0
            for i, hwnd in enumerate(self._hwnds[:4]):
                gx, gy, gw, gh = geos[i]
                if min(gw, gh) > th + 2:
                    user32.ShowWindow(int(hwnd), SW_HIDE)
                    continue
                if max(gw, gh) > max(sw, sh) * 2 + 200:
                    user32.ShowWindow(int(hwnd), SW_HIDE)
                    continue
                ok = user32.SetWindowPos(
                    wintypes.HWND(int(hwnd)),
                    HWND_TOPMOST,
                    int(gx),
                    int(gy),
                    max(1, int(gw)),
                    max(1, int(gh)),
                    SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
                if ok:
                    shown += 1
                    user32.RedrawWindow(
                        int(hwnd),
                        None,
                        None,
                        RDW_INVALIDATE | RDW_ERASE | RDW_UPDATENOW | RDW_FRAME,
                    )
            self._visible = shown > 0
            self._last = framed if shown else None

    def hide(self) -> None:
        with self._lock:
            for hwnd in self._hwnds:
                if hwnd:
                    user32.ShowWindow(int(hwnd), SW_HIDE)
            self._visible = False
            self._last = None

    def destroy(self) -> None:
        self.hide()
        with self._lock:
            for hwnd in self._hwnds:
                if hwnd:
                    try:
                        _brush_by_hwnd.pop(int(hwnd), None)
                        user32.DestroyWindow(int(hwnd))
                    except Exception:
                        pass
            self._hwnds.clear()
        if self._brush:
            try:
                gdi32.DeleteObject(self._brush)
            except Exception:
                pass
            self._brush = 0


class Win32CaptureBadge:
    def show(self, text: str = "") -> None:
        del text

    def hide(self) -> None:
        return

    def destroy(self) -> None:
        return
