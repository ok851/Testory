# -*- coding: utf-8 -*-
"""Windows 屏幕级鼠标注入与操作后效果校验。"""

from __future__ import annotations

import re
import sys
import os
import time
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_input 仅支持 Windows")


def _user32():
    import ctypes

    return ctypes.windll.user32


def _set_dpi_aware() -> None:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        elif hasattr(user32, "SetProcessDPIAware"):
            user32.SetProcessDPIAware()
    except Exception:
        pass


def physical_mouse_enabled() -> bool:
    """默认关闭物理鼠标移动（后台执行不抢用户光标）；DESKTOP_PHYSICAL_MOUSE=1 开启。"""
    raw = (os.environ.get("DESKTOP_PHYSICAL_MOUSE") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def steal_focus_enabled() -> bool:
    """默认不抢前台焦点；DESKTOP_STEAL_FOCUS=1 时 SetForegroundWindow。"""
    raw = (os.environ.get("DESKTOP_STEAL_FOCUS") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _save_cursor_pos() -> Tuple[int, int]:
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    _user32().GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def _restore_cursor_pos(x: int, y: int) -> None:
    try:
        _move_cursor(int(x), int(y))
    except Exception:
        pass


def message_click_at_screen(
    x: int,
    y: int,
    *,
    double: bool = False,
    right: bool = False,
) -> int:
    """
    向屏幕坐标下窗口发送鼠标消息，不移动用户光标。
    返回接收消息的 HWND。
    """
    import ctypes
    from ctypes import wintypes

    user32 = _user32()

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT(int(x), int(y))
    hwnd = int(user32.WindowFromPoint(pt) or 0)
    if not hwnd:
        raise RuntimeError(f"屏幕坐标 ({x},{y}) 下无有效窗口")
    client = POINT(int(x), int(y))
    if not user32.ScreenToClient(hwnd, ctypes.byref(client)):
        raise RuntimeError(f"无法换算客户端坐标: ({x},{y})")
    lparam = (int(client.y) << 16) | (int(client.x) & 0xFFFF)
    if right:
        user32.PostMessageW(hwnd, 0x0204, 2, lparam)  # WM_RBUTTONDOWN
        user32.PostMessageW(hwnd, 0x0205, 0, lparam)  # WM_RBUTTONUP
    elif double:
        gap = max(0.12, min(0.45, float(os.environ.get("DESKTOP_SHELL_DBLCLICK_GAP", "0.18") or 0.18)))
        for i in range(2):
            user32.PostMessageW(hwnd, 0x0201, 1, lparam)  # WM_LBUTTONDOWN
            time.sleep(0.03)
            user32.PostMessageW(hwnd, 0x0202, 0, lparam)  # WM_LBUTTONUP
            if i == 0:
                time.sleep(gap)
        user32.PostMessageW(hwnd, 0x0203, 1, lparam)  # WM_LBUTTONDBLCLK
    else:
        user32.PostMessageW(hwnd, 0x0201, 1, lparam)  # WM_LBUTTONDOWN
        user32.PostMessageW(hwnd, 0x0202, 0, lparam)  # WM_LBUTTONUP
    return hwnd


def message_click_at_client(
    hwnd: int,
    cx: int,
    cy: int,
    *,
    double: bool = False,
    right: bool = False,
) -> int:
    """向指定 HWND 客户区坐标发送鼠标消息（不依赖该窗口是否在前台）。"""
    if not hwnd:
        raise RuntimeError("message_click_at_client 需要有效 hwnd")
    lparam = (int(cy) << 16) | (int(cx) & 0xFFFF)
    user32 = _user32()
    if right:
        user32.PostMessageW(int(hwnd), 0x0204, 2, lparam)
        user32.PostMessageW(int(hwnd), 0x0205, 0, lparam)
    elif double:
        user32.PostMessageW(int(hwnd), 0x0203, 1, lparam)
    else:
        user32.PostMessageW(int(hwnd), 0x0201, 1, lparam)
        user32.PostMessageW(int(hwnd), 0x0202, 0, lparam)
    return int(hwnd)


def restore_cursor_after_pointer(*, force_physical: bool = False) -> bool:
    """默认不恢复光标（便于看见坐标点击）；DESKTOP_RESTORE_CURSOR=1 时恢复。"""
    raw = (os.environ.get("DESKTOP_RESTORE_CURSOR") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return not force_physical and physical_mouse_enabled()


def pointer_action_at_screen(
    x: int,
    y: int,
    action: str,
    *,
    force_physical: bool = False,
) -> int:
    """统一点击入口；桌面视觉步骤应直接使用 sendinput_pointer_at_screen。"""
    sendinput_pointer_at_screen(int(x), int(y), action)
    return 0


def sendinput_pointer_at_screen(
    x: int, y: int, action: str, *, step: Optional[dict] = None
) -> None:
    """
    桌面指针执行出口。
    - DESKTOP_PHYSICAL_MOUSE=0（默认）：PostMessage 到坐标下窗口，不移动光标。
    - DESKTOP_PHYSICAL_MOUSE=1：SendInput 物理点击（桌面图标类步骤会先尝试置前桌面）。
    """
    _set_dpi_aware()
    act = (action or "click").strip().lower()
    if not physical_mouse_enabled():
        message_click_at_screen(
            int(x),
            int(y),
            double=(act == "double_click"),
            right=(act == "right_click"),
        )
        return
    if should_focus_desktop_before_pointer(step):
        focus_desktop_surface()
    saved = _save_cursor_pos()
    try:
        if act == "double_click":
            _sendinput_move(int(x), int(y))
            time.sleep(0.04)
            _sendinput_click(left=True)
            time.sleep(max(0.05, _double_click_gap_sec()))
            _sendinput_click(left=True)
        elif act == "right_click":
            _sendinput_move(int(x), int(y))
            time.sleep(0.04)
            _sendinput_click(left=False)
        else:
            _sendinput_move(int(x), int(y))
            time.sleep(0.04)
            _sendinput_click(left=True)
    finally:
        if restore_cursor_after_pointer(force_physical=True):
            _restore_cursor_pos(*saved)


def _double_click_gap_sec() -> float:
    """两次单击间隔：过短会导致资源管理器不识别为双击打开。"""
    try:
        sys_ms = float(_user32().GetDoubleClickTime())
        return max(0.12, min(0.45, sys_ms / 1000.0 * 0.42))
    except Exception:
        return 0.18


def focus_desktop_surface() -> bool:
    """
    将 Program Manager / 桌面 ListView 置前，避免浏览器或其它窗口挡住图标坐标。
 返回是否找到桌面宿主窗口。
    """
    user32 = _user32()
    for cls, title in (("Progman", "Program Manager"), ("WorkerW", None)):
        try:
            hwnd = int(user32.FindWindowW(cls, title) or 0)
            if hwnd and is_valid_hwnd(hwnd):
                focus_hwnd(hwnd)
                time.sleep(0.15)
                return True
        except Exception:
            continue
    return False


def should_focus_desktop_before_pointer(step: Optional[dict] = None) -> bool:
    if not physical_mouse_enabled():
        return False
    raw = (os.environ.get("DESKTOP_FOCUS_BEFORE_CLICK") or "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if not step:
        return True
    spec = step.get("desktop_spec") if isinstance(step.get("desktop_spec"), dict) else {}
    if isinstance(step.get("desktop_spec"), str):
        try:
            import json

            spec = json.loads(step.get("desktop_spec") or "{}")
        except Exception:
            spec = {}
    if spec.get("desktop_shell") or spec.get("hybrid_capture"):
        return True
    desc = (step.get("description") or "").lower()
    if "listitem" in desc or "控制面板" in desc or "桌面" in desc:
        return True
    try:
        from modules.desktop.desktop_hybrid_locator import element_snapshot_for_step

        snap = element_snapshot_for_step(step)
        if snap:
            anchor = ((snap.get("selector") or snap).get("anchor_props") or "")
            if "listitem" in str(anchor).lower():
                return True
    except Exception:
        pass
    return False


def _sendinput_move(x: int, y: int) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    vl, vt, vw, vh = virtual_screen_rect()
    nx = int((int(x) - vl) * 65535 / max(1, vw - 1))
    ny = int((int(y) - vt) * 65535 / max(1, vh - 1))

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

    inp = INPUT()
    inp.type = 0
    inp.mi = MOUSEINPUT(nx, ny, 0, 0x8000 | 0x0001, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _sendinput_click(*, left: bool) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = _user32()

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

    down = 0x0002 if left else 0x0008
    up = 0x0004 if left else 0x0010
    for flag in (down, up):
        inp = INPUT()
        inp.type = 0
        inp.mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.02)


def sendinput_type_text(text: str) -> None:
    """Unicode 文本输入。含中文等非 ASCII 时优先剪贴板粘贴，避免逐键截断。"""
    import ctypes
    from ctypes import wintypes

    raw = str(text or "")
    if not raw:
        return
    if any(ord(c) > 127 for c in raw):
        try:
            _paste_unicode_via_clipboard(raw)
            return
        except Exception:
            pass

    user32 = _user32()

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]

    for ch in raw:
        code = ord(ch)
        # BMP 以外需 UTF-16 代理对
        units = []
        if code > 0xFFFF:
            c = code - 0x10000
            units = [0xD800 + (c >> 10), 0xDC00 + (c & 0x3FF)]
        else:
            units = [code]
        for u in units:
            for flag in (0x0004, 0x0006):  # KEYEVENTF_UNICODE / KEYEVENTF_UNICODE|KEYUP
                inp = INPUT()
                inp.type = 1
                inp.ki = KEYBDINPUT(0, u, flag, 0, None)
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            time.sleep(0.008)


def postmessage_type_text_to_hwnd(hwnd: int, text: str) -> dict:
    """向目标窗口异步投递 WM_CHAR（只用 PostMessage，禁止 SendMessage）。

    原因：对微信 Qt 窗口同步 SendMessage 会占用其 UI 线程，表现为窗口卡死、
    搜索结果无法刷新。ASCII 用 WM_CHAR 实测有效；中文同样走 PostMessage WM_CHAR。
    """
    import ctypes
    from ctypes import wintypes

    hwnd = int(hwnd or 0)
    raw = str(text if text is not None else "")
    if not hwnd:
        return {"ok": False, "via": "wm_char_post", "error": "hwnd 无效"}
    if not raw:
        return {"ok": True, "via": "wm_char_post", "chars": 0}
    user32 = _user32()
    WM_CHAR = 0x0102
    sent = 0
    try:
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
    except Exception:
        pass

    for ch in raw:
        code = ord(ch)
        units = []
        if code > 0xFFFF:
            c = code - 0x10000
            units = [0xD800 + (c >> 10), 0xDC00 + (c & 0x3FF)]
        else:
            units = [code & 0xFFFF]
        for u in units:
            try:
                if not user32.PostMessageW(hwnd, WM_CHAR, int(u), 1):
                    return {
                        "ok": False,
                        "via": "wm_char_post",
                        "error": "PostMessageW 失败",
                        "chars": sent,
                    }
                sent += 1
            except Exception as e:
                return {
                    "ok": False,
                    "via": "wm_char_post",
                    "error": str(e)[:200],
                    "chars": sent,
                }
            time.sleep(0.015)
    time.sleep(0.06)
    return {"ok": True, "via": "wm_char_post", "chars": sent, "hwnd": hwnd}


def _set_clipboard_unicode(text: str) -> None:
    """仅写入剪贴板（不按 Ctrl+V）。Win64 需正确 restype。"""
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL

    data = str(text or "")
    if not user32.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        buf = ctypes.create_unicode_buffer(data)
        nbytes = (len(data) + 1) * 2
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, nbytes)
        if not h:
            raise RuntimeError("GlobalAlloc failed")
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            raise RuntimeError("GlobalLock returned NULL")
        try:
            ctypes.memmove(ptr, buf, nbytes)
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            raise RuntimeError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


def paste_text_via_wm_paste(hwnd: int, text: str) -> dict:
    """剪贴板 + 异步 PostMessage(WM_PASTE)。避免 SendMessage 堵死微信 UI。"""
    import ctypes
    from ctypes import wintypes

    hwnd = int(hwnd or 0)
    if not hwnd:
        return {"ok": False, "via": "wm_paste", "error": "hwnd 无效"}
    try:
        _set_clipboard_unicode(text)
    except Exception as e:
        return {"ok": False, "via": "wm_paste", "error": f"clipboard: {e}"[:200]}
    user32 = _user32()
    WM_PASTE = 0x0302
    try:
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        if not user32.PostMessageW(hwnd, WM_PASTE, 0, 0):
            return {"ok": False, "via": "wm_paste", "error": "PostMessage WM_PASTE 失败"}
        time.sleep(0.12)
        return {"ok": True, "via": "wm_paste_post", "hwnd": hwnd, "text_length": len(text or "")}
    except Exception as e:
        return {"ok": False, "via": "wm_paste", "error": str(e)[:200]}


def _paste_unicode_via_clipboard(text: str) -> None:
    """写入剪贴板后 Ctrl+V（不检查前台；调用方应先 reclaim）。"""
    import ctypes
    from ctypes import wintypes

    _set_clipboard_unicode(text)
    user32 = _user32()
    VK_CONTROL, VK_V = 0x11, 0x56

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]

    def _key(vk: int, down: bool) -> None:
        inp = INPUT()
        inp.type = 1
        inp.ki = KEYBDINPUT(vk, 0, 0 if down else 0x0002, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    _key(VK_CONTROL, True)
    time.sleep(0.02)
    _key(VK_V, True)
    time.sleep(0.02)
    _key(VK_V, False)
    _key(VK_CONTROL, False)
    time.sleep(0.08)


def paste_text_via_ctrl_v(hwnd: int, text: str) -> dict:
    """剪贴板 + Ctrl+V（SendInput）。先捕获目标前台，适合 Qt 微信中文。"""
    hwnd = int(hwnd or 0)
    raw = str(text if text is not None else "")
    if not hwnd:
        return {"ok": False, "via": "clipboard_ctrl_v", "error": "hwnd 无效"}
    reclaim = reclaim_foreground_hwnd(hwnd, retries=3)
    if not reclaim.get("ok"):
        return {
            "ok": False,
            "via": "clipboard_ctrl_v",
            "error": reclaim.get("error") or "未能捕获目标前台",
            "reclaim": reclaim,
        }
    try:
        _set_clipboard_unicode(raw)
    except Exception as e:
        return {"ok": False, "via": "clipboard_ctrl_v", "error": f"clipboard: {e}"[:200]}
    if int(get_foreground_hwnd() or 0) != hwnd:
        reclaim2 = reclaim_foreground_hwnd(hwnd, retries=2)
        if not reclaim2.get("ok"):
            return {
                "ok": False,
                "via": "clipboard_ctrl_v",
                "error": "Ctrl+V 前目标窗失前台",
                "reclaim": reclaim2,
            }
    delivery = deliver_keys_to_hwnd(hwnd, ["ctrl", "v"])
    if not delivery.get("ok"):
        try:
            _paste_unicode_via_clipboard(raw)
            time.sleep(0.12)
            return {
                "ok": True,
                "via": "clipboard_ctrl_v_sendinput",
                "hwnd": hwnd,
                "text_length": len(raw),
                "delivery": delivery,
                "reclaim": reclaim,
            }
        except Exception as e:
            return {
                "ok": False,
                "via": "clipboard_ctrl_v",
                "error": delivery.get("error") or str(e)[:200],
                "delivery": delivery,
            }
    time.sleep(0.15)
    return {
        "ok": True,
        "via": "clipboard_ctrl_v",
        "hwnd": hwnd,
        "text_length": len(raw),
        "delivery": delivery,
        "reclaim": reclaim,
    }


def deliver_text_strategies(
    hwnd: int,
    text: str,
    *,
    clear: bool = False,
    prefer_qt: bool = False,
) -> dict:
    """多策略灌字；画面核验由调用方负责。返回最后一次成功投递或全部失败结果。"""
    hwnd = int(hwnd or 0)
    raw = str(text if text is not None else "")
    attempts: List[dict] = []
    if clear and hwnd:
        try:
            deliver_keys_to_hwnd(hwnd, ["ctrl", "a"])
            time.sleep(0.04)
            deliver_keys_to_hwnd(hwnd, ["delete"])
            time.sleep(0.04)
        except Exception:
            pass

    has_cjk = any(ord(c) > 127 for c in raw)
    if prefer_qt:
        order = (
            ["clipboard_ctrl_v", "wm_paste", "wm_char", "sendinput_unicode"]
            if has_cjk
            else ["wm_char", "clipboard_ctrl_v", "wm_paste"]
        )
    else:
        order = ["uia", "clipboard_ctrl_v", "wm_char", "sendinput"]

    last: dict = {"ok": False, "via": "none", "error": "no strategy"}
    for name in order:
        try:
            if name == "uia" and hwnd:
                if uia_set_value_in_hwnd(hwnd, raw):
                    last = {"ok": True, "via": "uia_value", "hwnd": hwnd, "text_length": len(raw)}
                else:
                    last = {"ok": False, "via": "uia_value", "error": "uia set_value 失败"}
            elif name == "clipboard_ctrl_v" and hwnd:
                last = paste_text_via_ctrl_v(hwnd, raw)
            elif name == "wm_paste" and hwnd:
                last = paste_text_via_wm_paste(hwnd, raw)
            elif name == "wm_char" and hwnd:
                last = postmessage_type_text_to_hwnd(hwnd, raw)
            elif name == "sendinput_unicode":
                if hwnd:
                    force_focus_hwnd(hwnd, retries=2)
                sendinput_type_text(raw)
                last = {
                    "ok": True,
                    "via": "sendinput_unicode",
                    "hwnd": hwnd,
                    "text_length": len(raw),
                }
            elif name == "sendinput":
                if hwnd:
                    force_focus_hwnd(hwnd, retries=2)
                if has_cjk:
                    _paste_unicode_via_clipboard(raw)
                    last = {
                        "ok": True,
                        "via": "clipboard_paste",
                        "hwnd": hwnd,
                        "text_length": len(raw),
                    }
                else:
                    sendinput_type_text(raw)
                    last = {
                        "ok": True,
                        "via": "sendinput_fallback",
                        "hwnd": hwnd,
                        "text_length": len(raw),
                    }
            else:
                continue
        except Exception as e:
            last = {"ok": False, "via": name, "error": str(e)[:200]}
        attempts.append(dict(last))
        if last.get("ok"):
            return {**last, "attempts": attempts, "strategy": name}
    return {**last, "attempts": attempts}


_SHELL_ROOT_CLASSES = frozenset({"Progman", "WorkerW", "#32769"})
_SHELL_VIEW_CLASSES = frozenset(
    {"SHELLDLL_DefView", "SysListView32", "DirectUIHWND", "FolderView"}
)


def is_valid_hwnd(hwnd: int) -> bool:
    """窗口句柄是否仍存在（录制时的 hwnd 可能已失效）。"""
    if not hwnd:
        return False
    try:
        return bool(_user32().IsWindow(int(hwnd)))
    except Exception:
        return False


def resolve_hwnd_from_spec(spec: Optional[dict]) -> int:
    """优先使用 spec.hwnd；失效时按窗口标题/正则匹配当前可见窗口。"""
    s = spec or {}
    hwnd = int(s.get("hwnd") or 0)
    if is_valid_hwnd(hwnd):
        return hwnd
    title_pat = (s.get("window_title_re") or "").strip()
    title_sub = (
        (s.get("window_title") or "").strip()
        or (s.get("title_contains") or "").strip()
        or (s.get("title") or "").strip()
    )
    if not title_pat and not title_sub:
        return 0
    for wh, wt, _cls in _enum_visible_windows():
        if title_pat:
            try:
                if re.search(title_pat, wt or "", re.I):
                    return int(wh)
            except re.error:
                if title_pat in (wt or ""):
                    return int(wh)
        elif title_sub and title_sub.lower() in (wt or "").lower():
            return int(wh)
    return 0


def screen_point_on_desktop_shell(x: int, y: int) -> bool:
    """屏幕坐标是否落在桌面 Shell（Progman/图标层）窗口树内。"""
    return is_desktop_shell_hwnd(hwnd_at_screen_point(int(x), int(y)))


def hwnd_at_screen_point(x: int, y: int) -> int:
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT(int(x), int(y))
    return int(_user32().WindowFromPoint(pt) or 0)


def _hwnd_class_name(hwnd: int) -> str:
    import ctypes

    buf = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(int(hwnd), buf, 256)
    return (buf.value or "").strip()


def is_desktop_shell_hwnd(hwnd: int) -> bool:
    """坐标是否落在 Program Manager / 桌面图标层窗口树内。"""
    if not hwnd:
        return False
    u = _user32()
    cur = int(hwnd)
    for _ in range(32):
        if not cur:
            break
        cls = _hwnd_class_name(cur)
        if cls in _SHELL_ROOT_CLASSES or cls in _SHELL_VIEW_CLASSES:
            return True
        cur = int(u.GetParent(cur) or 0)
    return False


def virtual_screen_rect() -> Tuple[int, int, int, int]:
    """虚拟桌面范围 (left, top, width, height)，覆盖多显示器。"""
    u = _user32()
    return (
        int(u.GetSystemMetrics(76)),
        int(u.GetSystemMetrics(77)),
        int(u.GetSystemMetrics(78)),
        int(u.GetSystemMetrics(79)),
    )


def screen_coords_in_virtual_bounds(x: int, y: int) -> bool:
    left, top, width, height = virtual_screen_rect()
    return (
        left <= int(x) < left + width
        and top <= int(y) < top + height
    )


def screen_size() -> tuple[int, int]:
    """主显示器宽高（兼容旧逻辑）。"""
    u = _user32()
    return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))


def hwnd_belongs_to_target(point_hwnd: int, target_hwnd: int) -> bool:
    """屏幕坐标命中窗口是否属于目标顶层/父窗口树。"""
    if not point_hwnd:
        return False
    if not target_hwnd:
        return bool(point_hwnd)
    if int(point_hwnd) == int(target_hwnd):
        return True
    u = _user32()
    cur = int(point_hwnd)
    for _ in range(64):
        if cur == int(target_hwnd):
            return True
        parent = int(u.GetParent(cur) or 0)
        if not parent or parent == cur:
            break
        cur = parent
    try:
        root = int(u.GetAncestor(int(point_hwnd), 2))
        return root == int(target_hwnd)
    except Exception:
        return False


def screen_to_client_xy(hwnd: int, x: int, y: int) -> Tuple[int, int]:
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT(int(x), int(y))
    if not _user32().ScreenToClient(int(hwnd), ctypes.byref(pt)):
        raise RuntimeError(f"ScreenToClient 失败: hwnd={hwnd} ({x},{y})")
    return int(pt.x), int(pt.y)


def client_to_screen_xy(hwnd: int, cx: int, cy: int) -> Tuple[int, int]:
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT(int(cx), int(cy))
    if not _user32().ClientToScreen(int(hwnd), ctypes.byref(pt)):
        raise RuntimeError(f"ClientToScreen 失败: hwnd={hwnd} ({cx},{cy})")
    return int(pt.x), int(pt.y)


def visible_window_effect_for_spec(
    spec: Optional[dict],
    keyword: str = "",
    *,
    fg_before: int = 0,
) -> bool:
    """
    不依赖前台焦点：检测可见窗口标题是否出现预期变化（用于后台双击校验）。
    """
    keys = _effect_keywords(keyword)
    title_hint = (
        (spec or {}).get("window_title_re")
        or (spec or {}).get("window_title")
        or ""
    ).strip()
    for hwnd, title, _cls in _enum_visible_windows():
        if fg_before and hwnd == fg_before:
            continue
        if keys and _title_matches(keyword, [title]):
            return True
        if title_hint and title and title_hint in title:
            return True
    if keys and _title_matches(keyword, _enum_visible_window_titles()):
        return True
    return False


def client_point_in_hwnd(hwnd: int, client_x: int, client_y: int) -> bool:
    """客户区坐标是否在窗口客户区矩形内（用于后台 PostMessage 送达校验）。"""
    if not is_valid_hwnd(hwnd):
        return False
    import ctypes
    from ctypes import wintypes

    u = _user32()
    h = int(hwnd)
    cx, cy = int(client_x), int(client_y)
    rect = wintypes.RECT()
    if u.GetClientRect(h, ctypes.byref(rect)):
        cw = int(rect.right) - int(rect.left)
        ch = int(rect.bottom) - int(rect.top)
        if cw > 0 and ch > 0:
            return (
                int(rect.left) <= cx < int(rect.right)
                and int(rect.top) <= cy < int(rect.bottom)
            )
    try:
        sx, sy = client_to_screen_xy(h, cx, cy)
        wr = wintypes.RECT()
        if u.GetWindowRect(h, ctypes.byref(wr)):
            return (
                int(wr.left) <= sx < int(wr.right)
                and int(wr.top) <= sy < int(wr.bottom)
            )
    except Exception:
        pass
    return u.IsIconic(h) or u.IsZoomed(h)


def verify_client_message_delivered(
    hwnd: int,
    client_x: int,
    client_y: int,
    spec: Optional[dict] = None,
) -> bool:
    """
    后台向目标 HWND 客户区发消息后校验：不依赖 WindowFromPoint / 前台 Z 序。
    """
    anchor = resolve_hwnd_from_spec(spec) if spec else 0
    if not anchor:
        anchor = int(hwnd) if is_valid_hwnd(hwnd) else 0
    if not anchor:
        return False
    return client_point_in_hwnd(anchor, client_x, client_y)


def verify_pointer_delivered(
    x: int,
    y: int,
    *,
    desktop_shell: bool = False,
    physical: bool = False,
    target_hwnd: int = 0,
    used_physical_click: bool = False,
    client_x: Optional[int] = None,
    client_y: Optional[int] = None,
    delivery_mode: str = "",
    spec: Optional[dict] = None,
) -> bool:
    """视觉 SendInput 路径不校验 hwnd/遮挡，物理点击视为已送达。"""
    del desktop_shell, target_hwnd, client_x, client_y, delivery_mode, spec
    return bool(used_physical_click or physical or True)


def _move_cursor(x: int, y: int) -> None:
    _user32().SetCursorPos(int(x), int(y))


def _mouse_button_event(down: bool, *, right: bool = False) -> None:
    import ctypes

    u = _user32()
    if right:
        flag = 0x0008 if down else 0x0010
    else:
        flag = 0x0002 if down else 0x0004
    u.mouse_event(flag, 0, 0, 0, 0)


def screen_click(x: int, y: int, *, right: bool = False) -> None:
    _set_dpi_aware()
    _move_cursor(x, y)
    time.sleep(0.04)
    _mouse_button_event(True, right=right)
    time.sleep(0.02)
    _mouse_button_event(False, right=right)


def screen_double_click(x: int, y: int) -> None:
    """物理双击（仅 DESKTOP_PHYSICAL_MOUSE=1；默认请用 message_click_at_screen）。"""
    _set_dpi_aware()
    _move_cursor(x, y)
    time.sleep(0.05)
    try:
        interval_ms = max(50, int(_user32().GetDoubleClickTime() // 3))
    except Exception:
        interval_ms = 120
    gap = interval_ms / 1000.0
    for i in range(2):
        _mouse_button_event(True, right=False)
        time.sleep(0.02)
        _mouse_button_event(False, right=False)
        if i == 0:
            time.sleep(gap)


def screen_right_click(x: int, y: int) -> None:
    screen_click(x, y, right=True)


def focus_hwnd(hwnd: int) -> None:
    if not hwnd or not steal_focus_enabled():
        return
    try:
        import ctypes

        u = _user32()
        try:
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            pass
        u.AllowSetForegroundWindow(-1)
        u.SetForegroundWindow(int(hwnd))
        time.sleep(0.12)
    except Exception:
        pass


def _alt_unlock_foreground() -> None:
    """模拟一次 Alt 键松开，绕过 Windows「前台锁」（后台进程常被拒绝 SetForegroundWindow）。"""
    try:
        import ctypes

        user32 = _user32()
        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        time.sleep(0.01)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


def _click_window_chrome_to_steal_fg(hwnd: int) -> bool:
    """物理点击目标窗标题栏安全区抢前台（比空 SetForeground 更稳；避开内容区以免误点搜索/聊天）。"""
    hwnd = int(hwnd or 0)
    if not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = _user32()
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        left, top, right, bottom = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        w = max(0, right - left)
        h = max(0, bottom - top)
        if w < 80 or h < 40:
            return False
        # 标题栏中部偏左：避开右上角关闭/最大化，也避开内容区输入框
        x = left + max(40, min(w // 3, w - 40))
        y = top + 12
        screen_click(x, y)
        time.sleep(0.12)
        return int(user32.GetForegroundWindow() or 0) == hwnd
    except Exception:
        return False


def force_focus_hwnd(
    hwnd: int, *, retries: int = 3, steal_click_xy: Optional[Tuple[int, int]] = None
) -> bool:
    """多策略把目标窗抢到前台并验证（核心能力，而非「前台不对就放弃」）。

    策略阶梯：已前台 → AttachThreadInput+SetFG → TOPMOST 闪一下 → Alt 解锁 → 物理点击。
    steal_click_xy：若提供则优先生理点击该点（如搜索框）抢前台，避免点标题栏弄丢输入焦点。
    禁止对顶层 hwnd 调 SetFocus（会把 Qt 微信搜索焦点打回聊天框）。
    """
    import ctypes
    from ctypes import wintypes

    hwnd = int(hwnd or 0)
    if not hwnd or not is_valid_hwnd(hwnd):
        return False
    user32 = _user32()
    kernel32 = ctypes.windll.kernel32

    def _fg_is_target() -> bool:
        return int(user32.GetForegroundWindow() or 0) == hwnd

    if _fg_is_target():
        return True

    # 后台模式：不抢前台，仅当已是目标窗才算成功
    if no_focus_steal_enabled():
        return False

    attempts = max(1, int(retries or 1))
    for i in range(attempts):
        try:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            try:
                user32.AllowSetForegroundWindow(-1)
            except Exception:
                pass

            fg = int(user32.GetForegroundWindow() or 0)
            tid_cur = int(kernel32.GetCurrentThreadId() or 0)
            pid = wintypes.DWORD()
            tid_fg = int(user32.GetWindowThreadProcessId(fg, ctypes.byref(pid)) or 0) if fg else 0
            tid_tg = int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or 0)
            attached: List[int] = []
            try:
                if tid_fg and tid_fg != tid_cur:
                    if user32.AttachThreadInput(tid_cur, tid_fg, True):
                        attached.append(tid_fg)
                if tid_tg and tid_tg != tid_cur and tid_tg != tid_fg:
                    if user32.AttachThreadInput(tid_cur, tid_tg, True):
                        attached.append(tid_tg)

                user32.BringWindowToTop(hwnd)
                user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                if i >= 1:
                    _alt_unlock_foreground()
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.06 + 0.04 * i)
            finally:
                for tid in attached:
                    try:
                        user32.AttachThreadInput(tid_cur, tid, False)
                    except Exception:
                        pass

            if _fg_is_target():
                return True

            if i >= 1:
                if steal_click_xy and len(steal_click_xy) == 2:
                    try:
                        screen_click(int(steal_click_xy[0]), int(steal_click_xy[1]))
                        time.sleep(0.12)
                        if _fg_is_target():
                            return True
                    except Exception:
                        pass
                if _click_window_chrome_to_steal_fg(hwnd):
                    return True
        except Exception:
            continue
    return _fg_is_target()


def reclaim_foreground_hwnd(
    hwnd: int,
    *,
    retries: int = 4,
    steal_click_xy: Optional[Tuple[int, int]] = None,
) -> dict:
    """显式「捕获目标窗口前台」：返回策略结果，供热键/输入前调用。"""
    hwnd = int(hwnd or 0)
    if not hwnd:
        return {"ok": False, "error": "hwnd 无效", "hwnd": 0}
    if not is_valid_hwnd(hwnd):
        return {"ok": False, "error": "hwnd 已失效，需重新 windows_focus_app", "hwnd": hwnd}
    before = get_foreground_hwnd()
    ok = force_focus_hwnd(hwnd, retries=retries, steal_click_xy=steal_click_xy)
    after = get_foreground_hwnd()
    title, cls = _hwnd_title_class(hwnd)
    fg_title, _ = _hwnd_title_class(after)
    return {
        "ok": bool(ok and after == hwnd),
        "hwnd": hwnd,
        "title": title,
        "class_name": cls,
        "before_fg": before,
        "after_fg": after,
        "fg_title": fg_title,
        "error": (
            ""
            if ok and after == hwnd
            else f"未能把「{title or hwnd}」抢到前台（当前前台={fg_title or after}）"
        ),
    }


def _vk_map() -> dict:
    return {
        "ctrl": 0x11,
        "control": 0x11,
        "shift": 0x10,
        "alt": 0x12,
        "win": 0x5B,
        "enter": 0x0D,
        "return": 0x0D,
        "esc": 0x1B,
        "escape": 0x1B,
        "tab": 0x09,
        "space": 0x20,
        "backspace": 0x08,
        "delete": 0x2E,
        "del": 0x2E,
        "up": 0x26,
        "down": 0x28,
        "left": 0x25,
        "right": 0x27,
        "home": 0x24,
        "end": 0x23,
        "f1": 0x70,
        "f2": 0x71,
        "f3": 0x72,
        "f4": 0x73,
        "f5": 0x74,
    }


def postmessage_key_to_hwnd(hwnd: int, vk: int, *, down: bool = True) -> bool:
    """向目标 hwnd 投递 WM_KEYDOWN/WM_KEYUP（不依赖全局前台）。"""
    hwnd = int(hwnd or 0)
    if not hwnd or not vk:
        return False
    user32 = _user32()
    WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
    msg = WM_KEYDOWN if down else WM_KEYUP
    # lParam: repeat=1, scan=MapVirtualKey, flags
    try:
        scan = int(user32.MapVirtualKeyW(int(vk), 0) or 0)
    except Exception:
        scan = 0
    lparam = 1 | (scan << 16)
    if not down:
        lparam |= 1 << 30 | 1 << 31
    try:
        return bool(user32.PostMessageW(hwnd, msg, int(vk) & 0xFFFF, lparam))
    except Exception:
        return False


def uia_set_value_in_hwnd(hwnd: int, text: str) -> bool:
    """优先对**当前焦点**可编辑控件写值；禁止盲写第一个 Edit（易写错控件）。"""
    hwnd = int(hwnd or 0)
    raw = str(text if text is not None else "")
    if not hwnd:
        return False
    try:
        from pywinauto import Desktop  # type: ignore

        win = Desktop(backend="uia").window(handle=hwnd)
        focused = None
        try:
            focused = win.get_focus()
        except Exception:
            focused = None
        if focused is not None:
            try:
                focused.set_edit_text(raw)
                return True
            except Exception:
                # 禁止 type_keys(raw)：pywinauto 会把 + ^ % { } 编译成 Shift/Ctrl/Alt/特殊键，
                # 导致正文里出现莫名字母/数字或触发热键。交给剪贴板 / Unicode SendInput。
                pass
            # 焦点控件不可编辑：再在其祖先/兄弟中找带焦点的 Edit
            try:
                ct = ""
                try:
                    ct = str(focused.element_info.control_type or "")
                except Exception:
                    ct = ""
                if "edit" in ct.lower():
                    return False
            except Exception:
                pass
        # 无可靠焦点时不盲写第一个 Edit，交给剪贴板/SendInput 降级
        return False
    except Exception:
        return False


def _uia_element_text(elem: Any) -> str:
    """从 pywinauto 元素尽量读出可见/可编辑文本。"""
    if elem is None:
        return ""
    for getter in (
        lambda e: e.get_value(),
        lambda e: e.window_text(),
        lambda e: getattr(e.element_info, "name", None),
        lambda e: getattr(e.element_info, "rich_text", None),
    ):
        try:
            val = getter(elem)
            if val is None:
                continue
            s = str(val).strip()
            if s:
                return s
        except Exception:
            continue
    try:
        leg = elem.legacy_properties()
        if isinstance(leg, dict):
            for k in ("Value", "Name", "DefaultAction"):
                s = str(leg.get(k) or "").strip()
                if s:
                    return s
    except Exception:
        pass
    return ""


def uia_get_focused_edit_text(hwnd: int) -> str:
    """回读目标窗当前焦点可编辑控件文本；失败返回空串。"""
    hwnd = int(hwnd or 0)
    if not hwnd:
        return ""
    try:
        from pywinauto import Desktop  # type: ignore

        win = Desktop(backend="uia").window(handle=hwnd)
        focused = None
        try:
            focused = win.get_focus()
        except Exception:
            focused = None
        if focused is None:
            return ""
        return _uia_element_text(focused)
    except Exception:
        return ""


def uia_hwnd_tree_contains_text(
    hwnd: int,
    token: str,
    *,
    max_nodes: int = 180,
) -> Dict[str, Any]:
    """在 hwnd 子树 Name/Value 中查找 token（搜索结果列表等证据）。"""
    hwnd = int(hwnd or 0)
    needle = re.sub(r"\s+", "", str(token or "").strip())
    out: Dict[str, Any] = {"ok": False, "matched": "", "via": ""}
    if not hwnd or not needle:
        return out
    try:
        from pywinauto import Desktop  # type: ignore

        win = Desktop(backend="uia").window(handle=hwnd)
        # descendants 可能很慢；限制数量
        try:
            nodes = win.descendants()
        except Exception:
            nodes = []
        scanned = 0
        for elem in nodes:
            if scanned >= max_nodes:
                break
            scanned += 1
            text = _uia_element_text(elem)
            if not text:
                continue
            compact = re.sub(r"\s+", "", text)
            if needle == compact or needle in compact or compact in needle:
                # 过短通用名易误命中
                if len(compact) < 2:
                    continue
                out["ok"] = True
                out["matched"] = text[:80]
                out["via"] = "uia_name"
                out["scanned"] = scanned
                return out
        out["scanned"] = scanned
        return out
    except Exception as e:
        out["error"] = str(e)[:160]
        return out


def no_focus_steal_enabled() -> bool:
    raw = (os.environ.get("DESKTOP_NO_FOCUS_STEAL") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def uia_invoke_or_click_at_screen(x: int, y: int, *, hwnd: int = 0) -> dict:
    """后台优先：UIA 在坐标处 Invoke/Click，不移动物理鼠标、尽量不抢前台。"""
    try:
        from pywinauto import Desktop  # type: ignore

        desk = Desktop(backend="uia")
        elem = None
        try:
            elem = desk.from_point(int(x), int(y))
        except Exception:
            elem = None
        if elem is None and hwnd:
            try:
                win = desk.window(handle=int(hwnd))
                elem = win.from_point(int(x), int(y))
            except Exception:
                elem = None
        if elem is None:
            return {"ok": False, "via": "uia_point", "error": "no element at point"}
        # InvokePattern / 默认 click（不依赖物理鼠标）
        try:
            elem.invoke()
            return {"ok": True, "via": "uia_invoke", "x": int(x), "y": int(y)}
        except Exception:
            pass
        try:
            elem.click_input(double=False)  # may move cursor; last UIA path
            return {"ok": True, "via": "uia_click_input", "x": int(x), "y": int(y)}
        except Exception as e:
            return {"ok": False, "via": "uia_point", "error": str(e)[:160]}
    except Exception as e:
        return {"ok": False, "via": "uia_point", "error": str(e)[:160]}


def deliver_keys_to_hwnd(hwnd: int, parts: List[str]) -> dict:
    """
    向目标窗口投递按键。

    含 Ctrl/Alt/Win 的组合热键必须用 SendInput（先 force_focus）：
    PostMessage 不会更新 GetKeyState，微信等应用会把 F 当成普通字符打进输入框。
    单键仍可先试 PostMessage。
    """
    hwnd = int(hwnd or 0)
    names = [str(p or "").strip().lower() for p in (parts or []) if str(p or "").strip()]
    if not names:
        return {"ok": False, "via": "none", "error": "empty keys"}
    vkm = _vk_map()
    vks = []
    for n in names:
        if n in vkm:
            vks.append(vkm[n])
        elif len(n) == 1:
            vks.append(ord(n.upper()))
        else:
            vks = []
            break
    has_modifier = any(n in ("ctrl", "control", "shift", "alt", "win") for n in names)
    # 组合热键：禁止 PostMessage（会导致「只打出 f」）
    if hwnd and vks and all(vks) and not has_modifier:
        mods = [v for v, n in zip(vks, names) if n in ("ctrl", "control", "shift", "alt", "win")]
        mains = [v for v, n in zip(vks, names) if n not in ("ctrl", "control", "shift", "alt", "win")]
        ok_pm = True
        for m in mods:
            ok_pm = postmessage_key_to_hwnd(hwnd, m, down=True) and ok_pm
        for main in mains or ([vks[-1]] if vks and not mains else []):
            ok_pm = postmessage_key_to_hwnd(hwnd, main, down=True) and ok_pm
            ok_pm = postmessage_key_to_hwnd(hwnd, main, down=False) and ok_pm
        for m in reversed(mods):
            ok_pm = postmessage_key_to_hwnd(hwnd, m, down=False) and ok_pm
        if ok_pm:
            time.sleep(0.05)
            return {
                "ok": True,
                "via": "postmessage",
                "keys": names,
                "hwnd": hwnd,
                "tentative": True,
                "note": "PostMessage 仅表示消息已入队，须结合画面观察确认",
            }

    # SendInput（组合热键必经）——先多策略捕获目标窗前台，再发送（失败才报错，不「消极取消」）
    reclaim = None
    if hwnd:
        reclaim = reclaim_foreground_hwnd(hwnd, retries=4)
        if not reclaim.get("ok"):
            return {
                "ok": False,
                "via": "focus_reclaim_failed",
                "error": reclaim.get("error")
                or "未能捕获目标窗口前台，热键未发送",
                "keys": names,
                "hwnd": hwnd,
                "reclaim": reclaim,
                "suggestion": "请确认目标应用已打开；可再调 windows_focus_app 后重试。",
            }
    try:
        import ctypes
        from ctypes import wintypes

        user32 = _user32()

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]

        def _key(vk: int, down: bool) -> None:
            inp = INPUT()
            inp.type = 1
            inp.ki = KEYBDINPUT(vk, 0, 0 if down else 0x0002, 0, None)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        resolved = []
        for n in names:
            if n in vkm:
                resolved.append(vkm[n])
            elif len(n) == 1:
                resolved.append(ord(n.upper()))
        if not resolved:
            return {"ok": False, "via": "sendinput", "error": f"unparsed keys: {names}"}
        mods = []
        main = None
        for n, vk in zip(names, resolved):
            if n in ("ctrl", "control", "shift", "alt", "win"):
                mods.append(vk)
            else:
                main = vk
        if mods and not main:
            return {
                "ok": False,
                "via": "sendinput",
                "error": "不能只按修饰键（如单独 Ctrl）；请使用完整组合键 Ctrl+F",
                "keys": names,
            }
        # 发送前再确认/抢一次，防止刚被浏览器抢回
        if hwnd and int(get_foreground_hwnd() or 0) != hwnd:
            reclaim2 = reclaim_foreground_hwnd(hwnd, retries=2)
            if not reclaim2.get("ok"):
                return {
                    "ok": False,
                    "via": "focus_reclaim_failed",
                    "error": "热键发送前目标窗又失前台，已中止",
                    "keys": names,
                    "hwnd": hwnd,
                    "reclaim": reclaim2,
                }
        for m in mods:
            _key(m, True)
            time.sleep(0.02)
        if main:
            _key(main, True)
            time.sleep(0.02)
            _key(main, False)
        for m in reversed(mods):
            _key(m, False)
            time.sleep(0.01)
        time.sleep(0.05)
        return {
            "ok": True,
            "via": "sendinput" if has_modifier else "sendinput_fallback",
            "keys": names,
            "hwnd": hwnd,
            "fg_captured": True,
            "reclaim": reclaim,
        }
    except Exception as e:
        return {"ok": False, "via": "sendinput_fallback", "error": str(e)[:200]}


def deliver_text_to_hwnd(
    hwnd: int, text: str, *, clear: bool = False, force_paste: bool = False
) -> dict:
    """
    向目标窗口输入文本。

    Qt 微信等：优先 WM_CHAR（实测剪贴板/SendInput 进不了搜索框）；
    其它应用：UIA → 剪贴板 → SendInput。
    """
    hwnd = int(hwnd or 0)
    raw = str(text if text is not None else "")
    if clear and hwnd:
        deliver_keys_to_hwnd(hwnd, ["ctrl", "a"])
        time.sleep(0.04)
        deliver_keys_to_hwnd(hwnd, ["delete"])
        time.sleep(0.04)

    # 1) Qt/微信：中文优先 Ctrl+V；ASCII 优先 WM_CHAR
    if hwnd:
        try:
            title, cls = _hwnd_title_class(hwnd)
            blob = f"{title} {cls}".lower()
            prefer_qt = force_paste or any(
                k in blob for k in ("微信", "wechat", "weixin", "qt515", "qt5")
            )
        except Exception:
            prefer_qt = bool(force_paste)
        if prefer_qt:
            return deliver_text_strategies(
                hwnd, raw, clear=False, prefer_qt=True
            )

    if (not force_paste) and hwnd and uia_set_value_in_hwnd(hwnd, raw):
        return {"ok": True, "via": "uia_value", "hwnd": hwnd, "text_length": len(raw)}
    if hwnd:
        try:
            if int(get_foreground_hwnd() or 0) != hwnd:
                force_focus_hwnd(hwnd)
        except Exception:
            force_focus_hwnd(hwnd)
    # 2) 仍优先 WM_CHAR（非微信窗口也常比 SendInput 稳）
    if hwnd:
        r = postmessage_type_text_to_hwnd(hwnd, raw)
        if r.get("ok"):
            return r
    try:
        use_paste = bool(force_paste) or any(ord(c) > 127 for c in raw) or len(raw) > 8
        if use_paste:
            _paste_unicode_via_clipboard(raw)
            return {
                "ok": True,
                "via": "clipboard_paste",
                "hwnd": hwnd,
                "text_length": len(raw),
                "fallback": True,
            }
        sendinput_type_text(raw)
        return {
            "ok": True,
            "via": "sendinput_fallback",
            "hwnd": hwnd,
            "text_length": len(raw),
            "fallback": True,
        }
    except Exception as e:
        return {"ok": False, "via": "none", "error": str(e)[:200], "hwnd": hwnd}


def get_foreground_hwnd() -> int:
    try:
        return int(_user32().GetForegroundWindow() or 0)
    except Exception:
        return 0


def _hwnd_title_class(hwnd: int) -> Tuple[str, str]:
    if not hwnd:
        return "", ""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = (buff.value or "").strip()
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        return title, (cls.value or "").strip()
    except Exception:
        return "", ""


def _enum_visible_windows() -> List[Tuple[int, str, str]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rows: List[Tuple[int, str, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title, cls = _hwnd_title_class(int(hwnd))
        if title or cls:
            rows.append((int(hwnd), title, cls))
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:
        pass
    return rows


def _enum_visible_window_titles() -> List[str]:
    return [t for _h, t, _c in _enum_visible_windows() if t]


_EXPLORER_WINDOW_CLASSES = frozenset(
    {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"}
)

_EFFECT_TITLE_ALIASES: dict[str, list[str]] = {
    "控制面板": ["控制面板", "Control Panel", "设置", "Settings", "所有控制面板项"],
    "设置": ["设置", "Settings", "控制面板", "Control Panel"],
    "回收站": ["回收站", "Recycle Bin", "$Recycle.Bin"],
    "此电脑": ["此电脑", "This PC", "Computer"],
    "计算机": ["此电脑", "This PC", "Computer", "计算机"],
    # 浏览器别名（窗口标题通常含 "Microsoft Edge" / "Google Chrome" 等）
    "edge": ["Microsoft Edge", "Edge", "msedge"],
    "msedge": ["Microsoft Edge", "Edge", "msedge"],
    "chrome": ["Google Chrome", "Chrome"],
    "google chrome": ["Google Chrome", "Chrome"],
    "firefox": ["Mozilla Firefox", "Firefox"],
    "浏览器": ["Microsoft Edge", "Google Chrome", "Chrome", "Edge", "Firefox"],
}


def _effect_keywords(keyword: str) -> List[str]:
    key = (keyword or "").strip()
    if not key:
        return []
    aliases = _EFFECT_TITLE_ALIASES.get(key, [])
    out: List[str] = []
    for item in [key, *aliases]:
        if item and item not in out:
            out.append(item)
    return out


def _title_matches(keyword: str, titles: List[str]) -> bool:
    keys = _effect_keywords(keyword)
    for title in titles:
        title_lower = title.lower() if title else ""
        for key in keys:
            if key.lower() in title_lower:
                return True
    return False


def wait_for_window_title_keyword(
    keyword: str,
    *,
    timeout: float = 8.0,
    poll: float = 0.25,
) -> bool:
    if not (keyword or "").strip():
        return True
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        if _title_matches(keyword, _enum_visible_window_titles()):
            return True
        time.sleep(poll)
    return False


def wait_for_desktop_change(
    *,
    timeout_ms: int = 8000,
    poll_interval_ms: int = 400,
    title_filter: str = "",
    require_foreground_change: bool = True,
) -> dict:
    """等待桌面窗口集合或前台窗口变化（借鉴 allcanuse wait_for_desktop_change）。"""
    import time as _time

    timeout_s = max(0.3, float(timeout_ms) / 1000.0)
    poll_s = max(0.1, float(poll_interval_ms) / 1000.0)
    fg0 = int(get_foreground_hwnd() or 0)
    titles0 = set(_enum_visible_window_titles())
    deadline = _time.time() + timeout_s
    filt = (title_filter or "").strip().lower()
    while _time.time() < deadline:
        fg1 = int(get_foreground_hwnd() or 0)
        titles1 = set(_enum_visible_window_titles())
        fg_changed = require_foreground_change and fg1 != fg0
        set_changed = titles1 != titles0
        filter_hit = False
        if filt:
            filter_hit = any(filt in (t or "").lower() for t in titles1)
        if filter_hit or fg_changed or set_changed:
            return {
                "ok": True,
                "success": True,
                "changed": True,
                "fg_changed": fg_changed,
                "titles_changed": set_changed,
                "filter_hit": filter_hit,
                "foreground_hwnd": fg1,
                "title_count": len(titles1),
            }
        _time.sleep(poll_s)
    return {
        "ok": False,
        "success": False,
        "changed": False,
        "error": "等待桌面变化超时",
        "suggestion": "可增大 timeout_ms，或改用 windows_wait(condition='stable')。",
    }


def _any_new_app_foreground(fg_before: int) -> bool:
    """双击后前台变为非桌面/任务栏窗口，视为应用已打开。"""
    fg = get_foreground_hwnd()
    if not fg or fg == fg_before:
        return False
    _t, cls = _hwnd_title_class(fg)
    if cls in (
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "DV2ControlHost",
    ):
        return False
    return bool(_t or cls)


def _explorer_window_opened(
    fg_before: int,
    keyword: str = "",
    *,
    hwnds_before: Optional[set] = None,
) -> bool:
    """桌面文件夹类图标：新出现的资源管理器(CabinetWClass)窗口视为生效。"""
    keys = _effect_keywords(keyword)
    known = hwnds_before or set()
    saw_new_cabinet = False
    for hwnd, title, cls in _enum_visible_windows():
        if cls not in _EXPLORER_WINDOW_CLASSES or cls in ("Progman", "WorkerW"):
            continue
        if cls == "CabinetWClass" or cls == "ExploreWClass":
            if hwnd in known:
                continue
            saw_new_cabinet = True
            if not keys:
                if hwnd != fg_before:
                    return True
            else:
                for key in keys:
                    if key in title:
                        return True
    fg = get_foreground_hwnd()
    if fg and fg != fg_before and fg not in known:
        _t, cls = _hwnd_title_class(fg)
        if cls in ("CabinetWClass", "ExploreWClass"):
            if not keys or _title_matches(keyword, [_t]):
                return True
    if keys and saw_new_cabinet:
        for hwnd, _title, cls in _enum_visible_windows():
            if cls == "CabinetWClass" and hwnd not in known and hwnd != fg_before:
                return True
    return False


def should_verify_desktop_effect(
    spec: Optional[dict],
    *,
    action: str = "",
) -> bool:
    """
    是否在指针动作后校验「界面效果」。

    - 显式 verify_effect 优先
    - 双击 / 启动类、或 desktop_spec 声明 expect_window/effect_keyword/desktop_shell 时默认校验
    - 普通单击/右键（应用内按钮）默认不校验「新窗口标题」——否则 QQ「扫码登录」等
      会因标题未变而被误判失败
    """
    s = spec or {}
    raw = s.get("verify_effect")
    if raw is not None:
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() not in ("0", "false", "no", "off")
    if (
        s.get("desktop_shell")
        or s.get("expect_window")
        or (s.get("effect_keyword") or "").strip()
        or (s.get("target_name") or "").strip()
    ):
        return True
    act = (action or "").strip().lower()
    if act in ("double_click", "doubleclick", "launch_app"):
        return True
    return False


def wait_for_desktop_effect(
    keyword: str,
    *,
    fg_before: int = 0,
    timeout: float = 8.0,
    poll: float = 0.25,
    desktop_shell: bool = False,
    require_verify: bool = True,
    titles_before: Optional[set] = None,
    hwnds_before: Optional[set] = None,
) -> bool:
    """
    操作后等待界面变化：仅匹配操作后新出现的窗口标题，避免误判已打开窗口。
    """
    if not require_verify:
        return True
    if not (keyword or "").strip() and not desktop_shell:
        fg = get_foreground_hwnd()
        if fg and fg != fg_before:
            return True
        return False
    baseline_titles = titles_before if titles_before is not None else set(_enum_visible_window_titles())
    baseline_hwnds = hwnds_before if hwnds_before is not None else {h for h, _t, _c in _enum_visible_windows()}
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        current_rows = _enum_visible_windows()
        new_titles = [t for _h, t, _c in current_rows if t and t not in baseline_titles]
        if (keyword or "").strip() and _title_matches(keyword, new_titles):
            return True
        if desktop_shell and _explorer_window_opened(
            fg_before, keyword, hwnds_before=baseline_hwnds
        ):
            return True
        if desktop_shell and not (keyword or "").strip():
            if _any_new_app_foreground(fg_before):
                return True
        fg = get_foreground_hwnd()
        if fg and fg != fg_before and fg not in baseline_hwnds and (keyword or "").strip():
            fg_title, _cls = _hwnd_title_class(fg)
            if fg_title and _title_matches(keyword, [fg_title]):
                return True
        time.sleep(poll)
    return False


_SPURIOUS_TARGET_NAMES = frozenset({
    "folderview",
    "桌面",
    "desktop",
    "桌面 1",
    "syslistview32",
    "shelldll_defview",
    "directuihwnd",
})


def infer_effect_keyword(
    spec: Optional[dict],
    description: str = "",
) -> str:
    s = spec or {}
    name = (s.get("target_name") or "").strip()
    if name and name.strip().lower() not in _SPURIOUS_TARGET_NAMES:
        return name
    uia = s.get("uia_path")
    if isinstance(uia, list) and uia:
        last = uia[-1] if isinstance(uia[-1], dict) else {}
        n = (last.get("name") or "").strip()
        if n and n not in ("桌面", "Desktop"):
            return n
    m = re.search(r"「([^」]+)」", description or "")
    if m:
        return (m.group(1) or "").strip()
    return ""
