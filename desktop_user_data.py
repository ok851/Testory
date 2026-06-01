# -*- coding: utf-8 -*-
"""桌面版用户数据目录：安装到 Program Files 时写入 %LOCALAPPDATA%\\Testory。"""
from __future__ import annotations

import os
from pathlib import Path


def install_root() -> Path:
    return Path(__file__).resolve().parent


def _is_under_program_files(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = (os.environ.get(key) or "").strip()
        if not base:
            continue
        try:
            resolved.relative_to(Path(base).resolve())
            return True
        except ValueError:
            continue
    return False


def _local_appdata_testory() -> Path:
    base = (os.environ.get("LOCALAPPDATA") or "").strip()
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "Testory"


def resolve_user_data_dir(app_root: Path | None = None) -> Path:
    """返回可写用户数据目录（数据库、日志、客户端配置）。"""
    explicit = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if explicit:
        return Path(explicit)

    root = app_root or install_root()
    desktop_mode = (os.environ.get("UAT_DESKTOP_MODE") or "").strip() in ("1", "true", "yes")
    if desktop_mode or _is_under_program_files(root):
        return _local_appdata_testory()
    return root / "data"


def ensure_user_data_dirs(app_root: Path | None = None) -> Path:
    data = resolve_user_data_dir(app_root)
    (data / "logs").mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    return data
