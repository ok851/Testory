# -*- coding: utf-8 -*-
"""解析安装目录内的可移植 Python 运行时（供桌面启动器共用）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

_RUNTIME_NAMES = ("pythonw.exe", "python.exe")
_RUNTIME_DIRS = (".venv", ".venv/Scripts", "runtime/python")


def bundled_python_candidates(root: Path) -> list[Path]:
    """按优先级返回已存在的内置 Python 可执行文件。"""
    found: list[Path] = []
    seen: set[str] = set()

    shell = root / "TestoryShell.exe"
    if shell.is_file():
        key = str(shell.resolve()).lower()
        if key not in seen:
            seen.add(key)
            found.append(shell)

    for rel in _RUNTIME_DIRS:
        base = root / rel if "/" not in rel else root.joinpath(*rel.split("/"))
        for name in _RUNTIME_NAMES:
            cand = base / name
            key = str(cand.resolve()).lower() if cand.is_file() else str(cand).lower()
            if cand.is_file() and key not in seen:
                seen.add(key)
                found.append(cand)
    return found


def resolve_bundled_python(root: Path) -> Optional[Path]:
    """优先 pythonw.exe，供桌面 UI 启动。"""
    cands = bundled_python_candidates(root)
    return cands[0] if cands else None


def resolve_bundled_python_console(root: Path) -> Optional[Path]:
    """用于启动前自检（捕获错误输出）。"""
    for cand in bundled_python_candidates(root):
        if cand.name.lower() == "python.exe":
            return cand
        console = cand.with_name("python.exe")
        if console.is_file():
            return console
    return None


def verify_bundled_python(root: Path, *, timeout_sec: float = 30.0) -> Tuple[Optional[Path], Optional[str]]:
    """
    校验内置 Python 能否运行。
    返回 (解释器路径, 错误信息)；成功时错误信息为 None。
    """
    interpreter = resolve_bundled_python(root)
    if interpreter is None:
        return None, "未找到内置 Python 环境。\n请使用完整安装包重新安装 Testory。"

    console = resolve_bundled_python_console(root) or interpreter
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    root_str = str(root.resolve())
    env["PYTHONPATH"] = root_str
    if (root / "app.py").is_file():
        probe = "import sys; import database; import requests; print(sys.version)"
    else:
        probe = "import sys; print(sys.version)"
    try:
        proc = subprocess.run(
            [str(console), "-c", probe],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            creationflags=flags,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return interpreter, "内置 Python 启动超时，请检查杀毒软件是否拦截安装目录。"
    except OSError as exc:
        return interpreter, f"无法启动内置 Python：{exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if not detail:
            detail = f"exit code {proc.returncode}"
        if "did not find executable" in detail.lower():
            detail = (
                "Bundled Python runtime is incomplete.\n"
                "Please reinstall using the latest offline installer."
            )
        elif "No module named" in detail:
            detail = (
                f"Bundled dependencies are incomplete:\n{detail}\n"
                "Please rebuild and reinstall the offline installer."
            )
        return interpreter, detail
    return interpreter, None
