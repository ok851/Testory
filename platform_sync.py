# -*- coding: utf-8 -*-
"""桌面客户端 / 团队服务器 ↔ 创始人控制面：用户同步与官网支付链接。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from deployment_config import get_platform_admin_url, get_website_url
from platform_pay_token import create_pay_token


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


def platform_api_json(path: str, method: str = "GET", body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """官网服务端调用创始人控制面 JSON API。"""
    admin_url = get_platform_admin_url()
    if not admin_url:
        return {"success": False, "error": "未配置 PLATFORM_ADMIN_URL"}
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        admin_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": True}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": False, "error": str(e)}
        except Exception:
            return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}
