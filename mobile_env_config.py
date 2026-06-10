# -*- coding: utf-8 -*-
"""
Android 移动端自动化环境配置（从 .env 读取）。

ENABLE_MOBILE=1 启用模块
APPIUM_SERVER_URL=http://127.0.0.1:4723
ANDROID_DEVICE_NAME / ANDROID_APP_PACKAGE / ANDROID_APP_ACTIVITY
SCRCPY_PATH / ADB_PATH / MOBILE_MIRROR_FPS
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from client_config_store import load_client_config, save_client_config
except ImportError:

    def load_client_config() -> Dict[str, Any]:
        return {}

    def save_client_config(data: Dict[str, Any]) -> None:
        pass


def _truthy(name: str, default: str = "0") -> bool:
    return (os.environ.get(name, default) or default).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def mobile_enabled() -> bool:
    """是否启用移动端测试模块（ENABLE_MOBILE=1）。"""
    return _truthy("ENABLE_MOBILE", "0")


def mobile_driver_mode() -> str:
    """
    自动化驱动模式：
    auto — 执行步骤优先 Appium，投屏/手动操作用 ADB
    appium — 仅 Appium
    adb — 仅 ADB（适合纯点击滑动，无元素定位会话）
    """
    raw = (os.environ.get("MOBILE_DRIVER") or "auto").strip().lower()
    if raw not in ("auto", "appium", "adb"):
        return "auto"
    return raw


def auto_connect_on_studio() -> bool:
    return _truthy("MOBILE_AUTO_CONNECT", "1")


def appium_server_url() -> str:
    cfg = _load_mobile_defaults()
    env_url = (os.environ.get("APPIUM_SERVER_URL") or "").strip().rstrip("/")
    if env_url:
        return env_url
    return (cfg.get("appium_server_url") or "http://127.0.0.1:4723").strip().rstrip("/")


def adb_path() -> str:
    """
    解析 adb 可执行文件路径。优先级：
    1) 插件市场已安装的 Platform-Tools
    2) 环境变量 ADB_PATH（须为存在的文件）
    3) client_config.mobile_defaults.adb_path
    4) 回退字符串 adb（依赖系统 PATH）
    """
    try:
        from mobile_emulator_sdk_bundles import get_installed_emulator_sdk_home, resolve_adb_in_sdk

        if get_installed_emulator_sdk_home():
            sdk_adb = resolve_adb_in_sdk()
            if sdk_adb:
                return sdk_adb
    except Exception:
        pass
    try:
        from mobile_plugin_bundles import get_installed_adb_path

        bundled = get_installed_adb_path()
        if bundled:
            return bundled
    except Exception:
        pass
    env_path = (os.environ.get("ADB_PATH") or "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    cfg = _load_mobile_defaults()
    cfg_path = (cfg.get("adb_path") or "").strip()
    if cfg_path and Path(cfg_path).is_file():
        return cfg_path
    return env_path or "adb"


def adb_path_source() -> str:
    """供 UI 展示当前 adb 来源：plugin / env / config / default。"""
    try:
        from mobile_plugin_bundles import get_installed_adb_path

        if get_installed_adb_path():
            return "plugin"
    except Exception:
        pass
    env_path = (os.environ.get("ADB_PATH") or "").strip()
    if env_path and Path(env_path).is_file():
        return "env"
    cfg = _load_mobile_defaults()
    cfg_path = (cfg.get("adb_path") or "").strip()
    if cfg_path and Path(cfg_path).is_file():
        return "config"
    return "default"


def scrcpy_path() -> str:
    try:
        from mobile_scrcpy_bundles import get_installed_scrcpy_exe

        bundled = get_installed_scrcpy_exe()
        if bundled:
            return bundled
    except Exception:
        pass
    cfg = _load_mobile_defaults()
    env_path = (os.environ.get("SCRCPY_PATH") or "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    cfg_path = (cfg.get("scrcpy_path") or "").strip()
    if cfg_path and Path(cfg_path).is_file():
        return cfg_path
    return env_path or cfg_path or "scrcpy"


def mirror_fps() -> int:
    """adb screencap 轮询目标帧率（无线真机通常 5–15 实际帧）。"""
    try:
        return max(1, min(30, int(os.environ.get("MOBILE_MIRROR_FPS", "8"))))
    except ValueError:
        return 8


def resolve_emulator_gpu(requested: str = "", *, no_window: bool = True) -> str:
    """
    解析模拟器 -gpu 参数。
    无窗口 Windows 默认 swiftshader_indirect，避免 -gpu host 占满 GPU 导致整机卡顿。
    可通过 MOBILE_EMULATOR_GPU 强制指定（如 host / angle_indirect）。
    """
    explicit = (os.environ.get("MOBILE_EMULATOR_GPU") or "").strip()
    if explicit:
        return explicit
    req = (requested or "").strip().lower()
    if no_window and os.name == "nt" and (not req or req in ("host", "auto", "default")):
        return "swiftshader_indirect"
    return req or "host"


def scrcpy_mirror_fps() -> int:
    """scrcpy_ws H.264 视频流目标帧率（模拟器推荐 24–30）。"""
    raw = (os.environ.get("MOBILE_SCRCPY_FPS") or os.environ.get("MOBILE_MIRROR_FPS") or "24").strip()
    try:
        return max(15, min(60, int(raw)))
    except ValueError:
        return 24


def emulator_scrcpy_ws_enabled() -> bool:
    """模拟器是否启用 scrcpy_ws（默认开启；设 MOBILE_EMULATOR_SCRCPY=0 可关闭）。"""
    raw = (os.environ.get("MOBILE_EMULATOR_SCRCPY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def mirror_max_width() -> int:
    """投屏 JPEG 最大宽度（0=不缩放，原始 PNG）。"""
    try:
        return max(0, min(2160, int(os.environ.get("MOBILE_MIRROR_MAX_WIDTH", "720"))))
    except ValueError:
        return 720


def mirror_jpeg_quality() -> int:
    try:
        return max(40, min(95, int(os.environ.get("MOBILE_MIRROR_JPEG_QUALITY", "75"))))
    except ValueError:
        return 75


def mirror_format() -> str:
    """jpeg（默认，体积小）或 png。"""
    raw = (os.environ.get("MOBILE_MIRROR_FORMAT") or "jpeg").strip().lower()
    return raw if raw in ("jpeg", "jpg", "png") else "jpeg"


def android_sdk_home() -> str:
    """Android SDK 根目录（emulator / avdmanager）。"""
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        val = (os.environ.get(key) or "").strip()
        if val and Path(val).is_dir():
            return val
    cfg = _load_mobile_defaults()
    cfg_path = (cfg.get("android_sdk_home") or "").strip()
    if cfg_path and Path(cfg_path).is_dir():
        return cfg_path
    try:
        from mobile_emulator_sdk_bundles import get_installed_emulator_sdk_home

        plugin_sdk = get_installed_emulator_sdk_home()
        if plugin_sdk:
            return plugin_sdk
    except Exception:
        pass
    user = os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE") or ""
    if user:
        default = Path(user) / "AppData" / "Local" / "Android" / "Sdk"
        if default.is_dir():
            return str(default)
    return ""


def emulator_mode_enabled() -> bool:
    """模拟器优先模式（默认开启）。"""
    return _truthy("MOBILE_EMULATOR_MODE", "1")


def mirror_backend() -> str:
    """
    投屏后端：auto | scrcpy_ws | screencap
    auto — 模拟器( emulator-* ) 用 scrcpy_ws，真机用 screencap
    """
    raw = (os.environ.get("MOBILE_MIRROR_BACKEND") or "auto").strip().lower()
    if raw not in ("auto", "scrcpy_ws", "screencap"):
        return "auto"
    return raw


def scrcpy_bridge_port() -> int:
    try:
        return max(1024, min(65535, int(os.environ.get("MOBILE_SCRCPY_BRIDGE_PORT", "8767"))))
    except ValueError:
        return 8767


def scrcpy_bridge_url(client_host: str = "") -> str:
    """
    浏览器连接 scrcpy WebSocket 桥的 URL。
    client_host 优先（通常为 request.host / window.location.hostname），便于局域网访问。
    """
    port = scrcpy_bridge_port()
    host = (
        (client_host or "").strip()
        or (os.environ.get("MOBILE_SCRCPY_BRIDGE_PUBLIC_HOST") or "").strip()
        or (os.environ.get("MOBILE_SCRCPY_BRIDGE_HOST") or "").strip()
        or "127.0.0.1"
    )
    return f"ws://{host}:{port}"


def resolve_mirror_backend(udid: str = "") -> str:
    """根据设备 serial 解析实际投屏后端。"""
    backend = mirror_backend()
    if backend != "auto":
        return backend
    serial = (udid or "").strip()
    if serial.startswith("emulator-"):
        if emulator_scrcpy_ws_enabled():
            return "scrcpy_ws"
        return "screencap"
    if ":" in serial and serial.split(":")[0].replace(".", "").isdigit():
        return "screencap"
    return "screencap"


def default_emulator_avd() -> str:
    return (os.environ.get("MOBILE_EMULATOR_AVD") or "").strip() or (
        _load_mobile_defaults().get("emulator_avd") or ""
    ).strip()


def default_device_name() -> str:
    cfg = _load_mobile_defaults()
    return (
        (os.environ.get("ANDROID_DEVICE_NAME") or "").strip()
        or (cfg.get("device_name") or "").strip()
        or "Android"
    )


def default_app_package() -> str:
    cfg = _load_mobile_defaults()
    return (
        (os.environ.get("ANDROID_APP_PACKAGE") or "").strip()
        or (cfg.get("app_package") or "").strip()
    )


def default_app_activity() -> str:
    cfg = _load_mobile_defaults()
    return (
        (os.environ.get("ANDROID_APP_ACTIVITY") or "").strip()
        or (cfg.get("app_activity") or "").strip()
    )


def default_udid() -> str:
    cfg = _load_mobile_defaults()
    return (os.environ.get("ANDROID_UDID") or "").strip() or (cfg.get("udid") or "").strip()


def _load_mobile_defaults() -> Dict[str, Any]:
    cfg = load_client_config()
    raw = cfg.get("mobile_defaults")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def save_mobile_defaults(data: Dict[str, Any]) -> None:
    """持久化移动端默认配置到 client_config.json。"""
    existing = _load_mobile_defaults()
    existing.update({k: v for k, v in (data or {}).items() if v is not None})
    save_client_config({"mobile_defaults": existing})


def build_default_capabilities(udid: str = "") -> Dict[str, Any]:
    """构建 Appium capabilities 默认值。"""
    caps: Dict[str, Any] = {
        "platformName": "Android",
        "automationName": "UiAutomator2",
        "deviceName": default_device_name(),
        "noReset": True,
        "newCommandTimeout": 300,
    }
    pkg = default_app_package()
    act = default_app_activity()
    if pkg:
        caps["appPackage"] = pkg
    if act:
        caps["appActivity"] = act
    resolved_udid = (udid or default_udid()).strip()
    if resolved_udid:
        caps["udid"] = resolved_udid
    return caps


def appium_client_available() -> bool:
    try:
        import appium  # noqa: F401

        return True
    except ImportError:
        return False


def mobile_runtime_available() -> bool:
    return mobile_enabled() and appium_client_available()


def mobile_runtime_unavailable_reason() -> Optional[str]:
    if not mobile_enabled():
        return "移动端测试未启用，请在 .env 中设置 ENABLE_MOBILE=1"
    if not appium_client_available():
        return "未安装 Appium-Python-Client，请执行 pip install -r requirements-mobile-optional.txt"
    return None


def public_config() -> Dict[str, Any]:
    """供 UI / API 使用的公开配置。"""
    from mobile_device_profiles import list_frame_presets

    reason = mobile_runtime_unavailable_reason()
    return {
        "enabled": mobile_enabled(),
        "runtime_available": mobile_runtime_available(),
        "unavailable_reason": reason or "",
        "driver_mode": mobile_driver_mode(),
        "auto_connect": auto_connect_on_studio(),
        "appium_server_url": appium_server_url(),
        "adb_path": adb_path(),
        "adb_path_source": adb_path_source(),
        "adb_plugin_installed": adb_path_source() == "plugin",
        "scrcpy_path": scrcpy_path(),
        "mirror_fps": mirror_fps(),
        "scrcpy_mirror_fps": scrcpy_mirror_fps(),
        "emulator_scrcpy_ws": emulator_scrcpy_ws_enabled(),
        "mirror_max_width": mirror_max_width(),
        "mirror_jpeg_quality": mirror_jpeg_quality(),
        "mirror_format": mirror_format(),
        "mirror_backend": mirror_backend(),
        "emulator_mode": emulator_mode_enabled(),
        "android_sdk_home": android_sdk_home(),
        "scrcpy_bridge_url": scrcpy_bridge_url(),
        "scrcpy_bridge_port": scrcpy_bridge_port(),
        "default_emulator_avd": default_emulator_avd(),
        "device_name": default_device_name(),
        "app_package": default_app_package(),
        "app_activity": default_app_activity(),
        "udid": default_udid(),
        "defaults": _load_mobile_defaults(),
        "device_frame_presets": list_frame_presets(),
        "backends": [
            {"id": "appium", "label": "Appium + UiAutomator2", "default": True},
            {"id": "adb", "label": "ADB 直连（投屏/点击，零 Appium）"},
            {"id": "uiautomator2", "label": "uiautomator2（可选 pip install uiautomator2）"},
            {"id": "airtest", "label": "图像模板（OpenCV / tap_image，点屏录制可用）"},
        ],
        "hint": (
            "推荐：在「模拟器」区启动本机 AVD，高帧率投屏；真机 USB/无线请展开「真机兼容」连接。"
            "Appium 仅在运行自动化步骤时需要。"
        ),
    }
