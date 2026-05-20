# -*- coding: utf-8 -*-
"""
本机应用目录：首次扫描开始菜单建立 exe 路径与别名映射，持久化到 data/desktop_app_catalog.json。
与 .env 中 DESKTOP_APP_ALIASES 合并（.env 优先）。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_CATALOG_VERSION = 1
_BUILD_LOCK = threading.Lock()
_BUILDING = False

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _catalog_path() -> Path:
    root = Path(__file__).resolve().parent
    data_dir = Path(os.environ.get("UAT_DATA_DIR") or (root / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "desktop_app_catalog.json"


def _slugify(text: str) -> str:
    s = (text or "").strip().lower()
    if s.endswith(".exe"):
        s = s[:-4]
    s = _SLUG_RE.sub("_", s).strip("_")
    return s or "app"


def _load_raw() -> Dict[str, Any]:
    path = _catalog_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_raw(data: Dict[str, Any]) -> None:
    path = _catalog_path()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_catalog_from_start_menu() -> Dict[str, Any]:
    """扫描开始菜单快捷方式，生成应用目录。"""
    from desktop_discovery import _refresh_start_menu_index, discovery_available

    if not discovery_available():
        return {"version": _CATALOG_VERSION, "built_at": time.time(), "apps": []}

    index = _refresh_start_menu_index()
    apps: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()

    for key, exe_path in sorted(index.items(), key=lambda x: x[1].lower()):
        if not exe_path or exe_path in seen_paths:
            continue
        if not os.path.isfile(exe_path):
            continue
        seen_paths.add(exe_path)
        exe_name = os.path.basename(exe_path)
        stem = os.path.splitext(exe_name)[0]
        display = key if not key.endswith(".exe") else stem
        aliases = sorted({
            a
            for a in (
                stem.lower(),
                exe_name.lower(),
                key.lower(),
                _slugify(display),
            )
            if a
        })
        apps.append({
            "id": _slugify(stem or display),
            "display_name": display,
            "exe_name": exe_name,
            "path": exe_path,
            "aliases": aliases,
            "source": "start_menu",
        })

    apps.sort(key=lambda a: (a.get("display_name") or "").lower())
    return {
        "version": _CATALOG_VERSION,
        "built_at": time.time(),
        "apps": apps,
        "app_count": len(apps),
    }


def ensure_catalog_built(*, force: bool = False) -> Dict[str, Any]:
    """若目录不存在或 force，则扫描并写入；返回当前目录。"""
    global _BUILDING
    path = _catalog_path()
    if path.is_file() and not force:
        data = _load_raw()
        if data.get("apps"):
            return data

    with _BUILD_LOCK:
        if _BUILDING and not force:
            return _load_raw()
        _BUILDING = True
        try:
            if path.is_file() and not force:
                data = _load_raw()
                if data.get("apps"):
                    return data
            data = build_catalog_from_start_menu()
            _save_raw(data)
            return data
        finally:
            _BUILDING = False


def ensure_catalog_built_async(*, force: bool = False) -> None:
    """后台构建，不阻塞 Flask 启动。"""

    def _run() -> None:
        try:
            ensure_catalog_built(force=force)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="desktop-catalog-build").start()


def list_catalog_apps() -> List[Dict[str, Any]]:
    data = ensure_catalog_built()
    apps = data.get("apps")
    return apps if isinstance(apps, list) else []


def catalog_meta() -> Dict[str, Any]:
    data = _load_raw() if _catalog_path().is_file() else ensure_catalog_built()
    return {
        "catalog_path": str(_catalog_path()),
        "built_at": data.get("built_at"),
        "app_count": len(data.get("apps") or []),
        "version": data.get("version"),
    }


def catalog_aliases_map() -> Dict[str, str]:
    """别名（小写）→ exe 完整路径；供 launch 解析。"""
    out: Dict[str, str] = {}
    for app in list_catalog_apps():
        path = (app.get("path") or "").strip()
        if not path:
            continue
        keys = set(app.get("aliases") or [])
        keys.add((app.get("id") or "").strip().lower())
        keys.add((app.get("exe_name") or "").strip().lower())
        keys.add((app.get("display_name") or "").strip().lower())
        for k in keys:
            kk = (k or "").strip().lower()
            if kk:
                out[kk] = path
    return out


def find_catalog_app(query: str) -> Optional[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return None
    q_stem = os.path.splitext(q)[0] if q.endswith(".exe") else q
    for app in list_catalog_apps():
        if q == (app.get("id") or "").lower():
            return app
        if q in (app.get("aliases") or []):
            return app
        if q == (app.get("exe_name") or "").lower():
            return app
        if q == (app.get("display_name") or "").lower():
            return app
    # 安装包名如 AweSun_16.2.0.27059_x64.exe → 目录中的 AweSun.exe
    for app in list_catalog_apps():
        cat_stem = os.path.splitext((app.get("exe_name") or ""))[0].lower()
        if not cat_stem:
            continue
        if q_stem == cat_stem or q_stem.startswith(cat_stem + "_"):
            return app
    return None
