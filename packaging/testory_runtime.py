# -*- coding: utf-8 -*-
"""解析安装目录内的可移植 Python 运行时（供桌面启动器共用）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

# 仅 .venv 内解释器带有 python3xx._pth → Lib\site-packages；根目录 TestoryShell.exe 不能优先使用
_RUNTIME_DIRS = (".venv", ".venv/Scripts")


def _add_candidate(found: list[Path], seen: set[str], cand: Path) -> None:
    if not cand.is_file():
        return
    key = str(cand.resolve()).lower()
    if key in seen:
        return
    seen.add(key)
    found.append(cand)


def bundled_python_candidates(root: Path) -> list[Path]:
    """返回用于执行 packaging\\uat_desktop.py 的解释器（优先 .venv\\pythonw）。"""
    found: list[Path] = []
    seen: set[str] = set()

    for rel in _RUNTIME_DIRS:
        base = root / rel if "/" not in rel else root.joinpath(*rel.split("/"))
        for name in ("pythonw.exe", "python.exe"):
            _add_candidate(found, seen, base / name)

    return found


def resolve_bundled_python(root: Path) -> Optional[Path]:
    cands = bundled_python_candidates(root)
    return cands[0] if cands else None


def resolve_bundled_python_console(root: Path) -> Optional[Path]:
    for cand in bundled_python_candidates(root):
        if cand.name.lower() == "python.exe":
            return cand
        console = cand.with_name("python.exe")
        if console.is_file():
            return console
    return None


def verify_bundled_python(root: Path, *, timeout_sec: float = 30.0) -> Tuple[Optional[Path], Optional[str]]:
    """
    校验内置 Python 能否运行并加载桌面壳依赖。
    返回 (解释器路径, 错误信息)；成功时错误信息为 None。
    """
    interpreter = resolve_bundled_python(root)
    if interpreter is None:
        return None, (
            "未找到内置 Python（.venv\\pythonw.exe）。\n"
            "请使用完整离线安装包重新安装 Testory。"
        )

    console = resolve_bundled_python_console(root) or interpreter
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = str(root.resolve())

    protected = (root / "runtime" / "testory_app" / "TestoryBackend.exe").is_file()
    if protected:
        probe = "import webview; import sys; print(sys.executable)"
    elif (root / "app.py").is_file():
        probe = "import webview; import database; import requests; print(sys.executable)"
    else:
        probe = "import webview; print(sys.executable)"

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
                "内置 Python 运行库不完整。\n"
                "请重新安装完整离线安装包。"
            )
        elif "No module named" in detail:
            detail = (
                f"内置依赖未正确安装：\n{detail}\n"
                "常见原因：使用了根目录 TestoryShell.exe 而非 .venv 解释器；"
                "请重新执行 build_desktop_installer.ps1 并完整安装。"
            )
        return interpreter, detail
    return interpreter, None
