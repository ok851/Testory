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


# =====================================================================
# scrcpy 投屏 + 反控
# =====================================================================

def scrcpy_path() -> str:
    """
    解析 scrcpy 可执行文件路径。优先级：
    1) 插件市场已安装的 scrcpy
    2) 环境变量 SCRCPY_PATH
    3) client_config.mobile_defaults.scrcpy_path
    4) 回退字符串 scrcpy（依赖系统 PATH）
    """
    try:
        from mobile_scrcpy_bundles import get_installed_scrcpy_exe

        bundled = get_installed_scrcpy_exe()
        if bundled:
            return bundled
    except Exception:
        pass
    env_path = (os.environ.get("SCRCPY_PATH") or "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    cfg = _load_mobile_defaults()
    cfg_path = (cfg.get("scrcpy_path") or "").strip()
    if cfg_path and Path(cfg_path).is_file():
        return cfg_path
    return env_path or "scrcpy"


def scrcpy_available() -> bool:
    """scrcpy 是否已通过插件市场安装。"""
    try:
        from mobile_scrcpy_bundles import get_installed_scrcpy_exe

        return bool(get_installed_scrcpy_exe())
    except Exception:
        return False


def scrcpy_mirror_fps() -> int:
    """scrcpy 投屏帧率（默认 30）。"""
    try:
        return max(1, min(120, int(os.environ.get("SCRCPY_MIRROR_FPS", "30"))))
    except ValueError:
        return 30


def scrcpy_max_size() -> int:
    """scrcpy 投屏最大分辨率（0=不限制）。"""
    try:
        return max(0, min(3840, int(os.environ.get("SCRCPY_MAX_SIZE", "0"))))
    except ValueError:
        return 0


def scrcpy_bridge_port() -> int:
    """scrcpy WebSocket 桥接端口（默认 8767，避免与桌面网关 8766 冲突）。"""
    try:
        return max(1024, min(65535, int(os.environ.get("MOBILE_SCRCPY_BRIDGE_PORT") or os.environ.get("SCRCPY_BRIDGE_PORT") or "8767")))
    except ValueError:
        return 8767


def scrcpy_bridge_url(client_host: str = "127.0.0.1") -> str:
    """scrcpy WebSocket 桥接 URL。"""
    return f"ws://{client_host}:{scrcpy_bridge_port()}/scrcpy"


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


def mirror_backend() -> str:
    """投屏后端：已放弃内嵌画布方案，统一由独立 scrcpy 窗口承担。保留字段以兼容旧调用方。"""
    return resolve_mirror_backend()


def resolve_mirror_backend(udid: str = "") -> str:
    """根据 scrcpy 可用性决定投屏后端（仅用于兼容性标记，投屏实际由独立 scrcpy 窗口承担）。

    已放弃内嵌画布方案，亦不再提供 screencap 降级方案。
    """
    del udid
    return "scrcpy_ws" if scrcpy_available() else "none"


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
        "scrcpy_available": scrcpy_available(),
        "scrcpy_path": scrcpy_path() if scrcpy_available() else "",
        "scrcpy_bridge_port": scrcpy_bridge_port(),
        "external_scrcpy_supported": True,
        "hint": (
            "连接真机 USB、无线调试或模拟器后点击「连接设备」。"
            "录制请使用手机端助手 APK。"
            "安装 scrcpy 插件后将在本地电脑启动手机画面进行投屏与反控（已放弃内嵌画布方案）。"
        ),
        "auto_start_appium": False,
    }


def _mobile_agent_ws_public() -> str:
    try:
        from mobile_agent_client import mobile_agent_ws_url

        return mobile_agent_ws_url()
    except ImportError:
        return ""


# =====================================================================
# Maestro 专用配置
# =====================================================================

def maestro_home() -> str:
    """Maestro CLI 安装根目录 (默认项目 .maestro/)"""
    cfg = _load_mobile_defaults()
    return (os.environ.get("MAESTRO_HOME") or cfg.get("maestro_home") or "").strip()


def maestro_version() -> str:
    """指定 Maestro 版本 (默认最新稳定版)"""
    return (
        os.environ.get("MAESTRO_VERSION") or _load_mobile_defaults().get("maestro_version") or ""
    ).strip()


def maestro_timeout_seconds() -> int:
    """Maestro 单次执行超时 (秒, 默认 600)"""
    raw = os.environ.get("MAESTRO_TIMEOUT") or ""
    try:
        return max(30, min(3600, int(raw))) if raw else 600
    except ValueError:
        return 600


def maestro_auto_install() -> bool:
    """是否自动下载 Maestro (默认 1)"""
    return _truthy("MAESTRO_AUTO_INSTALL", "1")


def maestro_auto_inject_dialogs() -> bool:
    """是否自动注入系统弹窗处理逻辑 (默认 1)"""
    return _truthy("MAESTRO_AUTO_INJECT_DIALOGS", "1")


def maestro_driver_startup_timeout_ms() -> int:
    """Maestro 驱动启动超时 (毫秒)"""
    raw = os.environ.get("MAESTRO_DRIVER_STARTUP_TIMEOUT") or "60000"
    try:
        return max(10000, min(300000, int(raw)))
    except ValueError:
        return 60000


def maestro_env_extra() -> Dict[str, str]:
    """Maestro 执行时的额外环境变量 (JSON 格式)"""
    raw = (os.environ.get("MAESTRO_ENV_EXTRA") or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def visual_fallback_enabled() -> bool:
    """是否启用视觉兜底适配器 (默认 1)"""
    return _truthy("MOBILE_VISUAL_FALLBACK", "1")


def visual_min_confidence() -> float:
    """视觉匹配置信度阈值 (默认 0.75)"""
    raw = os.environ.get("MOBILE_VISUAL_MIN_CONFIDENCE") or "0.75"
    try:
        return max(0.3, min(1.0, float(raw)))
    except ValueError:
        return 0.75


def self_healing_enabled() -> bool:
    """是否启用自愈定位 (默认 1)"""
    return _truthy("MOBILE_SELF_HEALING", "1")


def layered_locator_strategy() -> str:
    """
    分层定位策略模式:
    - strict: 仅用最高优先级定位符
    - cascade: 按优先级级联回退 (默认)
    - ai_first: AI 视觉优先
    """
    raw = (os.environ.get("MOBILE_LOCATOR_STRATEGY") or "cascade").strip().lower()
    if raw in ("strict", "cascade", "ai_first"):
        return raw
    return "cascade"


def device_pool_max_workers() -> int:
    """多设备并行最大工作进程数 (默认 4)"""
    raw = os.environ.get("MOBILE_DEVICE_POOL_WORKERS") or "4"
    try:
        return max(1, min(16, int(raw)))
    except ValueError:
        return 4


def maestro_jvm_args() -> str:
    """Maestro JVM 启动参数 (如 -Xmx2g)"""
    return (os.environ.get("MAESTRO_JVM_ARGS") or "-Xmx1g").strip()


def cloud_device_endpoint() -> str:
    """云真机 STF 代理端点"""
    return (os.environ.get("MOBILE_CLOUD_ENDPOINT") or "").strip()


def install_java_required() -> bool:
    """安装脚本是否自动检测/安装 Java (Windows 一键脚本)"""
    return _truthy("ENABLE_MOBILE", "0")


def ios_enabled() -> bool:
    """是否启用 iOS 设备支持"""
    return _truthy("ENABLE_IOS", "0")


def idb_path() -> str:
    """idb (iOS Device Bridge) 路径"""
    return os.environ.get("IDB_PATH") or "idb"


def maestro_debug_mode() -> bool:
    """是否输出 Maestro 调试日志"""
    return _truthy("MAESTRO_DEBUG", "0")


def maestro_report_retention_days() -> int:
    """Maestro 报告保留天数 (默认 7)"""
    raw = os.environ.get("MAESTRO_REPORT_RETENTION_DAYS") or "7"
    try:
        return max(1, min(90, int(raw)))
    except ValueError:
        return 7



