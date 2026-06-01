# -*- coding: utf-8 -*-
"""
Testory 原生桌面壳（Native Desktop Shell）

架构分层（与「浏览器打开网站」不同，这是常见桌面软件模式）：
  1. 启动器 Testory.exe          — 轻量 exe，拉起桌面进程
  2. 桌面壳 desktop_shell        — 原生窗口、启动页、生命周期（本模块）
  3. 本地服务 app.py (Flask)     — 业务 API，仅监听 127.0.0.1
  4. 内嵌界面 WebView2           — 渲染已有 Web UI，用户看不到地址栏/Edge 错误页

与 Electron / Notion / Slack 桌面版同类；后续若需纯原生控件，可替换第 4 层为 Qt/WPF。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable, Optional

APP_TITLE = "Testory"


def splash_boot_uri(root: Path) -> str:
    boot = root / "static" / "desktop" / "shell_boot.html"
    if boot.is_file():
        return boot.resolve().as_uri()
    return "about:blank"


def _show_error(msg: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, msg, APP_TITLE, 0x10)
            return
        except Exception:
            pass
    print(msg, file=sys.stderr)


def run_native_shell(
    *,
    root: Path,
    app_url: str,
    wait_until_ready: Callable[[], bool],
    startup_failed_message: Callable[[], str],
    on_closed: Callable[[], None],
) -> int:
    try:
        import webview
    except ImportError:
        _show_error("缺少桌面界面组件 pywebview。\n请重新安装完整安装包。")
        return 1

    splash = splash_boot_uri(root)
    gui = "edgechromium" if sys.platform == "win32" else None

    window = webview.create_window(
        APP_TITLE,
        url=splash,
        width=1360,
        height=860,
        min_size=(1024, 640),
        text_select=True,
        confirm_close=False,
    )

    def bootstrap() -> None:
        win = webview.windows[0] if webview.windows else window
        deadline = time.time() + 120.0
        while time.time() < deadline:
            if wait_until_ready():
                try:
                    win.load_url(app_url)
                except Exception as exc:
                    _show_error(f"无法加载应用界面：{exc}")
                    try:
                        win.destroy()
                    except Exception:
                        pass
                return
            time.sleep(0.4)
        _show_error(startup_failed_message())
        try:
            win.destroy()
        except Exception:
            pass

    webview.start(bootstrap, gui=gui)
    on_closed()
    return 0
