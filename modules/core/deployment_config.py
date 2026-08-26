# -*- coding: utf-8 -*-
"""部署模式：standalone（单机一体）| server（团队数据服务器）| client（桌面客户端）。"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any, Dict


class DeploymentMode(str, Enum):
    STANDALONE = "standalone"
    SERVER = "server"
    CLIENT = "client"


def get_deployment_mode() -> DeploymentMode:
    raw = (os.environ.get("DEPLOYMENT_MODE") or "").strip().lower()
    if os.environ.get("UAT_DESKTOP_MODE", "").strip() in ("1", "true", "yes"):
        if not raw or raw == "standalone":
            return DeploymentMode.CLIENT
    if raw in ("server", "team_server", "team-server"):
        return DeploymentMode.SERVER
    if raw in ("client", "desktop"):
        return DeploymentMode.CLIENT
    return DeploymentMode.STANDALONE


def is_standalone_mode() -> bool:
    return get_deployment_mode() == DeploymentMode.STANDALONE


def is_server_mode() -> bool:
    return get_deployment_mode() == DeploymentMode.SERVER


def is_client_mode() -> bool:
    return get_deployment_mode() == DeploymentMode.CLIENT


def is_local_standalone_desktop() -> bool:
    """桌面客户端选择「本机独立使用」，不连接团队服务器。"""
    if not is_client_mode():
        return is_standalone_mode()
    try:
        from modules.core.client_config_store import load_client_config

        return bool(load_client_config().get("local_standalone"))
    except Exception:
        return False


def uses_team_server() -> bool:
    return is_client_mode() and not is_local_standalone_desktop()


def can_run_automation_locally() -> bool:
    """是否在本进程内启动 Playwright / 桌面自动化。"""
    mode = get_deployment_mode()
    return mode in (DeploymentMode.STANDALONE, DeploymentMode.CLIENT)


def should_delegate_execution_to_clients() -> bool:
    return is_server_mode()


def hide_billing_ui() -> bool:
    """桌面客户端不展示定价/支付页。"""
    return is_client_mode()


def get_team_server_url() -> str:
    return (os.environ.get("TEAM_SERVER_URL") or "").strip().rstrip("/")


def get_platform_admin_url() -> str:
    return (os.environ.get("PLATFORM_ADMIN_URL") or "").strip().rstrip("/")


def get_website_url() -> str:
    """官网根地址。未配置或仍为本地联调地址时，桌面客户端回落到正式官网。"""
    from packages.testory_common.brand import OFFICIAL_WEBSITE_URL

    raw = (os.environ.get("WEBSITE_URL") or "").strip().rstrip("/")
    if not raw:
        return OFFICIAL_WEBSITE_URL
    # 安装包若残留 127.0.0.1:5200，用户点「升级订阅」会跳到无效本机地址
    low = raw.lower()
    if is_client_mode() and (
        "127.0.0.1" in low or "localhost" in low or low.startswith("http://0.0.0.0")
    ):
        return OFFICIAL_WEBSITE_URL
    return raw


from modules.core.brand_config import brand_context  # noqa: E402


def deployment_context() -> Dict[str, Any]:
    mode = get_deployment_mode()
    return {
        "deployment_mode": mode.value,
        "is_standalone": mode == DeploymentMode.STANDALONE,
        "is_server": mode == DeploymentMode.SERVER,
        "is_client": mode == DeploymentMode.CLIENT,
        "is_tauri": os.environ.get("TESTORY_TAURI_MODE", "").strip() == "1",
        "is_local_standalone": is_local_standalone_desktop(),
        "uses_team_server": uses_team_server(),
        "hide_billing_ui": hide_billing_ui(),
        "can_run_automation_locally": can_run_automation_locally(),
        "team_server_url": get_team_server_url(),
        "platform_admin_url": get_platform_admin_url(),
        "website_url": get_website_url(),
        **brand_context(),
    }
