# -*- coding: utf-8 -*-
"""Testory.exe — 安装目录根下的轻量启动器（PyInstaller onefile，不含业务代码）。"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

APP_TITLE = "Testory"
CREATE_NO_WINDOW = 0x08000000
STARTUP_GRACE_SEC = 4.0


def _load_testory_runtime():
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        mod_path = base / "testory_runtime.py"
    else:
        mod_path = Path(__file__).resolve().parent / "testory_runtime.py"
    spec = importlib.util.spec_from_file_location("testory_runtime", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def _child_exited_too_soon(proc: subprocess.Popen) -> bool:
    deadline = time.time() + STARTUP_GRACE_SEC
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.25)
    return proc.poll() is not None


def _diagnose_launch_failure(root: Path, interpreter: Path, script: Path) -> str:
    console = interpreter
    if interpreter.name.lower() == "pythonw.exe":
        candidate = interpreter.with_name("python.exe")
        if candidate.is_file():
            console = candidate
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    probe = subprocess.run(
        [str(console), str(script), "--probe"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=flags,
    )
    detail = (probe.stderr or probe.stdout or "").strip()
    if detail:
        return detail
    if probe.returncode != 0:
        return f"桌面进程退出，代码 {probe.returncode}"
    return "桌面进程启动后立即退出。"


def main() -> int:
    runtime = _load_testory_runtime()
    verify_bundled_python = runtime.verify_bundled_python

    root = install_root()
    os.chdir(root)

    script = root / "packaging" / "uat_desktop.py"
    if not script.is_file():
        _show_error(f"未找到程序文件：\n{script}\n\n请重新安装 Testory。")
        return 1

    interpreter, err = verify_bundled_python(root)
    if err:
        _show_error(err)
        return 1
    assert interpreter is not None

    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [str(interpreter), str(script)],
        cwd=str(root),
        creationflags=flags,
    )
    if _child_exited_too_soon(proc):
        detail = _diagnose_launch_failure(root, interpreter, script)
        _show_error(
            f"Testory 未能启动。\n\n{detail}\n\n"
            f"日志目录：{root / 'logs'}\n"
            f"或 %LOCALAPPDATA%\\Testory\\logs"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
