# -*- coding: utf-8 -*-
"""可选组件管理 — 用于按需下载和安装大体积组件（Chromium、OpenCV 等）。

组件列表：
- chromium: Playwright Chromium 浏览器（Web 自动化必需）
- opencv:   OpenCV 视觉库（视觉定位 / AI 视觉必需）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from modules.core.install_paths import resolve_install_root

COMPONENTS_FILE = "components.json"

COMPONENT_DEFS: Dict[str, Dict[str, Any]] = {
    "chromium": {
        "id": "chromium",
        "name": "Playwright Chromium",
        "description": "内置 Chromium 浏览器，用于 Web 自动化测试",
        "icon": "🌐",
        "category": "browser",
        "estimated_size_mb": 180,
        "required_by": ["Web 自动化测试"],
        "dependencies": [],
    },
    "opencv": {
        "id": "opencv",
        "name": "OpenCV 视觉库",
        "description": "计算机视觉库，用于视觉定位和图像识别",
        "icon": "👁️",
        "category": "vision",
        "estimated_size_mb": 70,
        "required_by": ["视觉定位", "AI 视觉", "桌面混排"],
        "dependencies": [],
    },
}

ProgressCallback = Callable[[str, float, str], None]


def _components_file() -> Path:
    return resolve_install_root() / COMPONENTS_FILE


def _load_components() -> Dict[str, Any]:
    path = _components_file()
    if not path.is_file():
        return {c["id"]: {"installed": False} for c in COMPONENT_DEFS.values()}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {c["id"]: {"installed": False} for c in COMPONENT_DEFS.values()}


def _save_components(data: Dict[str, Any]) -> None:
    path = _components_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _venv_python() -> Optional[Path]:
    """返回安装目录内的便携 Python 解释器路径。"""
    root = resolve_install_root()
    candidates = [
        root / ".venv" / "python.exe",
        root / ".venv" / "Scripts" / "python.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p
    if sys.executable and Path(sys.executable).is_file():
        return Path(sys.executable)
    return None


def _check_chromium_installed() -> bool:
    """检测 Playwright Chromium 是否已安装。"""
    root = resolve_install_root()
    browsers_dir = root / "playwright-browsers"
    if browsers_dir.is_dir():
        for child in browsers_dir.iterdir():
            if child.name.startswith("chromium-") and child.is_dir():
                return True
    pw_browsers_env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if pw_browsers_env:
        pw_dir = Path(pw_browsers_env)
        if pw_dir.is_dir():
            for child in pw_dir.iterdir():
                if child.name.startswith("chromium-") and child.is_dir():
                    return True
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        ms_pw = Path(localappdata) / "ms-playwright"
        if ms_pw.is_dir():
            for child in ms_pw.iterdir():
                if child.name.startswith("chromium-") and child.is_dir():
                    return True
    return False


def _check_opencv_installed() -> bool:
    """检测 opencv-python 是否已安装。"""
    py = _venv_python()
    if not py:
        return False
    try:
        result = subprocess.run(
            [str(py), "-c", "import cv2; print(cv2.__version__)"],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


CHECK_FUNCS = {
    "chromium": _check_chromium_installed,
    "opencv": _check_opencv_installed,
}

# 安装状态缓存，避免每次请求都执行耗时检测（如 subprocess 调用）
_check_cache: Dict[str, Any] = {}
_CHECK_CACHE_TTL = 30  # 秒


def _cached_check(component_id: str) -> bool:
    """带缓存的安装状态检测。"""
    import time

    now = time.monotonic()
    cached = _check_cache.get(component_id)
    if cached and (now - cached[1]) < _CHECK_CACHE_TTL:
        return cached[0]
    func = CHECK_FUNCS.get(component_id)
    result = func() if func else False
    _check_cache[component_id] = (result, now)
    return result


def list_components() -> List[Dict[str, Any]]:
    """返回所有组件的列表，包含安装状态。"""
    stored = _load_components()
    result = []
    for cid, cdef in COMPONENT_DEFS.items():
        is_installed = _cached_check(cid)
        info = dict(cdef)
        info["installed"] = is_installed
        stored_info = stored.get(cid, {})
        info["version"] = stored_info.get("version", "")
        info["installed_at"] = stored_info.get("installed_at", "")
        result.append(info)
    return result


def is_installed(component_id: str) -> bool:
    """检查指定组件是否已安装（带缓存）。"""
    if component_id not in COMPONENT_DEFS:
        return False
    return _cached_check(component_id)


def _set_installed(component_id: str, version: str = "") -> None:
    data = _load_components()
    entry = data.get(component_id, {})
    entry["installed"] = True
    entry["version"] = version
    from datetime import datetime
    entry["installed_at"] = datetime.now().isoformat()
    data[component_id] = entry
    _save_components(data)


def _run_pip_install(package: str, progress: Optional[ProgressCallback] = None) -> bool:
    """通过 pip 安装 Python 包。"""
    py = _venv_python()
    if not py:
        if progress:
            progress("error", 0, "找不到 Python 解释器")
        return False

    mirror = os.environ.get("PYPI_MIRROR", "").strip()
    cmd = [str(py), "-m", "pip", "install", "--upgrade", package]
    if mirror:
        cmd.extend(["-i", mirror, "--trusted-host", mirror.split("://")[-1].split("/")[0]])

    if progress:
        progress("downloading", 10, f"正在下载 {package}...")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "pip install 失败").strip()
            if progress:
                progress("error", 0, err[-500:])
            return False
        if progress:
            progress("done", 100, f"{package} 安装成功")
        return True
    except subprocess.TimeoutExpired:
        if progress:
            progress("error", 0, "安装超时，请检查网络后重试")
        return False
    except Exception as e:
        if progress:
            progress("error", 0, f"安装失败: {e}")
        return False


def _install_chromium(progress: Optional[ProgressCallback] = None) -> bool:
    """安装 Playwright Chromium 浏览器。"""
    py = _venv_python()
    if not py:
        if progress:
            progress("error", 0, "找不到 Python 解释器")
        return False

    root = resolve_install_root()
    browsers_dir = root / "playwright-browsers"
    browsers_dir.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir.resolve()),
        "PYTHONIOENCODING": "utf-8",
    }
    mirror = os.environ.get("PLAYWRIGHT_DOWNLOAD_MIRROR", "").strip()
    if mirror:
        env["PLAYWRIGHT_DOWNLOAD_HOST"] = mirror

    if progress:
        progress("downloading", 20, "正在下载 Chromium 浏览器...")

    try:
        result = subprocess.run(
            [str(py), "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=900,
            env=env,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "playwright install 失败").strip()
            if progress:
                progress("error", 0, err[-500:])
            return False

        _set_installed("chromium", version="latest")
        if progress:
            progress("done", 100, "Chromium 浏览器安装成功")
        return True
    except subprocess.TimeoutExpired:
        if progress:
            progress("error", 0, "下载超时，请检查网络后重试")
        return False
    except Exception as e:
        if progress:
            progress("error", 0, f"安装失败: {e}")
        return False


def _install_opencv(progress: Optional[ProgressCallback] = None) -> bool:
    """安装 opencv-python。"""
    success = _run_pip_install("opencv-python-headless", progress)
    if success:
        version = ""
        py = _venv_python()
        if py:
            try:
                r = subprocess.run(
                    [str(py), "-c", "import cv2; print(cv2.__version__)"],
                    capture_output=True, text=True, timeout=10,
                )
                version = r.stdout.strip()
            except Exception:
                pass
        _set_installed("opencv", version=version)
    return success


INSTALL_FUNCS = {
    "chromium": _install_chromium,
    "opencv": _install_opencv,
}


def install(component_id: str, progress: Optional[ProgressCallback] = None) -> bool:
    """安装指定组件。

    Args:
        component_id: 组件 ID（chromium / opencv）
        progress: 进度回调函数，签名为 callback(status: str, percent: float, message: str)
    """
    if component_id not in COMPONENT_DEFS:
        if progress:
            progress("error", 0, f"未知组件: {component_id}")
        return False

    if is_installed(component_id):
        if progress:
            progress("done", 100, "组件已安装")
        return True

    install_fn = INSTALL_FUNCS.get(component_id)
    if not install_fn:
        if progress:
            progress("error", 0, f"组件 {component_id} 暂不支持自动安装")
        return False

    return install_fn(progress)


def get_available_components() -> List[str]:
    """返回所有可用组件的 ID 列表。"""
    return list(COMPONENT_DEFS.keys())




