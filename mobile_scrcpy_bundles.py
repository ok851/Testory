# -*- coding: utf-8 -*-
"""插件市场：scrcpy 高帧率投屏组件。"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import urllib.request
from urllib.error import URLError
from urllib.request import Request

from mobile_emulator_sdk_bundles import (
    _download_url,
    _offline_bundle_dirs,
    _platform_key,
    _sanitize_env_value,
    _verify_sha256,
)

_ROOT = Path(__file__).resolve().parent
_MANIFEST_PATH = _ROOT / "config" / "plugin_bundles" / "android_scrcpy.json"
_PLUGIN_ID = "mobile-scrcpy"

ProgressCallback = Optional[Callable[[int, str], None]]


def _manifest() -> Dict[str, Any]:
    if not _MANIFEST_PATH.is_file():
        return {}
    try:
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _platform_spec() -> Dict[str, Any]:
    manifest = _manifest()
    platforms = manifest.get("platforms") or {}
    spec = platforms.get(_platform_key()) or {}
    return spec if isinstance(spec, dict) else {}


def scrcpy_install_dir() -> Path:
    from web_capture.plugin_market import software_extensions_root

    dest = software_extensions_root() / "android" / "scrcpy"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def get_installed_scrcpy_exe() -> Optional[str]:
    dest = scrcpy_install_dir()
    name = "scrcpy.exe" if _platform_key() == "windows" else "scrcpy"
    direct = dest / name
    if direct.is_file():
        return str(direct.resolve())
    for hit in dest.rglob(name):
        if hit.is_file():
            return str(hit.resolve())
    return None


def _has_scrcpy_server(install_dir: Path) -> bool:
    for hit in install_dir.rglob("scrcpy-server*"):
        if hit.is_file():
            return True
    return False


def _resolve_local_archive() -> Optional[Path]:
    env_local = _sanitize_env_value(os.environ.get("SCRCPY_BUNDLE_LOCAL_ZIP"))
    if env_local:
        p = Path(env_local)
        if p.is_file():
            return p.resolve()
    spec = _platform_spec()
    filename = spec.get("filename") or "scrcpy-win64.zip"
    for root in _offline_bundle_dirs():
        cand = (root / filename).resolve()
        if cand.is_file():
            return cand
    return None


def _download_with_fallback(urls: List[str], dest: Path) -> None:
    errors: List[str] = []
    for url in urls:
        try:
            _download_url(url, dest)
            return
        except (URLError, OSError, RuntimeError, TimeoutError, UnicodeEncodeError) as exc:
            errors.append(str(exc)[:120])
    hint = "无法下载 scrcpy。请检查网络或联系管理员提供离线安装包。"
    if errors:
        hint += " 详情: " + "; ".join(errors[:2])
    raise RuntimeError(hint)


def _collect_download_urls() -> List[str]:
    seen: set = set()
    out: List[str] = []

    def add(raw: Optional[str]) -> None:
        u = _sanitize_env_value(raw) or (raw or "").strip()
        if not u.startswith(("http://", "https://")) or u in seen:
            return
        seen.add(u)
        out.append(u)

    add(os.environ.get("SCRCPY_BUNDLE_URL"))
    spec = _platform_spec()
    add(spec.get("url"))
    for item in spec.get("mirror_urls") or []:
        add(str(item) if item else None)
    return out


def _extract_scrcpy_zip(zip_path: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="testory_scrcpy_"))
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)
        exe_name = "scrcpy.exe" if _platform_key() == "windows" else "scrcpy"
        hits = list(tmp.rglob(exe_name))
        if not hits:
            raise RuntimeError("安装包中未找到 scrcpy 程序")
        tool_dir = hits[0].parent
        for item in tool_dir.iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not get_installed_scrcpy_exe():
        raise RuntimeError("解压后未找到 scrcpy")
    if not _has_scrcpy_server(dest):
        raise RuntimeError("解压后未找到 scrcpy-server，请使用完整官方安装包")


def _apply_scrcpy_path(exe: str) -> None:
    os.environ["SCRCPY_PATH"] = exe
    try:
        from mobile_env_config import save_mobile_defaults

        save_mobile_defaults({"scrcpy_path": exe})
    except Exception:
        pass


def get_scrcpy_catalog_entry() -> Dict[str, Any]:
    manifest = _manifest()
    spec = _platform_spec()
    local = _resolve_local_archive()
    urls = _collect_download_urls()
    installed = get_installed_scrcpy_exe()
    return {
        "id": _PLUGIN_ID,
        "category": "mobile",
        "name": manifest.get("name") or "scrcpy 高帧率投屏",
        "browser": "any",
        "browser_label": "移动端",
        "icon": "fas fa-bolt",
        "icon_color": "#8B5CF6",
        "version": manifest.get("version") or "1.0.0",
        "type": "runtime_bundle",
        "description": manifest.get("description")
        or "模拟器高帧率画面组件，安装后自动配置。",
        "features": ["高帧率画布", "自动配置", "模拟器推荐"],
        "download_source": (
            "local" if local else ("installed" if installed else ("url" if urls else "none"))
        ),
        "local_bundle_ready": bool(local),
        "download_url_configured": bool(urls),
        "size_mb_hint": spec.get("size_mb_hint"),
        "license": manifest.get("license"),
    }


def install_scrcpy_bundle(*, progress_cb: ProgressCallback = None) -> Dict[str, Any]:
    from web_capture.plugin_market import _load_state, _save_state

    if _platform_key() != "windows":
        return {
            "success": False,
            "error": "当前系统暂未提供一键安装，请联系管理员在 Windows 环境安装 scrcpy。",
        }

    dest = scrcpy_install_dir()
    progress: List[Dict[str, Any]] = []
    tmp_zip: Optional[Path] = None

    def _step(percent: int, label: str) -> None:
        progress.append({"label": label, "percent": percent})
        if progress_cb:
            try:
                progress_cb(percent, label)
            except Exception:
                pass

    exe = get_installed_scrcpy_exe()
    if exe and _has_scrcpy_server(dest):
        _apply_scrcpy_path(exe)
        return {
            "success": True,
            "plugin_id": _PLUGIN_ID,
            "installed": True,
            "scrcpy_path": exe,
            "message": "scrcpy 已就绪，可在「移动端测试」中使用高帧率画面。",
            "progress": [{"label": "已安装", "percent": 100}],
        }

    try:
        local = _resolve_local_archive()
        if local:
            _step(15, "使用本地安装包…")
            zip_path = local
            _verify_sha256(zip_path, str(_platform_spec().get("sha256") or ""))
        else:
            urls = _collect_download_urls()
            if not urls:
                return {
                    "success": False,
                    "error": "无法下载 scrcpy。请检查网络，或联系管理员提供离线安装包。",
                }
            _step(10, "正在下载 scrcpy…")
            fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="testory_scrcpy_")
            os.close(fd)
            tmp_zip = Path(tmp_name)
            _download_with_fallback(urls, tmp_zip)
            zip_path = tmp_zip
            _verify_sha256(zip_path, str(_platform_spec().get("sha256") or ""))
        _step(60, "正在解压…")
        _extract_scrcpy_zip(zip_path, dest)
        exe = get_installed_scrcpy_exe()
        if not exe:
            return {"success": False, "error": "解压后未找到 scrcpy"}
        _apply_scrcpy_path(exe)
        _step(95, "保存配置…")
        state = _load_state()
        plugins = state.setdefault("plugins", {})
        plugins[_PLUGIN_ID] = {
            "plugin_id": _PLUGIN_ID,
            "type": "runtime_bundle",
            "version": _manifest().get("version") or "1.0.0",
            "install_dir": str(dest),
            "scrcpy_path": exe,
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_state(state)
        _step(100, "安装完成")
        return {
            "success": True,
            "plugin_id": _PLUGIN_ID,
            "installed": True,
            "scrcpy_path": exe,
            "progress": progress,
            "message": "scrcpy 安装完成。打开「移动端测试」连接模拟器即可使用高帧率画面。",
        }
    except Exception as exc:
        msg = str(exc)
        if "latin-1" in msg:
            msg = "下载失败（网络或代理异常），请关闭代理后重试或联系管理员提供离线包。"
        return {"success": False, "error": msg}
    finally:
        if tmp_zip and tmp_zip.is_file():
            try:
                tmp_zip.unlink()
            except OSError:
                pass
