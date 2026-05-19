# -*- coding: utf-8 -*-
"""PyInstaller 入口：设置本地版环境并启动 app.py。"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    os.chdir(ROOT)
    os.environ.setdefault("DEPLOYMENT_PROFILE", "local")
    os.environ.setdefault("DESKTOP_EXECUTION_MODE", "inprocess")
    os.environ.setdefault("PLAYWRIGHT_HEADLESS", "0")
    os.environ.setdefault("DESKTOP_AUTO_START_GATEWAY", "0")
    app = ROOT / "app.py"
    subprocess.call([sys.executable, str(app)])


if __name__ == "__main__":
    main()
