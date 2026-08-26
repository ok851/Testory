# -*- coding: utf-8 -*-
"""AI 模型目录与注册表路径（开发 / PyInstaller / 桌面安装包）。"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def _dev_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_install_root() -> Path:
    try:
        from modules.core.install_paths import resolve_install_root as _rir

        return _rir()
    except ImportError:
        env = (os.environ.get("TESTORY_INSTALL_ROOT") or "").strip()
        if env:
            return Path(env)
        if getattr(sys, "frozen", False):
            exe = Path(sys.executable).resolve()
            parent = exe.parent
            if parent.name == "testory_app" and parent.parent.name == "runtime":
                return parent.parent.parent
        return _dev_root()


def _search_data_file(filename: str) -> Path | None:
    """安装根、config/、PyInstaller _internal、模块目录等均可放置数据 JSON。"""
    name = Path(filename).name
    seen: set[str] = set()
    candidates: list[Path] = []

    def _add(path: Path) -> None:
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            candidates.append(path)

    root = resolve_install_root()
    _add(root / name)
    _add(root / "config" / name)

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        _add(exe_dir / name)
        _add(exe_dir / "_internal" / name)
        _add(exe_dir / "config" / name)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            mp = Path(meipass)
            _add(mp / name)
            _add(mp / "config" / name)

    # 与 ai_config_paths 同目录（PyInstaller datas 常落在此处）
    _add(Path(__file__).resolve().parents[2] / name)

    try:
        from modules.core.install_paths import resource_root

        rr = resource_root()
        _add(rr / name)
        _add(rr / "config" / name)
    except ImportError:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            mp = Path(meipass)
            _add(mp / name)
            _add(mp / "config" / name)

    _add(_dev_root() / name)
    _add(_dev_root() / "config" / name)

    for p in candidates:
        if p.is_file():
            return p
    return None


def ai_provider_catalog_path() -> Path:
    found = _search_data_file("ai_provider_catalog.json")
    return found if found else resolve_install_root() / "ai_provider_catalog.json"


_CATALOG_CACHE: Optional[dict] = None
_CATALOG_SOURCE: Optional[str] = None


def _iter_data_file_candidates(filename: str) -> list[Path]:
    """与 _search_data_file 相同候选列表，但返回全部存在路径。"""
    name = Path(filename).name
    seen: set[str] = set()
    out: list[Path] = []

    def _add(path: Path) -> None:
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            out.append(path)

    root = resolve_install_root()
    _add(root / name)
    _add(root / "config" / name)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        _add(exe_dir / name)
        _add(exe_dir / "_internal" / name)
        _add(exe_dir / "config" / name)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            mp = Path(meipass)
            _add(mp / name)
            _add(mp / "config" / name)
    _add(Path(__file__).resolve().parents[2] / name)
    try:
        from modules.core.install_paths import resource_root

        rr = resource_root()
        _add(rr / name)
        _add(rr / "config" / name)
    except ImportError:
        pass
    _add(_dev_root() / name)
    _add(_dev_root() / "config" / name)
    return [p for p in out if p.is_file()]


def load_ai_provider_catalog_dict() -> dict:
    """加载供应商目录；多路径回退，避免保护版仅把 JSON 打入 _internal 时返回空。"""
    global _CATALOG_CACHE, _CATALOG_SOURCE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    last_err: Optional[Exception] = None
    for path in _iter_data_file_candidates("ai_provider_catalog.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("providers"), list) and raw["providers"]:
                _CATALOG_CACHE = raw
                _CATALOG_SOURCE = str(path)
                return raw
        except Exception as exc:
            last_err = exc
            continue

    _CATALOG_CACHE = {}
    _CATALOG_SOURCE = None
    if last_err is not None:
        try:
            from modules.core.logger import uat_logger

            uat_logger.warning(
                "ai_provider_catalog 加载失败（已搜索安装根、config、_internal）：%s",
                last_err,
            )
        except Exception:
            pass
    return _CATALOG_CACHE


def ai_provider_catalog_source() -> Optional[str]:
    load_ai_provider_catalog_dict()
    return _CATALOG_SOURCE


def ai_model_registry_path() -> Path:
    """可写注册表：优先用户数据目录，首次从安装包种子复制。"""
    name = "ai_model_registry.json"
    try:
        from modules.desktop.desktop_user_data import resolve_user_data_dir

        user_dir = resolve_user_data_dir(resolve_install_root())
    except ImportError:
        user_dir = resolve_install_root() / "data"

    target = user_dir / name
    if target.is_file():
        return target

    seed = _search_data_file(name)
    if seed and seed.resolve() != target.resolve():
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed, target)
            return target
        except OSError:
            return seed

    install_copy = resolve_install_root() / name
    if install_copy.is_file():
        return install_copy
    return target
