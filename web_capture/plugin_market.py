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
_LEGACY_EXTENSIONS_DIRNAME = "NewUITestPlatform"


def _legacy_extensions_root() -> Optional[Path]:
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if not local:
        return None
    legacy = Path(local) / _LEGACY_EXTENSIONS_DIRNAME / "extensions"
    return legacy if legacy.is_dir() else None


def _maybe_migrate_legacy_extensions(target: Path) -> None:
    """若新目录为空且存在旧品牌目录，一次性复制扩展文件。"""
    marker = target.parent / ".extensions_migrated"
    if marker.is_file():
        return
    legacy = _legacy_extensions_root()
    if legacy is None:
        marker.write_text("no_legacy\n", encoding="utf-8")
        return
    try:
        target.mkdir(parents=True, exist_ok=True)
        has_new = any(target.iterdir())
    except OSError:
        has_new = False
    if has_new:
        marker.write_text("skipped_existing\n", encoding="utf-8")
        return
    try:
        if not any(legacy.iterdir()):
            marker.write_text("legacy_empty\n", encoding="utf-8")
            return
        shutil.copytree(legacy, target, dirs_exist_ok=True)
        marker.write_text(f"migrated_from={legacy}\n", encoding="utf-8")
    except OSError:
        pass


def software_extensions_root() -> Path:
    """软件扩展安装根目录（桌面版优先 UAT_DATA_DIR/extensions）。"""
    override = (os.environ.get("TESTORY_EXTENSIONS_ROOT") or "").strip()
    if override:
        root = Path(override)
        root.mkdir(parents=True, exist_ok=True)
        return root

    uat = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if uat:
        root = Path(uat) / "extensions"
        _maybe_migrate_legacy_extensions(root)
        root.mkdir(parents=True, exist_ok=True)
        return root

    if getattr(sys, "frozen", False):
        try:
            from install_paths import resolve_install_root

            root = resolve_install_root() / "extensions"
        except ImportError:
            root = Path(sys.executable).resolve().parent / "extensions"
        root.mkdir(parents=True, exist_ok=True)
        return root

    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        root = Path(local) / "Testory" / "extensions"
        _maybe_migrate_legacy_extensions(root)
        root.mkdir(parents=True, exist_ok=True)
        return root

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


_MOBILE_RUNTIME_PLUGIN_IDS = frozenset(
    {
        "mobile-android-platform-tools",
        "mobile-testory-assistant",
    }
)

# 组件类型 ID（由 components_manager 管理）
_COMPONENT_IDS = frozenset({'chromium', 'opencv'})


def _mobile_runtime_installed(plugin_id: str) -> bool:
    """移动端运行时包：必须存在对应可执行文件，空目录或仅 JSON 登记不算已安装。"""
    pid = (plugin_id or "").strip()
    try:
        if pid == "mobile-android-platform-tools":
            from mobile_plugin_bundles import get_installed_adb_path

            return bool(get_installed_adb_path())
        if pid == "mobile-testory-assistant":
            from mobile_assistant_bundles import (
                assistant_installed_on_device,
                is_assistant_prepared,
                resolve_target_udid_for_push,
            )

            udid = resolve_target_udid_for_push()
            prepared = is_assistant_prepared()
            on_device = assistant_installed_on_device(udid) if udid else False
            return prepared or on_device
    except Exception:
        return False
    return False


def prune_stale_plugin_records() -> int:
    """移除登记在案但磁盘上已不存在的插件记录。"""
    state = _load_state()
    plugins = state.setdefault("plugins", {})
    removed: List[str] = []
    for pid in list(plugins.keys()):
        if not is_plugin_installed(pid):
            plugins.pop(pid, None)
            removed.append(pid)
    if removed:
        _save_state(state)
    return len(removed)


def is_plugin_installed(plugin_id: str) -> bool:
    pid = (plugin_id or "").strip()
    # 组件类型由 components_manager 管理
    if pid in _COMPONENT_IDS:
        try:
            from components_manager import is_installed as _comp_installed
            return _comp_installed(pid)
        except Exception:
            return False
    if pid in _MOBILE_RUNTIME_PLUGIN_IDS:
        return _mobile_runtime_installed(pid)

    rec = (_load_state().get("plugins") or {}).get(pid) or {}
    if not rec:
        return False
    if rec.get("type") == "bookmarklet":
        return bool(rec.get("installed"))
    if rec.get("type") == "runtime_bundle":
        if pid in _MOBILE_RUNTIME_PLUGIN_IDS:
            return _mobile_runtime_installed(pid)
        install_dir = str(rec.get("install_dir") or "")
        return bool(install_dir) and Path(install_dir).is_dir()
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
    # 组件类型由 components_manager 管理
    if pid in _COMPONENT_IDS:
        out = dict(plugin)
        try:
            from components_manager import is_installed as _comp_installed
            installed = _comp_installed(pid)
        except Exception:
            installed = False
        out["installed"] = installed
        out["connected"] = False
        out["install_dir"] = ""
        out["installed_at"] = ""
        out["status_label"] = "已安装" if installed else "未安装"
        out["status_tone"] = "ok" if installed else "muted"
        return out
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
        if pid == "mobile-android-platform-tools":
            try:
                from mobile_plugin_bundles import get_installed_adb_path

                ap = get_installed_adb_path()
                if ap:
                    out["adb_path"] = ap
            except Exception:
                pass
        if pid == "mobile-testory-assistant":
            try:
                from mobile_assistant_bundles import (
                    assistant_installed_on_device,
                    is_assistant_prepared,
                    resolve_target_udid_for_push,
                )

                udid = resolve_target_udid_for_push()
                prepared = is_assistant_prepared()
                on_device = assistant_installed_on_device(udid) if udid else False
                out["assistant_prepared"] = prepared
                out["assistant_on_device"] = on_device
                out["device_push_pending"] = prepared and not on_device
                if prepared and on_device:
                    out["status_label"] = "已安装（设备就绪）"
                    out["status_tone"] = "ok"
                elif prepared:
                    out["status_label"] = "已准备（待连接设备推送）"
                    out["status_tone"] = "warn"
                elif on_device:
                    out["status_label"] = "设备已安装"
                    out["status_tone"] = "ok"
            except Exception:
                pass
    else:
        out["status_label"] = "未安装"
        out["status_tone"] = "muted"
        if pid == "mobile-android-platform-tools" and not plugin.get("local_bundle_ready"):
            if not plugin.get("download_url_configured"):
                out["status_label"] = "待配置安装包"
                out["status_tone"] = "warn"
        if pid == "mobile-testory-assistant" and not plugin.get("local_bundle_ready"):
            out["status_label"] = "待配置 APK"
            out["status_tone"] = "warn"
    return out


def _all_catalog_items(*, platform_origin: str = "") -> List[Dict[str, Any]]:
    """网页捕获 + 移动端等全部可安装插件（安装与列表须共用此目录）。"""
    items = list(_catalog_raw(platform_origin=platform_origin))
    try:
        from mobile_plugin_bundles import get_android_platform_tools_catalog_entry

        items.append(get_android_platform_tools_catalog_entry())
    except Exception:
        pass
    try:
        from mobile_assistant_bundles import get_testory_assistant_catalog_entry

        items.append(get_testory_assistant_catalog_entry())
    except Exception:
        pass
    # 运行组件（Chromium、OpenCV 等）
    try:
        from components_manager import COMPONENT_DEFS

        for _cid, _cdef in COMPONENT_DEFS.items():
            items.append(
                {
                    "id": _cid,
                    "category": "component",
                    "name": _cdef.get("name", _cid),
                    "icon": _cdef.get("icon", "📦"),
                    "icon_color": "#8B5CF6",
                    "version": _cdef.get("version", "1.0.0"),
                    "type": "component",
                    "description": _cdef.get("description", ""),
                    "features": _cdef.get("required_by", []),
                    "estimated_size_mb": _cdef.get("estimated_size_mb", 0),
                }
            )
    except Exception:
        pass
    return items


def get_plugin_catalog(*, platform_origin: str = "") -> List[Dict[str, Any]]:
    prune_stale_plugin_records()
    return [enrich_plugin_status(p) for p in _all_catalog_items(platform_origin=platform_origin)]


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


def install_plugin(plugin_id: str, *, background: Optional[bool] = None) -> Dict[str, Any]:
    """一键安装。移动端运行时包默认后台安装，切换页面不中断。"""
    pid = (plugin_id or "").strip()
    catalog = {p["id"]: p for p in _all_catalog_items()}
    meta = catalog.get(pid)
    if not meta:
        return {"success": False, "error": f"未知插件: {pid or '(空)'}"}

    if background is None:
        try:
            from plugin_install_jobs import should_install_in_background

            background = should_install_in_background(pid)
        except Exception:
            background = False

    # 组件类型始终后台安装（耗时较长）
    if background is None and meta.get("type") == "component":
        background = True

    if background and meta.get("type") in ("runtime_bundle", "component"):
        try:
            from plugin_install_jobs import start_install_job

            job_id = start_install_job(pid)
            return {
                "success": True,
                "async": True,
                "job_id": job_id,
                "plugin_id": pid,
                "message": "正在后台安装，您可以切换其他页面；稍后在插件市场查看进度。",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    return install_plugin_sync(pid)


def install_plugin_sync(
    plugin_id: str,
    *,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """同步安装（供后台任务或快速扩展安装调用）。"""
    pid = (plugin_id or "").strip()
    catalog = {p["id"]: p for p in _all_catalog_items()}
    meta = catalog.get(pid)
    if not meta:
        return {"success": False, "error": f"未知插件: {pid or '(空)'}"}

    def _progress(percent: int, label: str) -> None:
        if progress_cb:
            try:
                progress_cb(int(percent), label)
            except Exception:
                pass

    state = _load_state()
    plugins = state.setdefault("plugins", {})
    now = datetime.now(timezone.utc).isoformat()

    # 组件类型由 components_manager 安装
    if meta.get("type") == "component":
        try:
            from components_manager import install as _comp_install
            def _comp_progress(_status: str, _percent: float, _message: str) -> None:
                _progress(int(_percent), _message)
            ok = _comp_install(pid, progress=_comp_progress)
            if ok:
                return {
                    "success": True,
                    "plugin_id": pid,
                    "installed": True,
                    "message": "组件安装完成。平台将在需要时自动调用该组件。",
                }
            return {"success": False, "error": "组件安装失败，请检查网络后重试"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    if meta.get("type") == "runtime_bundle":
        if pid == "mobile-android-platform-tools":
            try:
                from mobile_plugin_bundles import install_android_platform_tools

                return install_android_platform_tools(progress_cb=progress_cb)
            except Exception as exc:
                return {"success": False, "error": str(exc)}
        if pid == "mobile-testory-assistant":
            try:
                from mobile_assistant_bundles import (
                    assistant_installed_on_device,
                    is_assistant_prepared,
                    install_testory_assistant,
                    resolve_target_udid_for_push,
                )

                udid = resolve_target_udid_for_push()
                result = install_testory_assistant(udid, progress_cb=_progress)
                if result.get("success"):
                    plugins[pid] = {
                        "plugin_id": pid,
                        "type": "runtime_bundle",
                        "version": meta.get("version") or "1.0.0",
                        "installed_at": now,
                        "package": result.get("package") or "com.testory.assistant",
                        "apk_path": result.get("apk_path") or "",
                        "device_push_pending": bool(result.get("device_push_pending")),
                        "assistant_on_device": bool(
                            result.get("assistant_on_device")
                            or (udid and assistant_installed_on_device(udid))
                        ),
                    }
                    _save_state(state)
                return result
            except Exception as exc:
                return {"success": False, "error": str(exc)}
        return {"success": False, "error": "未知的运行时插件"}

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











