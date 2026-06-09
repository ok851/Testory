# -*- coding: utf-8 -*-
"""安装包 / 发布目录启动前自检（桌面壳、后端、资源文件）。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_RUNTIME_REL = ".venv"
_PROBE_CACHE_NAME = ".launch_probe_ok"


def _venv_pythonw(root: Path) -> Optional[Path]:
    for rel in (
        ".venv/pythonw.exe",
        ".venv/Scripts/pythonw.exe",
        ".venv/python.exe",
        ".venv/Scripts/python.exe",
    ):
        p = root / rel.replace("/", os.sep)
        if p.is_file():
            return p
    return None


def _backend_exe(root: Path) -> Optional[Path]:
    for rel in (
        "runtime/testory_app/TestoryBackend.exe",
        "app.py",
    ):
        p = root / rel.replace("/", os.sep)
        if p.is_file():
            return p
    return None


def _install_fingerprint(root: Path) -> str:
    parts = []
    for rel in (
        "Testory.exe",
        "runtime/testory_app/TestoryBackend.exe",
        ".venv/pythonw.exe",
        "packaging/APP_VERSION.txt",
    ):
        p = root / rel.replace("/", os.sep)
        if p.is_file():
            parts.append(f"{rel}:{p.stat().st_mtime_ns}:{p.stat().st_size}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _probe_cache_path(root: Path) -> Path:
    base = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if not base:
        base = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Testory")
    return Path(base) / _PROBE_CACHE_NAME


def _probe_cache_valid(root: Path) -> bool:
    path = _probe_cache_path(root)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("fingerprint") == _install_fingerprint(root)
    except (OSError, json.JSONDecodeError):
        return False


def _write_probe_cache(root: Path) -> None:
    path = _probe_cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fingerprint": _install_fingerprint(root)}, indent=2),
        encoding="utf-8",
    )


def check_layout(root: Path) -> List[str]:
    """不启动进程，仅检查文件布局。"""
    errors: List[str] = []
    root = root.resolve()

    if not (root / "packaging" / "uat_desktop.py").is_file():
        errors.append(f"缺少 packaging\\uat_desktop.py（安装目录：{root}）")

    pyw = _venv_pythonw(root)
    if pyw is None:
        errors.append(
            "缺少内置 Python（.venv\\pythonw.exe）。\n"
            "请使用完整离线安装包重新安装，勿只复制部分文件夹。"
        )

    if _backend_exe(root) is None:
        errors.append(
            "缺少后端（runtime\\testory_app\\TestoryBackend.exe 或 app.py）。\n"
            "保护版安装包必须包含 runtime 目录。"
        )

    if not (root / "templates").is_dir():
        errors.append("缺少 templates 目录。")
    if not (root / "static").is_dir():
        errors.append("缺少 static 目录。")
    boot = root / "static" / "desktop" / "shell_boot.html"
    if not boot.is_file():
        errors.append("缺少 static\\desktop\\shell_boot.html（启动画面）。")

    if not (root / "Testory.exe").is_file():
        errors.append("缺少 Testory.exe 启动器。")

    wv2 = root / "redist" / "webview2" / "MicrosoftEdgeWebview2Setup.exe"
    if sys.platform == "win32" and not wv2.is_file():
        errors.append(
            "缺少 WebView2 引导包 redist\\webview2\\MicrosoftEdgeWebview2Setup.exe；"
            "未安装 WebView2 的机器可能无法显示窗口。"
        )

    browsers = root / "playwright-browsers"
    if not browsers.is_dir():
        errors.append(
            "缺少 playwright-browsers（浏览器自动化可能不可用）。"
            "请重新执行完整 build_desktop_installer.ps1。"
        )

    catalog_found = False
    for rel in ("ai_provider_catalog.json", "config/ai_provider_catalog.json"):
        if (root / rel.replace("/", os.sep)).is_file():
            catalog_found = True
            break
    if not catalog_found:
        errors.append(
            "缺少 ai_provider_catalog.json（AI 测试「添加模型」供应商列表将为空）。"
            "请重新执行 build_desktop_installer.ps1。"
        )

    vendor_tw = root / "static" / "vendor" / "tailwindcss" / "tailwind.min.js"
    if not vendor_tw.is_file():
        errors.append(
            "缺少 static\\vendor\\tailwindcss\\tailwind.min.js（离线 UI 资源）。"
            "请运行: python packaging\\fetch_frontend_vendors.py"
        )

    return errors


def check_python_imports(root: Path, *, timeout_sec: float = 60.0) -> List[str]:
    """用 .venv 内解释器验证 pywebview 等（勿使用根目录 TestoryShell.exe）。"""
    errors: List[str] = []
    pyw = _venv_pythonw(root)
    if pyw is None:
        return errors

    console = pyw.with_name("python.exe") if pyw.name.lower() == "pythonw.exe" else pyw
    if not console.is_file():
        console = pyw

    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = str(root.resolve())
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

    probes = [
        ("import webview; print('webview ok')", "pywebview（桌面窗口）"),
        ("import win32api; print('pywin32 ok')", "pywin32"),
    ]

    for code, label in probes:
        try:
            proc = subprocess.run(
                [str(console), "-c", code],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=env,
                creationflags=flags,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{label} 导入检测超时。")
            continue
        except OSError as exc:
            errors.append(f"{label} 无法运行内置 Python：{exc}")
            continue
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
            errors.append(f"缺少或无法加载 {label}：\n{detail}")
    return errors


def check_current_process_imports(root: Path) -> List[str]:
    """
    子进程 import 成功不代表当前进程可用（例如误用根目录 TestoryShell.exe 启动 uat_desktop）。
    """
    errors: List[str] = []
    root = root.resolve()
    exe = Path(sys.executable).resolve()
    name = exe.name.lower()

    if name == "testoryshell.exe" and exe.parent == root:
        errors.append(
            "当前使用了安装根目录的 TestoryShell.exe，无法加载 pywebview 等内置依赖。\n"
            "请从开始菜单或桌面快捷方式启动「Testory」（Testory.exe），"
            "不要直接运行 TestoryShell.exe。"
        )
        return errors

    try:
        import webview  # noqa: F401
    except ImportError:
        detail = (
            f"当前 Python 进程无法加载 pywebview（解释器：{exe}）。\n"
            "请使用完整安装包重新安装，并通过 Testory.exe 启动。"
        )
        if ".venv" not in str(exe).replace("\\", "/").lower():
            detail += (
                "\n若从命令行调试，请使用："
                f' "{root / ".venv" / "pythonw.exe"}" packaging\\uat_desktop.py'
            )
        errors.append(detail)
    return errors


def run_launch_preflight(root: Path, *, port: int = 5000, force_full_probe: bool = False) -> Tuple[List[str], List[str]]:
    """
    返回 (errors, warnings)。errors 非空时不应启动。
    """
    errors = check_layout(root)
    if not errors and (force_full_probe or not _probe_cache_valid(root)):
        errors.extend(check_python_imports(root))
        if not errors:
            _write_probe_cache(root)
    if not errors:
        errors.extend(check_current_process_imports(root))
    warnings: List[str] = []
    if not (root / "redist" / "webview2").is_dir() and sys.platform == "win32":
        warnings.append("未检测到 WebView2 离线安装包目录。")
    return errors, warnings
