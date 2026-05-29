# -*- coding: utf-8 -*-
"""插件市场：网页捕获浏览器扩展安装与状态检测。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
_CHROME_SRC = _ROOT / "browser_extension" / "chrome"
_STATE_FILENAME = "installed_plugins.json"


def software_extensions_root() -> Path:
    """软件扩展安装根目录（打包后优先使用程序目录）。"""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent / "extensions"
    else:
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            root = Path(local) / "NewUITestPlatform" / "extensions"
        else:
            root = _ROOT / "extensions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_path() -> Path:
    return software_extensions_root() / _STATE_FILENAME


def _load_state() -> Dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return {"plugins": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("plugins"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"plugins": {}}


def _save_state(state: Dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def default_install_dir(browser: str = "chrome") -> str:
    b = (browser or "chrome").strip().lower()
    return str(software_extensions_root() / b)


def _catalog_raw(*, platform_origin: str = "") -> List[Dict[str, Any]]:
    origin = (platform_origin or "").rstrip("/")
    return [
        {
            "id": "web-capture-chrome",
            "category": "web_capture",
            "name": "UAT 网页捕获助手",
            "browser": "chrome",
            "browser_label": "Google Chrome",
            "icon": "fab fa-chrome",
            "icon_color": "#4285F4",
            "version": "1.0.0",
            "type": "extension",
            "description": "在 Chrome 中注入网页元素捕获脚本，与平台捕获器联动，支持悬停高亮与多策略定位。",
            "features": ["悬停高亮", "一键武装", "WebSocket 回传", "支持 iframe"],
        },
        {
            "id": "web-capture-edge",
            "category": "web_capture",
            "name": "UAT 网页捕获助手",
            "browser": "edge",
            "browser_label": "Microsoft Edge",
            "icon": "fab fa-edge",
            "icon_color": "#0078D7",
            "version": "1.0.0",
            "type": "extension",
            "description": "适用于 Edge 的网页元素捕获扩展，与平台捕获器联动。",
            "features": ["悬停高亮", "一键武装", "WebSocket 回传", "支持 iframe"],
        },
        {
            "id": "web-capture-firefox",
            "category": "web_capture",
            "name": "UAT 网页捕获助手",
            "browser": "firefox",
            "browser_label": "Mozilla Firefox",
            "icon": "fab fa-firefox-browser",
            "icon_color": "#FF7139",
            "version": "1.0.0",
            "type": "extension",
            "description": "Firefox 版网页元素捕获扩展。",
            "features": ["悬停高亮", "WebSocket 回传", "MV3"],
        },
        {
            "id": "web-capture-bookmarklet",
            "category": "web_capture",
            "name": "UAT 网页捕获书签",
            "browser": "any",
            "browser_label": "通用（任意浏览器）",
            "icon": "fas fa-bookmark",
            "icon_color": "#dc2626",
            "version": "1.0.0",
            "type": "bookmarklet",
            "description": "无需浏览器扩展时的备用捕获方式，由平台在网页捕获会话中自动启用。",
            "features": ["免扩展", "跨浏览器", "与捕获器同步"],
        },
    ]


def _extension_files_valid(install_dir: str) -> bool:
    return (Path(install_dir) / "manifest.json").is_file()


def is_plugin_installed(plugin_id: str) -> bool:
    pid = (plugin_id or "").strip()
    rec = (_load_state().get("plugins") or {}).get(pid) or {}
    if not rec:
        return False
    if rec.get("type") == "bookmarklet":
        return bool(rec.get("installed"))
    install_dir = str(rec.get("install_dir") or "")
    return bool(install_dir) and _extension_files_valid(install_dir)


def get_installed_plugin_ids() -> List[str]:
    return [pid for pid in (_load_state().get("plugins") or {}) if is_plugin_installed(pid)]


_BROWSER_LABELS = {
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "firefox": "Mozilla Firefox",
}


def get_preferred_browser_for_capture() -> str:
    """返回已安装的 Chromium 系浏览器优先级：edge > chrome。"""
    for pid, browser in (("web-capture-edge", "edge"), ("web-capture-chrome", "chrome")):
        if is_plugin_installed(pid):
            return browser
    if is_plugin_installed("web-capture-firefox"):
        return "firefox"
    return ""


def get_capture_browser_options() -> List[Dict[str, str]]:
    """已安装、可用于网页捕获的浏览器列表（供通用捕获器 UI 选择）。"""
    out: List[Dict[str, str]] = []
    for pid, browser in (
        ("web-capture-edge", "edge"),
        ("web-capture-chrome", "chrome"),
        ("web-capture-firefox", "firefox"),
    ):
        if is_plugin_installed(pid):
            out.append(
                {
                    "id": browser,
                    "label": _BROWSER_LABELS.get(browser, browser),
                    "plugin_id": pid,
                }
            )
    return out


def browser_label(browser: str) -> str:
    return _BROWSER_LABELS.get((browser or "").strip().lower(), browser or "")


def _extension_connected() -> bool:
    try:
        from web_capture.extension_bridge import get_extension_status

        return bool(get_extension_status().get("extension_connected"))
    except Exception:
        return False


def enrich_plugin_status(plugin: Dict[str, Any]) -> Dict[str, Any]:
    """为目录项附加安装状态（不以浏览器连接作为安装前置条件）。"""
    pid = plugin.get("id") or ""
    rec = (_load_state().get("plugins") or {}).get(pid) or {}
    installed = is_plugin_installed(pid)
    out = dict(plugin)
    out["installed"] = installed
    out["connected"] = _extension_connected() if installed and plugin.get("type") == "extension" else False
    out["install_dir"] = rec.get("install_dir") or ""
    out["installed_at"] = rec.get("installed_at") or ""
    if installed:
        out["status_label"] = "已安装"
        out["status_tone"] = "ok"
    else:
        out["status_label"] = "未安装"
        out["status_tone"] = "muted"
    return out


def get_plugin_catalog(*, platform_origin: str = "") -> List[Dict[str, Any]]:
    return [enrich_plugin_status(p) for p in _catalog_raw(platform_origin=platform_origin)]


def _copy_extension(browser: str, dest: str) -> None:
    if not _CHROME_SRC.is_dir():
        raise FileNotFoundError(f"扩展源码不存在: {_CHROME_SRC}")
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(_CHROME_SRC):
        sp = _CHROME_SRC / name
        dp = Path(dest) / name
        if sp.is_dir():
            if dp.exists():
                shutil.rmtree(dp)
            shutil.copytree(sp, dp)
        else:
            shutil.copy2(sp, dp)
    if browser == "firefox":
        _patch_firefox_manifest(Path(dest) / "manifest.json")


def _patch_firefox_manifest(manifest_path: Path) -> None:
    if not manifest_path.is_file():
        return
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["browser_specific_settings"] = {
            "gecko": {
                "id": "uat-web-capture@newuitestplatform.local",
                "strict_min_version": "109.0",
            }
        }
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def try_launch_browser_with_extension(
    browser: str, install_dir: str, *, start_url: str = ""
) -> Dict[str, Any]:
    """使用独立配置文件启动捕获专用浏览器并加载扩展。"""
    b = (browser or "").strip().lower()
    if b not in ("chrome", "edge"):
        return {"success": True, "launched": False, "message": "当前浏览器需手动打开后自动关联"}
    if not _extension_files_valid(install_dir):
        return {"success": False, "launched": False, "error": "扩展文件不完整"}
    try:
        from web_capture.cdp_browser import detect_browser_executable

        exe = detect_browser_executable(b)
        if not exe:
            return {
                "success": False,
                "launched": False,
                "error": f"未检测到 {b} 浏览器，请先在插件市场安装对应插件",
            }
        profile_dir = software_extensions_root() / "profiles" / b
        profile_dir.mkdir(parents=True, exist_ok=True)
        open_url = (start_url or "").strip() or "about:blank"
        subprocess.Popen(
            [
                exe,
                f"--user-data-dir={profile_dir}",
                f"--load-extension={install_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                open_url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        label = "Edge" if b == "edge" else "Chrome"
        return {
            "success": True,
            "launched": True,
            "message": f"已打开{label}捕获专用窗口，请在该窗口打开待测页面",
        }
    except Exception as exc:
        return {"success": False, "launched": False, "error": str(exc)}


def ensure_uat_capture_browser(browser: str = "", *, shell_url: str = "") -> Dict[str, Any]:
    """打开捕获专用浏览器（仅在扩展未连接时启动新窗口）。"""
    chosen = (browser or "").strip().lower() or get_preferred_browser_for_capture()
    if _extension_connected():
        return {
            "success": True,
            "action": "connected",
            "launched": False,
            "browser": chosen,
            "browser_label": browser_label(chosen),
        }
    browser = chosen
    if not browser:
        return {
            "success": False,
            "error": "请先在插件市场安装 Chrome 或 Edge 网页捕获插件",
        }
    if browser == "firefox":
        return {
            "success": True,
            "action": "firefox",
            "launched": False,
            "browser": browser,
            "browser_label": browser_label(browser),
            "message": "请在 Firefox 中打开待测页面并确保扩展已启用",
        }
    pid = f"web-capture-{browser}"
    rec = (_load_state().get("plugins") or {}).get(pid) or {}
    install_dir = str(rec.get("install_dir") or default_install_dir(browser))
    if not _extension_files_valid(install_dir):
        return {
            "success": False,
            "error": f"请先在插件市场安装 {browser} 网页捕获插件",
        }
    try:
        _copy_extension(browser, install_dir)
    except Exception:
        pass
    installed_ids = {o["id"] for o in get_capture_browser_options()}
    if browser not in installed_ids:
        return {
            "success": False,
            "error": f"未安装 {browser_label(browser)} 捕获插件，请先在插件市场安装",
        }
    launch = try_launch_browser_with_extension(browser, install_dir, start_url=shell_url)
    if not launch.get("success"):
        return launch
    return {
        "success": True,
        "action": "launch",
        "launched": bool(launch.get("launched")),
        "browser": browser,
        "browser_label": browser_label(browser),
        "message": launch.get("message") or "",
    }


def ensure_capture_extension_ready() -> Dict[str, Any]:
    """向后兼容。"""
    return ensure_uat_capture_browser()


def install_plugin(plugin_id: str) -> Dict[str, Any]:
    """一键安装到软件目录并登记状态。"""
    pid = (plugin_id or "").strip()
    catalog = {p["id"]: p for p in _catalog_raw()}
    meta = catalog.get(pid)
    if not meta:
        return {"success": False, "error": "未知插件"}

    state = _load_state()
    plugins = state.setdefault("plugins", {})
    now = datetime.now(timezone.utc).isoformat()

    if meta.get("type") == "bookmarklet":
        plugins[pid] = {
            "plugin_id": pid,
            "type": "bookmarklet",
            "version": meta.get("version") or "1.0.0",
            "installed": True,
            "installed_at": now,
        }
        _save_state(state)
        return {
            "success": True,
            "plugin_id": pid,
            "installed": True,
            "progress": [
                {"label": "启用书签捕获", "percent": 100},
            ],
            "message": "安装完成。使用「网页捕获」时将自动启用书签捕获方式。",
        }

    browser = str(meta.get("browser") or "chrome").lower()
    if browser not in ("chrome", "edge", "firefox"):
        return {"success": False, "error": "不支持的浏览器类型"}
    dest = default_install_dir(browser)
    try:
        _copy_extension(browser, dest)
    except OSError as exc:
        return {"success": False, "error": str(exc)}

    plugins[pid] = {
        "plugin_id": pid,
        "type": "extension",
        "browser": browser,
        "version": meta.get("version") or "1.0.0",
        "install_dir": dest,
        "installed_at": now,
    }
    _save_state(state)

    return {
        "success": True,
        "plugin_id": pid,
        "browser": browser,
        "install_dir": dest,
        "installed": True,
        "progress": [
            {"label": "准备安装目录", "percent": 30},
            {"label": "复制扩展文件", "percent": 70},
            {"label": "登记插件状态", "percent": 100},
        ],
        "message": "安装完成。使用「网页捕获」时平台将自动加载该插件，无需手动操作浏览器。",
    }


# 向后兼容旧 API 名称
def prepare_extension(browser: str = "chrome", *, target_dir: str = "") -> Dict[str, Any]:
    pid = f"web-capture-{(browser or 'chrome').strip().lower()}"
    if target_dir:
        b = (browser or "chrome").strip().lower()
        try:
            _copy_extension(b, target_dir)
            return {"success": True, "install_dir": target_dir, "browser": b}
        except OSError as exc:
            return {"success": False, "error": str(exc)}
    return install_plugin(pid)
