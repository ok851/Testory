# -*- coding: utf-8 -*-
"""安装目录 / PyInstaller 冻结包路径解析（桌面离线安装包）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def resolve_install_root() -> Path:
    """
    用户可见的安装根目录（含 templates、runtime、playwright-browsers）。
    冻结后端：{app}/runtime/testory_app/TestoryBackend.exe → 上溯三级。
    """
    env = (os.environ.get("TESTORY_INSTALL_ROOT") or "").strip()
    if env:
        return Path(env)

    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        parent = exe.parent
        if parent.name == "testory_app" and parent.parent.name == "runtime":
            return parent.parent.parent
        if parent.name in ("TestoryEmbeddedGw", "TestoryDesktopGw"):
            return parent.parent.parent
        return parent.parent if parent.parent.is_dir() else parent

    return Path(__file__).resolve().parent


def configure_install_root_env() -> Path:
    """写入 TESTORY_INSTALL_ROOT 与 Playwright 浏览器路径。"""
    root = resolve_install_root()
    os.environ["TESTORY_INSTALL_ROOT"] = str(root.resolve())
    browsers = root / "playwright-browsers"
    if browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers.resolve())
        os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    return root


def resource_root() -> Path:
    """Flask 模板/静态资源目录：优先安装根目录，否则 PyInstaller _MEIPASS。"""
    root = resolve_install_root()
    if (root / "templates").is_dir() and (root / "static").is_dir():
        return root
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        if (mp / "templates").is_dir():
            return mp
    return root


def protected_backend_exe() -> Optional[Path]:
    root = resolve_install_root()
    for rel in (
        "runtime/testory_app/TestoryBackend.exe",
        "runtime/testory_app/testory_backend.exe",
    ):
        p = root / rel.replace("/", os.sep)
        if p.is_file():
            return p
    return None


def helper_executable(folder_name: str, exe_name: Optional[str] = None) -> Optional[Path]:
    """查找随安装包分发的辅助进程（网关等）。"""
    root = resolve_install_root()
    stem = exe_name or folder_name
    for rel in (
        f"runtime/{folder_name}/{stem}.exe",
        f"runtime/{folder_name}/{stem}",
    ):
        p = root / rel.replace("/", os.sep)
        if p.is_file():
            return p
    return None
