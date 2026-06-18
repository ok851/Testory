# -*- coding: utf-8 -*-
"""桌面客户端 / 团队服务器 ↔ 创始人控制面：用户同步与官网支付链接。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from deployment_config import get_platform_admin_url, get_website_url
from packages.testory_common.pay_token import create_pay_token
from packages.testory_common.platform_client import platform_api_json as _platform_api_json


def _sync_secret() -> str:
    return (
        os.environ.get("PLATFORM_SYNC_SECRET")
        or os.environ.get("PLATFORM_ADMIN_SECRET")
        or ""
    ).strip()


def sync_product_user(
    user_id: int,
    username: str,
    email: str = "",
    team_server_url: str = "",
    license_type: str = "free",
) -> bool:
    """登录成功后同步用户到创始人控制面用户库。"""
    admin_url = get_platform_admin_url()
    secret = _sync_secret()
    if not admin_url or not secret:
        return False
    payload = json.dumps(
        {
            "user_id": int(user_id),
            "username": (username or "").strip(),
            "email": (email or "").strip(),
            "team_server_url": (team_server_url or "").strip(),
            "license_type": (license_type or "free").strip(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        admin_url.rstrip("/") + "/api/platform/users/sync",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Platform-Sync-Secret": secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def build_website_payment_url(
    user_id: int,
    username: str,
    email: str = "",
    team_server_url: str = "",
    plan: str = "",
) -> Optional[str]:
    """生成软件内跳转官网支付的 URL（须已登录）。"""
    website = get_website_url()
    if not website or not user_id or not username:
        return None
    token = create_pay_token(
        int(user_id),
        username,
        email=(email or "").strip(),
        team_server_url=(team_server_url or "").strip(),
    )
    url = f"{website.rstrip('/')}/pricing?pay_token={token}"
    if plan:
        url += f"&plan={plan.strip()}"
    return url


def report_license_activation(
    license_id: str,
    binding_type: str = "",
    binding_id: str = "",
) -> bool:
    """客户端/服务器激活 License 后上报创始人控制面（写入 license_activations）。"""
    admin_url = get_platform_admin_url()
    lid = (license_id or "").strip()
    bid = (binding_id or "").strip()
    if not admin_url or not lid or not bid:
        return False
    payload = json.dumps(
        {
            "license_id": lid,
            "binding_type": (binding_type or "").strip(),
            "binding_id": bid,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        admin_url.rstrip("/") + "/api/licenses/activate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def report_current_license_activation() -> bool:
    """将本机当前已激活 License 的绑定信息同步到创始人控制面（用于补报历史激活）。"""
    try:
        from deployment_config import is_client_mode, is_server_mode
        from instance_identity import get_instance_id, get_machine_id
        from license_manager import license_manager

        info = license_manager.get_current_license()
        if not info or not (info.license_id or "").strip():
            return False
        if (info.license_type or "").strip().lower() in ("", "free", "trial"):
            return False

        binding_type = (info.binding_type or "").strip()
        binding_id = (info.binding_id or "").strip()
        if not binding_id:
            if is_server_mode():
                binding_type = binding_type or "instance"
                binding_id = get_instance_id()
            elif is_client_mode():
                binding_type = binding_type or "machine"
                binding_id = get_machine_id()
            else:
                binding_type = binding_type or "machine"
                binding_id = get_machine_id()
        if not binding_id:
            return False
        return report_license_activation(info.license_id, binding_type, binding_id)
    except Exception:
        return False


def platform_api_json(path: str, method: str = "GET", body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """官网服务端调用创始人控制面 JSON API。"""
    return _platform_api_json(path, method=method, body=body)
