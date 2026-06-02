# -*- coding: utf-8 -*-
"""Testory.exe — 安装目录根下的启动器（PyInstaller onefile，仅负责拉起内置 Python）。"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

APP_TITLE = "Testory"
CREATE_NO_WINDOW = 0x08000000
STARTUP_GRACE_SEC = 5.0


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


def _log_dir() -> Path:
    base = (os.environ.get("LOCALAPPDATA") or "").strip()
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "Testory" / "logs"


def _write_launcher_log(message: str) -> Path:
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "launcher.log"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with log_file.open("a", encoding="utf-8") as fp:
        fp.write(f"\n--- {stamp} ---\n{message}\n")
    return log_file


def _show_error(msg: str, *, log_path: Path | None = None) -> None:
    if log_path:
        msg = f"{msg}\n\n详细日志：{log_path}"
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, msg, APP_TITLE, 0x10)
            return
        except Exception:
            pass
    print(msg, file=sys.stderr)


def _desktop_child_env(root: Path) -> dict:
    env = os.environ.copy()
    env.setdefault("UAT_DESKTOP_MODE", "1")
    env.setdefault("SKIP_ENV_EXAMPLE_SYNC", "1")
    env.setdefault("PYTHONNOUSERSITE", "1")
    root_str = str(root.resolve())
    existing = env.get("PYTHONPATH", "").strip()
    if existing:
        parts = [p for p in existing.split(os.pathsep) if p]
        if root_str not in parts:
            env["PYTHONPATH"] = root_str + os.pathsep + existing
    else:
        env["PYTHONPATH"] = root_str
    return env


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
        env=_desktop_child_env(root),
        capture_output=True,
        text=True,
        timeout=90,
        creationflags=flags,
    )
    detail = (probe.stderr or probe.stdout or "").strip()
    if detail:
        return detail
    if probe.returncode != 0:
        return f"桌面进程退出，代码 {probe.returncode}"
    return "桌面进程启动后立即退出。"


def main() -> int:
    root = install_root()
    os.chdir(root)

    try:
        runtime = _load_testory_runtime()
        verify_bundled_python = runtime.verify_bundled_python

        script = root / "packaging" / "uat_desktop.py"
        if not script.is_file():
            msg = f"未找到程序文件：{script}\n请重新安装 Testory。"
            log_file = _write_launcher_log(msg)
            _show_error(msg, log_path=log_file)
            return 1

        interpreter, err = verify_bundled_python(root)
        if err:
            log_file = _write_launcher_log(err)
            _show_error(err, log_path=log_file)
            return 1
        assert interpreter is not None

        flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
        child_env = _desktop_child_env(root)
        proc = subprocess.Popen(
            [str(interpreter), str(script)],
            cwd=str(root),
            env=child_env,
            creationflags=flags,
        )
        if _child_exited_too_soon(proc):
            detail = _diagnose_launch_failure(root, interpreter, script)
            log_file = _write_launcher_log(
                f"启动失败\n安装目录: {root}\n解释器: {interpreter}\n\n{detail}"
            )
            _show_error(
                f"Testory 未能启动。\n\n{detail}\n\n"
                f"安装目录：{root}\n"
                f"日志目录：{_log_dir()}",
                log_path=log_file,
            )
            return 1
        return 0
    except Exception:
        log_file = _write_launcher_log(traceback.format_exc())
        _show_error(
            f"Testory 启动器异常。\n\n详见 launcher.log",
            log_path=log_file,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
