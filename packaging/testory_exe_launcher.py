# -*- coding: utf-8 -*-
"""Testory.exe — 安装目录根下的轻量启动器（PyInstaller onefile，不含业务代码）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_TITLE = "Testory"
CREATE_NO_WINDOW = 0x08000000


def install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _show_error(msg: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, msg, APP_TITLE, 0x10)
            return
        except Exception:
            pass
    print(msg, file=sys.stderr)


def main() -> int:
    root = install_root()
    os.chdir(root)

    script = root / "packaging" / "uat_desktop.py"
    if not script.is_file():
        _show_error(f"未找到程序文件：\n{script}\n\n请重新安装 Testory。")
        return 1

    pyw = root / ".venv" / "Scripts" / "pythonw.exe"
    py = root / ".venv" / "Scripts" / "python.exe"
    interpreter = pyw if pyw.is_file() else py
    if not interpreter.is_file():
        _show_error("未找到内置 Python 环境。\n请使用完整安装包重新安装 Testory。")
        return 1

    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    subprocess.Popen(
        [str(interpreter), str(script)],
        cwd=str(root),
        creationflags=flags,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
