# -*- coding: utf-8 -*-
"""
Testory 原生桌面壳（Native Desktop Shell）

架构分层：
  1. 启动器 Testory.exe
  2. 桌面壳 desktop_shell（本模块）
  3. 本地服务 Flask（127.0.0.1）
  4. 内嵌 WebView2 界面
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .win_app_icon import apply_window_icon_async, resolve_icon_path, set_process_app_user_model_id
from .window_state import resolve_window_geometry, save_window_state

APP_TITLE = "Testory"
DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 900
MIN_WIDTH = 1024
MIN_HEIGHT = 640

# Win32：无边框窗口仍可拖拽边框缩放；最大化限制在工作区（不盖任务栏）
_GWL_STYLE = -16
_WS_THICKFRAME = 0x00040000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_WS_SYSMENU = 0x00080000
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020
_SWP_SHOWWINDOW = 0x0040
_MONITOR_DEFAULTTONEAREST = 2


def splash_boot_uri(root: Path) -> str:
    boot = root / "static" / "desktop" / "shell_boot.html"
    if boot.is_file():
        return boot.resolve().as_uri()
    return "about:blank"


def _frameless_enabled() -> bool:
    raw = (os.environ.get("TESTORY_FRAMELESS_SHELL") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _show_error(msg: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, msg, APP_TITLE, 0x10)
            return
        except Exception:
            pass
    print(msg, file=sys.stderr)


def _filter_create_window_kwargs(create_window: Callable[..., Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """过滤当前 pywebview 不支持的参数，避免打包环境版本差异导致启动崩溃。"""
    try:
        params = inspect.signature(create_window).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


def _get_work_area_for_hwnd(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """返回 (left, top, width, height) 工作区像素（不含任务栏）。"""
    if sys.platform != "win32" or not hwnd:
        return None
    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    work = info.rcWork
    return (
        int(work.left),
        int(work.top),
        int(work.right - work.left),
        int(work.bottom - work.top),
    )


def _find_shell_hwnd(title: str = APP_TITLE) -> int:
    if sys.platform != "win32":
        return 0
    import ctypes

    user32 = ctypes.windll.user32
    found = ctypes.c_void_p(0)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if buf.value == title:
            found.value = hwnd
            return False
        return True

    user32.EnumWindows(_enum, 0)
    return int(found.value or 0)


def _enable_frameless_border_resize(hwnd: int) -> None:
    """无边框窗口补回 WS_THICKFRAME，允许拖边框缩放。"""
    if sys.platform != "win32" or not hwnd:
        return
    import ctypes

    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
    style |= _WS_THICKFRAME | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX | _WS_SYSMENU
    user32.SetWindowLongW(hwnd, _GWL_STYLE, style)
    user32.SetWindowPos(
        hwnd,
        0,
        0,
        0,
        0,
        0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED | _SWP_SHOWWINDOW,
    )


def _set_winforms_maximized_bounds(hwnd: int) -> None:
    """限制 WinForms 最大化到工作区，避免盖住任务栏。"""
    if sys.platform != "win32" or not hwnd:
        return
    area = _get_work_area_for_hwnd(hwnd)
    if not area:
        return
    left, top, width, height = area
    try:
        from webview.platforms import winforms as wf

        form = None
        for inst in getattr(wf.BrowserView, "instances", {}).values():
            try:
                if int(inst.Handle.ToInt32()) == int(hwnd):
                    form = inst
                    break
            except Exception:
                continue
        if form is None:
            return
        from System.Drawing import Rectangle  # type: ignore

        form.MaximizedBounds = Rectangle(left, top, width, height)
    except Exception:
        pass


def _maximize_to_work_area(hwnd: int) -> bool:
    """将窗口铺满当前显示器工作区（不覆盖任务栏）。"""
    area = _get_work_area_for_hwnd(hwnd)
    if not area:
        return False
    left, top, width, height = area
    import ctypes

    ctypes.windll.user32.SetWindowPos(
        hwnd,
        0,
        left,
        top,
        width,
        height,
        _SWP_NOZORDER | _SWP_SHOWWINDOW,
    )
    return True


def _apply_shell_chrome(frameless: bool) -> None:
    hwnd = _find_shell_hwnd(APP_TITLE)
    if not hwnd:
        return
    if frameless:
        _enable_frameless_border_resize(hwnd)
    _set_winforms_maximized_bounds(hwnd)


class DesktopWindowApi:
    """pywebview JS API：窗口控制与状态持久化。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._maximized = False
        self._restore_bounds: Optional[Tuple[int, int, int, int]] = None

    def minimize(self) -> None:
        import webview

        if webview.windows:
            webview.windows[0].minimize()

    def toggle_maximize(self) -> bool:
        import webview

        if not webview.windows:
            return False
        win = webview.windows[0]
        self._maximized = not self._maximized
        hwnd = _find_shell_hwnd(APP_TITLE)
        if self._maximized:
            if hwnd:
                try:
                    import ctypes
                    from ctypes import wintypes

                    class RECT(ctypes.Structure):
                        _fields_ = [
                            ("left", wintypes.LONG),
                            ("top", wintypes.LONG),
                            ("right", wintypes.LONG),
                            ("bottom", wintypes.LONG),
                        ]

                    rect = RECT()
                    if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        self._restore_bounds = (
                            int(rect.left),
                            int(rect.top),
                            int(rect.right - rect.left),
                            int(rect.bottom - rect.top),
                        )
                except Exception:
                    self._restore_bounds = None
                if _maximize_to_work_area(hwnd):
                    return self._maximized
            try:
                win.maximize()
            except Exception:
                pass
        else:
            if hwnd and self._restore_bounds:
                x, y, w, h = self._restore_bounds
                try:
                    import ctypes

                    ctypes.windll.user32.SetWindowPos(
                        hwnd,
                        0,
                        x,
                        y,
                        w,
                        h,
                        _SWP_NOZORDER | _SWP_SHOWWINDOW,
                    )
                    return self._maximized
                except Exception:
                    pass
            try:
                win.restore()
            except Exception:
                pass
        return self._maximized

    def close(self) -> None:
        import webview

        if webview.windows:
            webview.windows[0].destroy()

    def save_geometry(self, payload_json: str = "") -> None:
        import webview

        if not webview.windows:
            return
        win = webview.windows[0]
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            payload = {}
        save_window_state(
            width=int(payload.get("width") or getattr(win, "width", DEFAULT_WIDTH) or DEFAULT_WIDTH),
            height=int(payload.get("height") or getattr(win, "height", DEFAULT_HEIGHT) or DEFAULT_HEIGHT),
            x=payload.get("x"),
            y=payload.get("y"),
            maximized=bool(payload.get("maximized", self._maximized)),
        )


def _configure_webview2_runtime() -> None:

    if sys.platform != "win32":
        return
    flag = "--enable-features=WebCodecs"
    key = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
    extra = (os.environ.get(key) or "").strip()
    if flag in extra:
        return
    os.environ[key] = f"{extra} {flag}".strip()


def run_native_shell(
    *,
    root: Path,
    app_url: str,
    wait_until_ready: Callable[[], bool],
    startup_failed_message: Callable[[], str],
    on_closed: Callable[[], None],
) -> int:
    _configure_webview2_runtime()
    try:
        import webview
    except ImportError:
        exe = Path(sys.executable).resolve()
        msg = "缺少桌面界面组件 pywebview。\n请重新安装完整安装包。"
        if exe.name.lower() == "testoryshell.exe" and exe.parent == root.resolve():
            msg = (
                "无法加载 pywebview：请勿使用安装根目录的 TestoryShell.exe 启动。\n"
                "请从开始菜单运行 Testory（Testory.exe）。"
            )
        else:
            msg += f"\n\n当前解释器：{exe}"
        _show_error(msg)
        return 1

    width, height, maximized, pos_x, pos_y = resolve_window_geometry(
        default_width=DEFAULT_WIDTH,
        default_height=DEFAULT_HEIGHT,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )
    splash = splash_boot_uri(root)
    gui = "edgechromium" if sys.platform == "win32" else None
    frameless = _frameless_enabled()
    set_process_app_user_model_id()
    apply_window_icon_async(root, APP_TITLE)
    icon_path = resolve_icon_path(root)

    api = DesktopWindowApi(root)
    create_kwargs = dict(
        title=APP_TITLE,
        url=splash,
        width=width,
        height=height,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        text_select=True,
        confirm_close=False,
        js_api=api,
        frameless=frameless,
        easy_drag=frameless,
        resizable=True,
        maximized=False,
    )
    # 旧代码曾把 icon 传给 create_window；pywebview 6 仅支持 start(icon=...)
    create_kwargs = _filter_create_window_kwargs(webview.create_window, create_kwargs)

    window = webview.create_window(**create_kwargs)

    if pos_x is not None and pos_y is not None and not maximized:
        try:
            window.move(pos_x, pos_y)
        except Exception:
            pass

    backend_ready = {"ok": False}
    chrome_applied = {"ok": False}

    def _ensure_chrome() -> None:
        if chrome_applied["ok"]:
            return
        try:
            _apply_shell_chrome(frameless)
            chrome_applied["ok"] = True
        except Exception:
            pass

    def bootstrap() -> None:
        win = webview.windows[0] if webview.windows else window
        deadline = time.time() + 120.0
        while time.time() < deadline:
            _ensure_chrome()
            if wait_until_ready():
                backend_ready["ok"] = True
                try:
                    win.load_url(app_url)
                except Exception as exc:
                    _show_error(f"无法加载应用界面：{exc}")
                    try:
                        win.destroy()
                    except Exception:
                        pass
                if maximized:
                    api._maximized = True
                    hwnd = _find_shell_hwnd(APP_TITLE)
                    if hwnd:
                        _maximize_to_work_area(hwnd)
                    else:
                        try:
                            win.maximize()
                        except Exception:
                            pass
                return
            time.sleep(0.35)
        _show_error(startup_failed_message())
        try:
            win.destroy()
        except Exception:
            pass

    def _persist_on_close() -> None:
        try:
            if webview.windows:
                win = webview.windows[0]
                save_window_state(
                    width=int(getattr(win, "width", width) or width),
                    height=int(getattr(win, "height", height) or height),
                    maximized=maximized or api._maximized,
                )
        except Exception:
            pass

    def _closed_handler() -> None:
        _persist_on_close()
        on_closed()

    def _shown_handler() -> None:
        _ensure_chrome()

    try:
        window.events.closed += _closed_handler
    except Exception:
        pass
    try:
        window.events.shown += _shown_handler
    except Exception:
        pass

    start_kwargs: Dict[str, Any] = {"gui": gui}
    if icon_path is not None:
        start_kwargs["icon"] = str(icon_path)
    try:
        start_params = inspect.signature(webview.start).parameters
        start_kwargs = {k: v for k, v in start_kwargs.items() if k in start_params}
    except (TypeError, ValueError):
        pass

    webview.start(bootstrap, **start_kwargs)
    if not backend_ready["ok"]:
        on_closed()
    return 0
