# -*- coding: utf-8 -*-
"""插件市场：Android Platform-Tools (adb) 下载与安装。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError
import urllib.request
from urllib.request import Request

_ROOT = Path(__file__).resolve().parent
_MANIFEST_PATH = _ROOT / "config" / "plugin_bundles" / "android_platform_tools.json"
_PLUGIN_ID = "mobile-android-platform-tools"


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


def android_tools_install_dir() -> Path:
    from web_capture.plugin_market import software_extensions_root

    dest = software_extensions_root() / "android" / "platform-tools"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def resolve_adb_in_dir(install_dir: Optional[Path] = None) -> Optional[str]:
    """在安装目录中定位 adb 可执行文件（支持一层子目录）。"""
    dest = install_dir or android_tools_install_dir()
    if not dest.is_dir():
        return None
    spec = _platform_spec()
    binary = spec.get("adb_binary") or ("adb.exe" if _platform_key() == "windows" else "adb")
    direct = dest / binary
    if direct.is_file():
        return str(direct.resolve())
    for hit in dest.rglob(binary):
        if hit.is_file():
            return str(hit.resolve())
    return None


def get_installed_adb_path() -> Optional[str]:
    """已安装目录中的 adb 路径（以磁盘文件为准，不依赖插件状态是否已写入）。"""
    return resolve_adb_in_dir()


def _adb_bundle_valid(install_dir: Path) -> bool:
    return bool(resolve_adb_in_dir(install_dir))


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


def _resolve_local_zip() -> Optional[Path]:
    env_local = _sanitize_env_value(os.environ.get("ANDROID_PLATFORM_TOOLS_LOCAL_ZIP"))
    if env_local:
        p = Path(env_local)
        if p.is_file():
            return p.resolve()

    spec = _platform_spec()
    filename = spec.get("filename") or f"platform-tools-{_platform_key()}.zip"
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


def _resolve_download_url() -> str:
    urls = _collect_download_urls()
    return urls[0] if urls else ""


def _collect_download_urls() -> List[str]:
    """下载地址列表：国内镜像 → .env → 官方（去重）。"""
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
    add(os.environ.get("ANDROID_PLATFORM_TOOLS_URL"))
    add(spec.get("url"))
    return out


def _resolve_system_sdk_platform_tools() -> Optional[Path]:
    """若本机已装 Android Studio / SDK，直接复用其 platform-tools。"""
    candidates: List[Path] = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = _sanitize_env_value(os.environ.get(key))
        if root:
            candidates.append(Path(root) / "platform-tools")
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(Path(local) / "Android" / "Sdk" / "platform-tools")
    user = os.environ.get("USERPROFILE", "").strip()
    if user:
        candidates.append(Path(user) / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools")
    for path in candidates:
        try:
            if _adb_bundle_valid(path.resolve()):
                return path.resolve()
        except OSError:
            continue
    return None


def _copy_platform_tools_dir(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)


def _offline_install_message(errors: Optional[List[str]] = None) -> str:
    spec = _platform_spec()
    fname = spec.get("filename") or "platform-tools-windows.zip"
    root = _ROOT / "plugin_bundles"
    hint = (
        "无法联网下载 Platform-Tools（DNS/外网不可用）。请任选一种方式后重新点「安装」：\n"
        f"1) 在有网络的电脑下载官方 zip，重命名为 {fname}，放到：\n   {root}\n"
        "2) 若已安装 Android Studio，确保 SDK 含 platform-tools（将自动检测）。\n"
        "3) 在 .env 配置可访问的 ANDROID_PLATFORM_TOOLS_URL 或 ANDROID_PLATFORM_TOOLS_LOCAL_ZIP"
    )
    if errors:
        hint += "\n网络尝试：" + "; ".join(errors[:2])
    return hint


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_sha256(path: Path, expected: str) -> None:
    exp = (expected or "").strip().lower()
    if not exp:
        return
    got = _sha256_file(path)
    if got != exp:
        raise RuntimeError(f"安装包校验失败：SHA256 不匹配（期望 {exp[:12]}…，实际 {got[:12]}…）")


def _download_url(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = (url or "").strip()
    try:
        url.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RuntimeError("下载地址无效，请联系管理员检查网络或离线安装配置。") from exc
    req = Request(
        url,
        headers={"User-Agent": "Testory-Platform-Tools-Installer/1.0", "Accept": "*/*"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=600) as resp:
        data = resp.read()
    if len(data) < 1024:
        raise RuntimeError("下载内容过小，可能不是有效的 zip 包")
    dest.write_bytes(data)


def _download_with_fallback(urls: List[str], dest: Path) -> str:
    errors: List[str] = []
    for url in urls:
        try:
            _download_url(url, dest)
            return url
        except (URLError, OSError, RuntimeError, TimeoutError) as exc:
            errors.append(f"{url[:48]}… → {exc}")
    raise RuntimeError(_offline_install_message(errors))


def _extract_platform_tools_zip(zip_path: Path, dest: Path) -> None:
    spec = _platform_spec()
    binary = spec.get("adb_binary") or ("adb.exe" if _platform_key() == "windows" else "adb")

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)

    tmp_root = Path(tempfile.mkdtemp(prefix="testory_adb_extract_"))
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_root)
        adb_files = list(tmp_root.rglob(binary))
        if not adb_files:
            raise RuntimeError(f"zip 中未找到 {binary}，请确认是官方 platform-tools 包")
        tool_dir = adb_files[0].parent
        shutil.copytree(tool_dir, dest)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    if not _adb_bundle_valid(dest):
        raise RuntimeError(f"解压后未找到 {binary}，目录：{dest}")


def _apply_adb_path(adb_exe: str) -> None:
    os.environ["ADB_PATH"] = adb_exe
    try:
        from mobile_env_config import save_mobile_defaults

        save_mobile_defaults({"adb_path": adb_exe})
    except Exception:
        pass


def get_android_platform_tools_catalog_entry() -> Dict[str, Any]:
    manifest = _manifest()
    spec = _platform_spec()
    local = _resolve_local_zip()
    sdk_dir = _resolve_system_sdk_platform_tools()
    urls = _collect_download_urls()
    return {
        "id": _PLUGIN_ID,
        "category": "mobile",
        "name": manifest.get("name") or "Android Platform-Tools (adb)",
        "browser": "any",
        "browser_label": "移动端",
        "icon": "fas fa-terminal",
        "icon_color": "#3DDC84",
        "version": manifest.get("version") or "1.0.0",
        "type": "runtime_bundle",
        "description": (
            manifest.get("description")
            or "安装 adb 到本机软件目录，移动端测试可自动识别 USB 设备。"
        ),
        "features": ["USB 设备列表", "投屏截图", "点按滑动", "自动配置 ADB_PATH"],
        "download_source": (
            "local" if local else ("sdk" if sdk_dir else ("url" if urls else "none"))
        ),
        "local_bundle_ready": bool(local),
        "sdk_platform_tools_ready": bool(sdk_dir),
        "sdk_platform_tools_path": str(sdk_dir) if sdk_dir else "",
        "download_url_configured": bool(urls),
        "size_mb_hint": spec.get("size_mb_hint"),
        "license": manifest.get("license"),
    }


def install_android_platform_tools(
    *,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """下载或复制 zip，解压并登记插件状态。"""
    from web_capture.plugin_market import _load_state, _save_state

    dest = android_tools_install_dir()
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
        sdk_src = _resolve_system_sdk_platform_tools()
        source_kind = "download"

        if local:
            _step(20, "使用本地安装包…")
            zip_path = local
            expected_sha = str(_platform_spec().get("sha256") or "").strip()
            _verify_sha256(zip_path, expected_sha)
            _step(70, "正在解压…")
            _extract_platform_tools_zip(zip_path, dest)
        elif sdk_src:
            progress.append({"label": "检测到本机 Android SDK", "percent": 30})
            _copy_platform_tools_dir(sdk_src, dest)
            source_kind = "sdk"
            _step(70, "已复制 adb 组件")
        else:
            urls = _collect_download_urls()
            if not urls:
                return {
                    "success": False,
                    "error": _offline_install_message(),
                }
            _step(15, "正在下载 adb 组件…")
            fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="testory_adb_")
            os.close(fd)
            tmp_zip = Path(tmp_name)
            used = _download_with_fallback(urls, tmp_zip)
            _step(45, "下载完成")
            zip_path = tmp_zip
            expected_sha = str(_platform_spec().get("sha256") or "").strip()
            _verify_sha256(zip_path, expected_sha)
            _step(70, "正在解压…")
            _extract_platform_tools_zip(zip_path, dest)
        adb_exe = resolve_adb_in_dir(dest)
        if not adb_exe:
            return {
                "success": False,
                "error": (
                    f"解压完成但未找到 adb（目录 {dest}）。"
                    "请确认 zip 为官方 platform-tools 包，或删除该目录后重试。"
                ),
            }

        _apply_adb_path(adb_exe)
        _step(90, "保存配置…")

        state = _load_state()
        plugins = state.setdefault("plugins", {})
        now = datetime.now(timezone.utc).isoformat()
        plugins[_PLUGIN_ID] = {
            "plugin_id": _PLUGIN_ID,
            "type": "runtime_bundle",
            "version": (_manifest().get("version") or "1.0.0"),
            "install_dir": str(dest),
            "adb_path": adb_exe,
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
            "adb_path": adb_exe,
            "progress": progress,
            "message": "adb 已安装。请打开「移动端测试」连接手机；若列表为空，请重新插拔 USB 并授权调试。",
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
