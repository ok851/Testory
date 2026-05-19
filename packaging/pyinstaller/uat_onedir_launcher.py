# -*- coding: utf-8 -*-
"""PyInstaller onedir 启动器：设置 Playwright 浏览器路径并启动 app.py。"""
import os
import subprocess
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    BUNDLE = Path(sys.executable).resolve().parent
else:
    BUNDLE = Path(__file__).resolve().parent.parent.parent

ROOT = BUNDLE.parent if (BUNDLE / "app.py").is_file() is False and (BUNDLE.parent / "app.py").is_file() else BUNDLE
if not (ROOT / "app.py").is_file():
    ROOT = BUNDLE.parent.parent

browsers = BUNDLE / "playwright-browsers"
if browsers.is_dir():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"

os.environ.setdefault("DEPLOYMENT_PROFILE", "local")
os.environ.setdefault("DESKTOP_EXECUTION_MODE", "inprocess")
os.environ.setdefault("PLAYWRIGHT_HEADLESS", "0")

app = ROOT / "app.py"
if not app.is_file():
    print(f"未找到 app.py: {app}", file=sys.stderr)
    sys.exit(1)
os.chdir(ROOT)
raise SystemExit(subprocess.call([sys.executable, str(app)]))
