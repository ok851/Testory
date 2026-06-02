# -*- coding: utf-8 -*-
"""桌面版用户数据目录：安装目录只读时写入 %LOCALAPPDATA%\\Testory。"""
from __future__ import annotations

import os
from pathlib import Path


def install_root() -> Path:
    return Path(__file__).resolve().parent


def _local_appdata_testory() -> Path:
    base = (os.environ.get("LOCALAPPDATA") or "").strip()
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "Testory"


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


def _is_restricted_install_path(path: Path) -> bool:
    """Program Files、Public 桌面、Windows 等常见只读/共享路径。"""
    try:
        text = str(path.resolve()).replace("/", "\\").lower()
    except OSError:
        return True
    markers = (
        "\\program files\\",
        "\\program files (x86)\\",
        "\\users\\public\\",
        "\\windows\\",
        "\\programdata\\",
    )
    return any(m in text for m in markers)


def _path_is_writable(dir_path: Path) -> bool:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        probe = dir_path / ".testory_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def is_desktop_install(app_root: Path | None = None) -> bool:
    flag = (os.environ.get("UAT_DESKTOP_MODE") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    root = app_root or install_root()
    return _is_under_program_files(root) or _is_restricted_install_path(root)


def resolve_user_data_dir(app_root: Path | None = None) -> Path:
    """返回可写用户数据目录（数据库、日志、.env、客户端配置）。"""
    explicit = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if explicit:
        return Path(explicit)

    root = app_root or install_root()
    if is_desktop_install(root):
        return _local_appdata_testory()

    data = root / "data"
    if not _path_is_writable(data):
        return _local_appdata_testory()
    return data


def resolve_env_file(app_root: Path | None = None) -> Path:
    """桌面版 .env 始终落在用户数据目录，不写入安装目录。"""
    explicit = (os.environ.get("TESTORY_ENV_FILE") or "").strip()
    if explicit:
        return Path(explicit)
    return resolve_user_data_dir(app_root) / ".env"


def ensure_user_data_dirs(app_root: Path | None = None) -> Path:
    data = resolve_user_data_dir(app_root)
    data.mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(parents=True, exist_ok=True)
    return data
