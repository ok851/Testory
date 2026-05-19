# -*- coding: utf-8 -*-
"""
可选 pywebview 壳原型：加载本地 Flask UI。
需: pip install pywebview
用法: python packaging/uat_tray_webview.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:5000"


def _start_flask() -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("DEPLOYMENT_PROFILE", "local")
    env.setdefault("DESKTOP_EXECUTION_MODE", "inprocess")
    env.setdefault("PLAYWRIGHT_HEADLESS", "0")
    return subprocess.Popen(
        [sys.executable, str(ROOT / "app.py")],
        cwd=str(ROOT),
        env=env,
    )


def main() -> None:
    proc = _start_flask()
    for _ in range(60):
        time.sleep(0.5)
        try:
            import urllib.request

            urllib.request.urlopen(URL, timeout=1)
            break
        except Exception:
            pass

    try:
        import webview
    except ImportError:
        print("请安装: pip install pywebview")
        proc.terminate()
        sys.exit(1)

    def on_closed():
        try:
            proc.terminate()
        except Exception:
            pass

    window = webview.create_window("HuFirst UAT", URL, width=1280, height=800)
    webview.start(on_closed)


if __name__ == "__main__":
    main()
