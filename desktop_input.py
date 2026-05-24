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
        user32.PostMessageW(hwnd, 0x0203, 1, lparam)  # WM_LBUTTONDBLCLK
    else:
        user32.PostMessageW(hwnd, 0x0201, 1, lparam)  # WM_LBUTTONDOWN
        user32.PostMessageW(hwnd, 0x0202, 0, lparam)  # WM_LBUTTONUP
    return hwnd


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
    """统一点击入口：默认消息点击不抢鼠标；force_physical 或 DESKTOP_PHYSICAL_MOUSE=1 时移动真实光标。"""
    use_physical = force_physical or physical_mouse_enabled()
    act = (action or "click").strip().lower()
    if act == "double_click":
        if use_physical:
            saved = _save_cursor_pos()
            try:
                screen_double_click(int(x), int(y))
            finally:
                if restore_cursor_after_pointer(force_physical=force_physical):
                    _restore_cursor_pos(*saved)
        else:
            return message_click_at_screen(int(x), int(y), double=True)
        return 0
    if act == "right_click":
        if use_physical:
            saved = _save_cursor_pos()
            try:
                screen_right_click(int(x), int(y))
            finally:
                if restore_cursor_after_pointer(force_physical=force_physical):
                    _restore_cursor_pos(*saved)
        else:
            return message_click_at_screen(int(x), int(y), right=True)
        return 0
    if use_physical:
        saved = _save_cursor_pos()
        try:
            screen_click(int(x), int(y))
        finally:
            if restore_cursor_after_pointer(force_physical=force_physical):
                _restore_cursor_pos(*saved)
    else:
        return message_click_at_screen(int(x), int(y), double=False)
    return 0


_SHELL_ROOT_CLASSES = frozenset({"Progman", "WorkerW", "#32769"})
_SHELL_VIEW_CLASSES = frozenset(
    {"SHELLDLL_DefView", "SysListView32", "DirectUIHWND", "FolderView"}
)


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


def verify_pointer_delivered(
    x: int,
    y: int,
    *,
    desktop_shell: bool = False,
    physical: bool = False,
) -> bool:
    """
    坐标点击送达校验：确认坐标处为桌面/目标窗口，不校验资源管理器是否打开。
    物理点击另要求光标曾到达目标坐标（由 screen_*_click 保证）。
    """
    hwnd = hwnd_at_screen_point(int(x), int(y))
    if not hwnd:
        return False
    if desktop_shell:
        return is_desktop_shell_hwnd(hwnd)
    return bool(physical or hwnd)


def screen_size() -> tuple[int, int]:
    u = _user32()
    return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))


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
        u = _user32()
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


def _explorer_window_opened(fg_before: int, keyword: str = "") -> bool:
    """桌面文件夹类图标：资源管理器(CabinetWClass)打开即视为生效。"""
    keys = _effect_keywords(keyword)
    saw_cabinet = False
    for hwnd, title, cls in _enum_visible_windows():
        if cls not in _EXPLORER_WINDOW_CLASSES or cls in ("Progman", "WorkerW"):
            continue
        if cls == "CabinetWClass" or cls == "ExploreWClass":
            saw_cabinet = True
            if not keys:
                if hwnd != fg_before:
                    return True
            else:
                for key in keys:
                    if key in title:
                        return True
    fg = get_foreground_hwnd()
    if fg and fg != fg_before:
        _t, cls = _hwnd_title_class(fg)
        if cls in ("CabinetWClass", "ExploreWClass"):
            if not keys or _title_matches(keyword, [_t]):
                return True
    if keys and saw_cabinet:
        for hwnd, _title, cls in _enum_visible_windows():
            if cls == "CabinetWClass" and hwnd != fg_before:
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
) -> bool:
    """
    操作后等待界面变化：标题匹配；桌面图标另接受资源管理器窗口。
    """
    if not require_verify:
        return True
    if not (keyword or "").strip() and not desktop_shell:
        return False
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        if (keyword or "").strip() and _title_matches(keyword, _enum_visible_window_titles()):
            return True
        if desktop_shell and _explorer_window_opened(fg_before, keyword):
            return True
        if desktop_shell and not (keyword or "").strip():
            if _any_new_app_foreground(fg_before):
                return True
        fg = get_foreground_hwnd()
        if fg and fg != fg_before and (keyword or "").strip():
            fg_title, _cls = _hwnd_title_class(fg)
            if fg_title and _title_matches(keyword, [fg_title]):
                return True
        time.sleep(poll)
    return False


def infer_effect_keyword(
    spec: Optional[dict],
    description: str = "",
) -> str:
    s = spec or {}
    name = (s.get("target_name") or "").strip()
    if name:
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
