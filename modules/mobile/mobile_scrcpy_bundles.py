# -*- coding: utf-8 -*-
"""插件市场：scrcpy 投屏 + 反控 runtime bundle 下载与安装。"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
import urllib.request

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _ROOT / "config" / "plugin_bundles" / "mobile_scrcpy.json"
_PLUGIN_ID = "mobile-scrcpy"


# ------------------------------------------------------------------
# manifest / platform helpers
# ------------------------------------------------------------------

def _manifest() -> Dict[str, Any]:
    if not _MANIFEST_PATH.is_file():
        return {}
    try:
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _platform_key() -> str:
    s = sys.platform.lower()
    if s.startswith("win"):
        return "windows"
    if s.startswith("darwin"):
        return "darwin"
    return "linux"


def _platform_spec() -> Dict[str, Any]:
    manifest = _manifest()
    platforms = manifest.get("platforms") or {}
    spec = platforms.get(_platform_key()) or {}
    if not spec and _platform_key() != "windows":
        spec = platforms.get("windows") or {}
    return spec if isinstance(spec, dict) else {}


def _sanitize_env_value(raw: Optional[str]) -> str:
    """忽略 .env 中空值、行内注释误解析为值的情况。"""
    s = (raw or "").strip()
    if not s or s.startswith("#"):
        return ""
    if " #" in s:
        s = s.split(" #", 1)[0].strip()
    if s.startswith("#"):
        return ""
    return s


def _is_http_url(value: str) -> bool:
    low = (value or "").strip().lower()
    return low.startswith("http://") or low.startswith("https://")


# ------------------------------------------------------------------
# install directory & exe resolution
# ------------------------------------------------------------------

def scrcpy_install_dir() -> Path:
    """scrcpy 安装目录。"""
    from web_capture.plugin_market import software_extensions_root

    dest = software_extensions_root() / "android" / "scrcpy"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _resolve_scrcpy_in_dir(install_dir: Optional[Path] = None) -> Optional[str]:
    """在安装目录中定位 scrcpy 可执行文件（支持一层子目录）。"""
    dest = install_dir or scrcpy_install_dir()
    if not dest.is_dir():
        return None
    spec = _platform_spec()
    binary = spec.get("scrcpy_binary") or (
        "scrcpy.exe" if _platform_key() == "windows" else "scrcpy"
    )
    direct = dest / binary
    if direct.is_file():
        return str(direct.resolve())
    for hit in dest.rglob(binary):
        if hit.is_file():
            return str(hit.resolve())
    return None


def get_installed_scrcpy_exe() -> Optional[str]:
    """已安装目录中的 scrcpy 路径（以磁盘文件为准）。"""
    return _resolve_scrcpy_in_dir()


# ------------------------------------------------------------------
# catalog entry
# ------------------------------------------------------------------

def get_scrcpy_catalog_entry() -> Dict[str, Any]:
    """返回 scrcpy 插件的 catalog 条目（供插件市场 UI 使用）。"""
    manifest = _manifest()
    spec = _platform_spec()
    local = _resolve_local_zip()
    urls = _collect_download_urls()
    installed_exe = get_installed_scrcpy_exe()
    return {
        "id": _PLUGIN_ID,
        "category": "mobile",
        "name": manifest.get("name") or "scrcpy (mirror + control)",
        "browser": "any",
        "browser_label": "移动端",
        "icon": "fas fa-mobile-alt",
        "icon_color": "#4CAF50",
        "version": manifest.get("version") or "2.7",
        "type": "runtime_bundle",
        "description": (
            manifest.get("description")
            or "高帧率投屏 + PC 端反控（tap/swipe）。安装后自动写入 scrcpy_path。"
        ),
        "features": ["高帧率投屏", "PC 端反控（tap/swipe）", "自动配置 scrcpy_path"],
        "download_source": (
            "local" if local else ("url" if urls else "none")
        ),
        "local_bundle_ready": bool(local),
        "download_url_configured": bool(urls),
        "installed": bool(installed_exe),
        "scrcpy_path": installed_exe or "",
        "size_mb_hint": spec.get("size_mb_hint"),
        "license": manifest.get("license"),
    }


# ------------------------------------------------------------------
# download / extract helpers
# ------------------------------------------------------------------

def _resolve_local_zip() -> Optional[Path]:
    env_local = _sanitize_env_value(os.environ.get("SCRCPY_LOCAL_ZIP"))
    if env_local:
        p = Path(env_local)
        if p.is_file():
            return p.resolve()

    spec = _platform_spec()
    filename = spec.get("filename") or f"scrcpy-{_platform_key()}.zip"
    manifest = _manifest()
    patterns = manifest.get("local_bundle_search") or [
        "plugin_bundles/{filename}",
        "config/plugin_bundles/{filename}",
        "static/plugin_bundles/{filename}",
    ]
    for pattern in patterns:
        rel = str(pattern).format(filename=filename)
        candidate = (_ROOT / rel).resolve()
        if candidate.is_file():
            return candidate
    return None


def _collect_download_urls() -> List[str]:
    seen: set = set()
    out: List[str] = []

    def add(raw: Optional[str]) -> None:
        u = (raw or "").strip()
        if not _is_http_url(u):
            u = _sanitize_env_value(raw)
        if not _is_http_url(u) or u in seen:
            return
        seen.add(u)
        out.append(u)

    spec = _platform_spec()
    mirrors = spec.get("mirror_urls")
    if isinstance(mirrors, list):
        for item in mirrors:
            add(str(item) if item else None)
    add(os.environ.get("SCRCPY_URL"))
    add(spec.get("url"))
    return out


def _download_with_fallback(urls: List[str], dest: Path) -> str:
    last_err: Optional[Exception] = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "testory-scrpy/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                dest.write_bytes(resp.read())
            return url
        except Exception as exc:
            last_err = exc
    raise RuntimeError(
        f"所有下载地址均失败: {[u.split('?')[0] for u in urls]}"
    ) from last_err


def _verify_sha256(path: Path, expected: str) -> None:
    """若 expected 非空则校验 sha256。"""
    import hashlib

    if not expected:
        return
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected.lower():
        raise RuntimeError(f"SHA256 校验失败：期望 {expected}，实际 {actual}")


def _extract_zip(zip_path: Path, dest: Path, inner_dir: str = "") -> None:
    """解压 zip，若指定 inner_dir 则只提取该目录内容到 dest。"""
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        if inner_dir:
            prefix = inner_dir.rstrip("/") + "/"
            extracted = False
            for info in zf.infolist():
                if info.filename.startswith(prefix) and not info.is_dir():
                    rel = info.filename[len(prefix):]
                    if not rel:
                        continue
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(str(target), "wb") as dst:
                        dst.write(src.read())
                    extracted = True
            if not extracted:
                # fallback: extract all
                zf.extractall(str(dest))
        else:
            zf.extractall(str(dest))


def _offline_install_message(extra: Optional[List[str]] = None) -> str:
    spec = _platform_spec()
    url = spec.get("url") or ""
    local_hint = ""
    if _platform_key() == "windows":
        local_hint = (
            "或手动下载 zip 后设置环境变量 SCRCPY_LOCAL_ZIP 指向该文件，再重试安装。"
        )
    msg = (
        f"无法下载 scrcpy（网络不通）。请手动从以下地址下载 zip 包：\n{url}\n"
        f"{local_hint}"
    )
    if extra:
        msg += "\n" + "\n".join(extra)
    return msg


# ------------------------------------------------------------------
# apply scrcpy_path to config
# ------------------------------------------------------------------

def _apply_scrcpy_path(scrcpy_exe: str) -> None:
    """写入 scrcpy_path 到环境变量与 client_config。"""
    os.environ["SCRCPY_PATH"] = scrcpy_exe
    try:
        from modules.mobile.mobile_env_config import save_mobile_defaults

        save_mobile_defaults({"scrcpy_path": scrcpy_exe})
    except Exception:
        pass


# ------------------------------------------------------------------
# install entry point
# ------------------------------------------------------------------

def install_mobile_scrcpy(
    *,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """下载或复制 zip，解压并登记 scrcpy 插件状态。"""
    from web_capture.plugin_market import _load_state, _save_state

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

    try:
        local = _resolve_local_zip()
        source_kind = "download"

        if local:
            _step(20, "使用本地安装包…")
            zip_path = local
            expected_sha = str(_platform_spec().get("sha256") or "").strip()
            _verify_sha256(zip_path, expected_sha)
            _step(70, "正在解压…")
        else:
            urls = _collect_download_urls()
            if not urls:
                return {
                    "success": False,
                    "error": _offline_install_message(),
                }
            _step(15, "正在下载 scrcpy…")
            fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="testory_scrcpy_")
            os.close(fd)
            tmp_zip = Path(tmp_name)
            _download_with_fallback(urls, tmp_zip)
            _step(45, "下载完成")
            zip_path = tmp_zip
            expected_sha = str(_platform_spec().get("sha256") or "").strip()
            _verify_sha256(zip_path, expected_sha)
            _step(70, "正在解压…")

        inner_dir = _platform_spec().get("zip_inner_dir", "")
        _extract_zip(zip_path, dest, inner_dir)

        scrcpy_exe = _resolve_scrcpy_in_dir(dest)
        if not scrcpy_exe:
            return {
                "success": False,
                "error": (
                    f"解压完成但未找到 scrcpy（目录 {dest}）。"
                    "请确认 zip 为官方 scrcpy 包，或删除该目录后重试。"
                ),
            }

        _apply_scrcpy_path(scrcpy_exe)
        _step(90, "保存配置…")

        state = _load_state()
        plugins = state.setdefault("plugins", {})
        now = datetime.now(timezone.utc).isoformat()
        plugins[_PLUGIN_ID] = {
            "plugin_id": _PLUGIN_ID,
            "type": "runtime_bundle",
            "version": (_manifest().get("version") or "2.7"),
            "install_dir": str(dest),
            "scrcpy_path": scrcpy_exe,
            "installed_at": now,
            "source": source_kind if not local else "local",
        }
        _save_state(state)
        _step(100, "安装完成")

        return {
            "success": True,
            "plugin_id": _PLUGIN_ID,
            "installed": True,
            "install_dir": str(dest),
            "scrcpy_path": scrcpy_exe,
            "progress": progress,
            "message": "scrcpy 已安装。投屏与反控功能已就绪。",
        }
    except Exception as exc:
        msg = str(exc)
        if "getaddrinfo failed" in msg or "urlopen error" in msg.lower():
            msg = _offline_install_message([msg])
        return {"success": False, "error": msg}
    finally:
        if tmp_zip and tmp_zip.is_file():
            try:
                tmp_zip.unlink()
            except OSError:
                pass
