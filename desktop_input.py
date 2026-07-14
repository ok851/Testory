# -*- coding: utf-8 -*-
"""Windows 屏幕级鼠标注入与操作后效果校验。"""

from __future__ import annotations

import re
import sys
import os
import time
from typing import List, Optional, Tuple

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
        from desktop_hybrid_locator import element_snapshot_for_step

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
    """Unicode 文本输入（SendInput KEYEVENTF_UNICODE）。"""
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

    for ch in str(text or ""):
        for flag in (0x0004, 0x0006):
            inp = INPUT()
            inp.type = 1
            inp.ki = KEYBDINPUT(0, ord(ch), flag, 0, None)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            time.sleep(0.01)


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
    title_sub = (s.get("window_title") or "").strip()
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
        elif title_sub and title_sub in (wt or ""):
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
        for key in keys:
            if key in title:
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


def should_verify_desktop_effect(spec: Optional[dict]) -> bool:
    """双击回放默认校验界面变化；仅显式 verify_effect=0 时跳过。"""
    s = spec or {}
    raw = s.get("verify_effect")
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


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
