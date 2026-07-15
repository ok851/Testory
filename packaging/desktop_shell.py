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

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .win_app_icon import apply_window_icon_async, resolve_icon_path, set_process_app_user_model_id
from .window_state import load_window_state, resolve_window_geometry, save_window_state

APP_TITLE = "Testory"
DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 900
MIN_WIDTH = 1024
MIN_HEIGHT = 640


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


class DesktopWindowApi:
    """pywebview JS API：窗口控制与状态持久化。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._maximized = False

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
        if self._maximized:
            win.maximize()
        else:
            win.restore()
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
    )

    window = webview.create_window(**create_kwargs)

    if pos_x is not None and pos_y is not None and not maximized:
        try:
            window.move(pos_x, pos_y)
        except Exception:
            pass

    backend_ready = {"ok": False}

    def bootstrap() -> None:
        win = webview.windows[0] if webview.windows else window
        deadline = time.time() + 120.0
        while time.time() < deadline:
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

    try:
        window.events.closed += _closed_handler
    except Exception:
        pass

    if maximized:
        try:
            window.maximize()
            api._maximized = True
        except Exception:
            pass

    webview.start(bootstrap, gui=gui)
    if not backend_ready["ok"]:
        on_closed()
    return 0
