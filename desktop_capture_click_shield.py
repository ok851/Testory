# -*- coding: utf-8 -*-
"""
捕获态点击挡板：全屏近透明 Win32 窗口接收点击，不送达目标应用。

不使用 WH_MOUSE_LL（会卡顿），不使用 SetCapture（对外部窗口无效）。
仅用 WS_EX_LAYERED + Alpha=1 的顶层窗口吃掉点击；悬停探测时短暂
WS_EX_TRANSPARENT，让 UIA ElementFromPoint 打穿挡板。
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
from ctypes import wintypes
from typing import List, Optional, Sequence, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_capture_click_shield 仅支持 Windows")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TRANSPARENT_LAYERED = WS_EX_LAYERED | WS_EX_TRANSPARENT

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
HWND_TOPMOST = wintypes.HWND(-1)

LWA_ALPHA = 0x00000002
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_MOUSEMOVE = 0x0200
WM_NCHITTEST = 0x0084
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
HTCLIENT = 1
RGN_DIFF = 4
ERROR_CLASS_ALREADY_EXISTS = 1410

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
)


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


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
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

_CLASS = "TestoryCaptureClickShield"
_wndproc_ref = None
_owner_by_hwnd: dict = {}


def _virtual_screen() -> Tuple[int, int, int, int]:
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79
    l = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    t = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    w = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)) or int(user32.GetSystemMetrics(0))
    h = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)) or int(user32.GetSystemMetrics(1))
    return l, t, w, h


def _shield_wnd_proc(hwnd, msg, wparam, lparam):
    owner = _owner_by_hwnd.get(int(hwnd))
    if msg in (WM_LBUTTONDOWN, WM_LBUTTONDBLCLK):
        return 0
    if msg == WM_LBUTTONUP:
        if owner is not None and not owner._click_through:
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            pt = POINT(x, y)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            try:
                owner._events.put_nowait(("click", int(pt.x), int(pt.y)))
            except Exception:
                pass
        return 0
    if msg == WM_DESTROY:
        _owner_by_hwnd.pop(int(hwnd), None)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _ensure_class() -> None:
    global _wndproc_ref
    if _wndproc_ref is not None:
        return
    _wndproc_ref = WNDPROC(_shield_wnd_proc)
    wc = WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = _wndproc_ref
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.hIcon = None
    wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
    wc.hbrBackground = None
    wc.lpszMenuName = None
    wc.lpszClassName = _CLASS
    if not user32.RegisterClassW(ctypes.byref(wc)):
        err = int(ctypes.get_last_error() or 0)
        if err != ERROR_CLASS_ALREADY_EXISTS:
            raise OSError(f"RegisterClassW 失败: {err}")


class CaptureClickShield:
    """全屏近透明挡板：吃掉点击，工具条区域挖洞放行。"""

    def __init__(self) -> None:
        self._hwnd = 0
        self._visible = False
        self._click_through = False
        self._events: "queue.Queue[Tuple[str, int, int]]" = queue.Queue()
        self._lock = threading.Lock()
        self._exclude: List[Tuple[int, int, int, int]] = []

    @property
    def enabled(self) -> bool:
        return bool(self._visible and self._hwnd)

    def set_exclude_rects(self, rects: Sequence[Tuple[int, int, int, int]]) -> None:
        cleaned: List[Tuple[int, int, int, int]] = []
        for r in rects or []:
            if not r or len(r) != 4:
                continue
            l, t, right, b = int(r[0]), int(r[1]), int(r[2]), int(r[3])
            if right - l >= 2 and b - t >= 2:
                cleaned.append((l, t, right, b))
        self._exclude = cleaned
        if self._hwnd and self._visible:
            self._apply_region()

    def show(self) -> None:
        with self._lock:
            self._ensure_window()
            self._click_through = False
            self._apply_exstyle(click_through=False)
            self._apply_region()
            l, t, w, h = _virtual_screen()
            user32.SetWindowPos(
                self._hwnd,
                HWND_TOPMOST,
                l,
                t,
                w,
                h,
                SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
            user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
            self._visible = True
            self._drain()

    def hide(self) -> None:
        with self._lock:
            self._visible = False
            self._click_through = False
            if self._hwnd:
                user32.ShowWindow(self._hwnd, SW_HIDE)
            self._drain()

    def set_click_through(self, enabled: bool) -> None:
        """悬停 UIA 探测时短暂打穿挡板。"""
        if not self._hwnd or not self._visible:
            return
        want = bool(enabled)
        if self._click_through == want:
            return
        self._click_through = want
        self._apply_exstyle(click_through=want)
        # 保持顶层
        user32.SetWindowPos(
            self._hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def pop_click(self) -> Optional[Tuple[int, int]]:
        try:
            kind, x, y = self._events.get_nowait()
        except queue.Empty:
            return None
        if kind == "click":
            return int(x), int(y)
        return None

    def uninstall(self) -> None:
        with self._lock:
            self._visible = False
            self._drain()
            hwnd = self._hwnd
            self._hwnd = 0
        if hwnd:
            _owner_by_hwnd.pop(int(hwnd), None)
            try:
                user32.DestroyWindow(hwnd)
            except Exception:
                pass

    def _drain(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break

    def _ensure_window(self) -> None:
        if self._hwnd:
            return
        _ensure_class()
        l, t, w, h = _virtual_screen()
        hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED,
            _CLASS,
            "TestoryCaptureShield",
            WS_POPUP,
            l,
            t,
            w,
            h,
            None,
            None,
            kernel32.GetModuleHandleW(None),
            None,
        )
        if not hwnd:
            raise OSError(f"CreateWindowExW 失败: {ctypes.get_last_error()}")
        self._hwnd = int(hwnd)
        _owner_by_hwnd[self._hwnd] = self
        # Alpha=1：几乎看不见，但仍命中测试
        user32.SetLayeredWindowAttributes(self._hwnd, 0, 1, LWA_ALPHA)

    def _apply_exstyle(self, *, click_through: bool) -> None:
        if not self._hwnd:
            return
        GWL_EXSTYLE = -20
        style = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
        if click_through:
            style |= WS_EX_TRANSPARENT
        # 64 位用 SetWindowLongPtr
        try:
            user32.SetWindowLongPtrW(self._hwnd, GWL_EXSTYLE, style)
        except Exception:
            user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, style)
        user32.SetLayeredWindowAttributes(self._hwnd, 0, 1, LWA_ALPHA)

    def _apply_region(self) -> None:
        if not self._hwnd:
            return
        _l, _t, w, h = _virtual_screen()
        # SetWindowRgn 使用窗口客户区坐标，不是屏幕坐标
        base = gdi32.CreateRectRgn(0, 0, w, h)
        if not base:
            return
        for el, et, er, eb in self._exclude:
            hole = gdi32.CreateRectRgn(el - _l, et - _t, er - _l, eb - _t)
            if hole:
                gdi32.CombineRgn(base, base, hole, RGN_DIFF)
                gdi32.DeleteObject(hole)
        user32.SetWindowRgn(self._hwnd, base, True)
        # SetWindowRgn 取得所有权，勿 Delete base


# 兼容旧名
CaptureMouseSink = CaptureClickShield
CaptureClickGuard = CaptureClickShield
