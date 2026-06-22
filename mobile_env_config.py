# -*- coding: utf-8 -*-
"""
Android 移动端环境配置（从 .env 读取）。

ENABLE_MOBILE=1 启用模块
APPIUM_SERVER_URL=http://127.0.0.1:4723
ANDROID_DEVICE_NAME / ANDROID_APP_PACKAGE / ANDROID_APP_ACTIVITY
ADB_PATH — 第三方模拟器 adb 路径（优先于插件市场 adb）
MOBILE_DRIVER=auto|appium|adb|plugin
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
    auto — 元素定位步骤用 Appium，坐标/滑动可回退 ADB
    appium — 仅 Appium
    adb — 仅 ADB（坐标点击/滑动，无需 Appium Server）
    """
    raw = (os.environ.get("MOBILE_DRIVER") or "plugin").strip().lower()
    if raw not in ("auto", "appium", "adb", "plugin"):
        return "plugin"
    return raw


def auto_connect_on_studio() -> bool:
    return _truthy("MOBILE_AUTO_CONNECT", "1")


def emulator_mode_enabled() -> bool:
    """是否允许连接 adb 枚举到的模拟器（emulator-* 等）。"""
    return _truthy("MOBILE_EMULATOR_MODE", "1")


def appium_server_url() -> str:
    cfg = _load_mobile_defaults()
    env_url = (os.environ.get("APPIUM_SERVER_URL") or "").strip().rstrip("/")
    if env_url:
        return env_url
    return (cfg.get("appium_server_url") or "http://127.0.0.1:4723").strip().rstrip("/")


def adb_path() -> str:
    """
    解析 adb 可执行文件路径。优先级：
    1) 环境变量 ADB_PATH（第三方模拟器 adb，须为存在的文件）
    2) client_config.mobile_defaults.adb_path
    3) 插件市场已安装的 Platform-Tools
    4) 回退字符串 adb（依赖系统 PATH）
    """
    env_path = (os.environ.get("ADB_PATH") or "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    cfg = _load_mobile_defaults()
    cfg_path = (cfg.get("adb_path") or "").strip()
    if cfg_path and Path(cfg_path).is_file():
        return cfg_path
    try:
        from mobile_plugin_bundles import get_installed_adb_path

        bundled = get_installed_adb_path()
        if bundled:
            return bundled
    except Exception:
        pass
    return env_path or "adb"


def adb_path_source() -> str:
    """供 UI 展示当前 adb 来源：env / config / plugin / default。"""
    env_path = (os.environ.get("ADB_PATH") or "").strip()
    if env_path and Path(env_path).is_file():
        return "env"
    cfg = _load_mobile_defaults()
    cfg_path = (cfg.get("adb_path") or "").strip()
    if cfg_path and Path(cfg_path).is_file():
        return "config"
    try:
        from mobile_plugin_bundles import get_installed_adb_path

        if get_installed_adb_path():
            return "plugin"
    except Exception:
        pass
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


def mobile_screenshot_shrink_factor() -> float:
    """VLM 截图缩小倍数（Midscene screenshotShrinkFactor 对标，默认 2）。"""
    raw = (
        os.environ.get("MOBILE_SCREENSHOT_SHRINK_FACTOR")
        or os.environ.get("LOCATOR_VLM_SHRINK_FACTOR")
        or "2"
    ).strip()
    try:
        f = float(raw)
    except ValueError:
        f = 2.0
    return max(1.0, min(f, 4.0))


def mobile_wait_after_action_ms() -> int:
    """动作后等待毫秒（Midscene waitAfterAction 对标）。"""
    raw = (
        os.environ.get("MOBILE_WAIT_AFTER_ACTION_MS")
        or os.environ.get("MOBILE_PLAYGROUND_WAIT_MS")
        or "300"
    ).strip()
    try:
        return max(0, min(3000, int(raw)))
    except ValueError:
        return 300


def mirror_fps() -> int:
    try:
        return max(1, min(30, int(os.environ.get("MOBILE_MIRROR_FPS", "8"))))
    except ValueError:
        return 8


def scrcpy_mirror_fps() -> int:
    raw = (os.environ.get("MOBILE_SCRCPY_FPS") or os.environ.get("MOBILE_MIRROR_FPS") or "24").strip()
    try:
        return max(15, min(60, int(raw)))
    except ValueError:
        return 24


def device_scrcpy_ws_enabled() -> bool:
    raw = (
        os.environ.get("MOBILE_DEVICE_SCRCPY")
        or os.environ.get("MOBILE_EMULATOR_SCRCPY")
        or "1"
    ).strip().lower()
    return raw not in ("0", "false", "no", "off")


def scrcpy_available() -> bool:
    path = scrcpy_path()
    if not path or path == "scrcpy":
        import shutil

        return bool(shutil.which("scrcpy"))
    return Path(path).is_file()


def scrcpy_max_size() -> int:
    """scrcpy 视频长边上限；0=原生分辨率（不缩放）。"""
    raw = (os.environ.get("MOBILE_SCRCPY_MAX_SIZE") or "0").strip()
    try:
        val = int(raw)
    except ValueError:
        return 0
    if val <= 0:
        return 0
    return max(480, min(3840, val))


def mirror_max_width() -> int:
    try:
        return max(0, min(3840, int(os.environ.get("MOBILE_MIRROR_MAX_WIDTH", "0"))))
    except ValueError:
        return 0


def mirror_jpeg_quality() -> int:
    try:
        return max(40, min(95, int(os.environ.get("MOBILE_MIRROR_JPEG_QUALITY", "75"))))
    except ValueError:
        return 75


def mirror_format() -> str:
    raw = (os.environ.get("MOBILE_MIRROR_FORMAT") or "jpeg").strip().lower()
    return raw if raw in ("jpeg", "jpg", "png") else "jpeg"


def mirror_backend() -> str:
    raw = (os.environ.get("MOBILE_MIRROR_BACKEND") or "auto").strip().lower()
    if raw not in ("auto", "scrcpy_ws", "screencap"):
        return "auto"
    return raw


def scrcpy_auto_fallback_screencap() -> bool:
    """scrcpy 客户端长时间无画面时是否自动降级为 adb screencap（默认否）。"""
    raw = (os.environ.get("MOBILE_SCRCPY_AUTO_FALLBACK") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def scrcpy_bridge_port() -> int:
    try:
        return max(1024, min(65535, int(os.environ.get("MOBILE_SCRCPY_BRIDGE_PORT", "8767"))))
    except ValueError:
        return 8767


def scrcpy_bridge_url(client_host: str = "") -> str:
    port = scrcpy_bridge_port()
    host = (
        (client_host or "").strip()
        or (os.environ.get("MOBILE_SCRCPY_BRIDGE_PUBLIC_HOST") or "").strip()
        or (os.environ.get("MOBILE_SCRCPY_BRIDGE_HOST") or "").strip()
        or "127.0.0.1"
    )
    return f"ws://{host}:{port}"


def resolve_mirror_backend(udid: str = "") -> str:
    del udid
    backend = mirror_backend()
    if backend != "auto":
        return backend
    if device_scrcpy_ws_enabled() and scrcpy_available():
        return "scrcpy_ws"
    return "screencap"


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
    """移动端执行运行时是否可用。"""
    if not mobile_enabled():
        return False
    if mobile_driver_mode() in ("adb", "plugin"):
        try:
            from mobile_agent_client import mobile_agent_enabled

            return mobile_agent_enabled()
        except ImportError:
            return True
    return appium_client_available()


def mobile_runtime_unavailable_reason() -> Optional[str]:
    if not mobile_enabled():
        return "移动端测试未启用，请在 .env 中设置 ENABLE_MOBILE=1"
    if mobile_driver_mode() in ("adb", "plugin"):
        try:
            from mobile_agent_client import mobile_agent_enabled

            if not mobile_agent_enabled():
                return "移动端 Agent 未配置，请确认 TestoryMobileGw 已启动"
        except ImportError:
            pass
        return None
    if not appium_client_available():
        return "未安装 Appium-Python-Client，请执行 pip install -r requirements-mobile-optional.txt"
    return None


def requires_appium_for_execution() -> bool:
    """当前驱动模式是否必须在执行前连接 Appium。"""
    return mobile_driver_mode() in ("auto", "appium")


def public_config() -> Dict[str, Any]:
    """供 UI / API 使用的公开配置。"""
    from mobile_device_profiles import list_frame_presets

    reason = mobile_runtime_unavailable_reason()
    return {
        "enabled": mobile_enabled(),
        "runtime_available": mobile_runtime_available(),
        "unavailable_reason": reason or "",
        "driver_mode": mobile_driver_mode(),
        "emulator_mode": emulator_mode_enabled(),
        "auto_connect": auto_connect_on_studio(),
        "appium_server_url": appium_server_url(),
        "adb_path": adb_path(),
        "adb_path_source": adb_path_source(),
        "adb_plugin_installed": adb_path_source() == "plugin",
        "device_name": default_device_name(),
        "app_package": default_app_package(),
        "app_activity": default_app_activity(),
        "udid": default_udid(),
        "defaults": _load_mobile_defaults(),
        "device_frame_presets": list_frame_presets(),
        "backends": [
            {"id": "plugin", "label": "Recorder Plugin + Agent（推荐）", "default": True},
            {"id": "adb", "label": "ADB 直连（坐标兜底）"},
        ],
        "agent_ws_url": _mobile_agent_ws_public(),
        "mirror_backend": mirror_backend(),
        "mirror_fps": mirror_fps(),
        "scrcpy_mirror_fps": scrcpy_mirror_fps(),
        "scrcpy_max_size": scrcpy_max_size(),
        "scrcpy_available": scrcpy_available(),
        "scrcpy_path": scrcpy_path(),
        "scrcpy_bridge_port": scrcpy_bridge_port(),
        "device_scrcpy_ws": device_scrcpy_ws_enabled(),
        "scrcpy_auto_fallback": scrcpy_auto_fallback_screencap(),
        "hint": (
            "连接真机 USB、无线调试或模拟器后点击「连接设备」。"
            "推荐在插件市场安装 scrcpy 获得高帧率画布投屏；录制仍走手机端助手。"
        ),
        "auto_start_appium": False,
    }


def _mobile_agent_ws_public() -> str:
    try:
        from mobile_agent_client import mobile_agent_ws_url

        return mobile_agent_ws_url()
    except ImportError:
        return ""
