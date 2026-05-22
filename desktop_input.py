# -*- coding: utf-8 -*-
"""Windows 屏幕级鼠标注入与操作后效果校验。"""

from __future__ import annotations

import re
import sys
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
    screen_click(x, y)
    time.sleep(0.12)
    screen_click(x, y)


def screen_right_click(x: int, y: int) -> None:
    screen_click(x, y, right=True)


def focus_hwnd(hwnd: int) -> None:
    if not hwnd:
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
    # 标题可能为空或与别名不完全一致：桌面 shell 双击后出现新的 Cabinet 即视为打开
    if keys and saw_cabinet:
        for hwnd, _title, cls in _enum_visible_windows():
            if cls == "CabinetWClass" and hwnd != fg_before:
                return True
    return False


def should_verify_desktop_effect(spec: Optional[dict]) -> bool:
    """运行期是否校验双击效果；设计期可在拾取器校验，回放默认对 shell 图标放宽。"""
    s = spec or {}
    raw = s.get("verify_effect")
    if raw is None:
        return not bool(s.get("surface") == "desktop_shell")
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
        return True
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        if (keyword or "").strip() and _title_matches(keyword, _enum_visible_window_titles()):
            return True
        if desktop_shell and _explorer_window_opened(fg_before, keyword):
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
