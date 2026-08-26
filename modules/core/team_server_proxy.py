# -*- coding: utf-8 -*-
"""client 模式：将数据类 API 代理到团队服务器。"""
from __future__ import annotations

import json
from typing import Any, Tuple

from modules.core.deployment_config import is_client_mode, uses_team_server
from modules.core.client_config_store import get_team_server_url, is_setup_complete
from modules.core.team_server_client import TeamServerError, request_json


def should_proxy_path(path: str) -> bool:
    if not is_client_mode() or not uses_team_server():
        return False
    if not is_setup_complete() and not get_team_server_url():
        return False
    from modules.execution.execution_remote import is_local_client_api

    if not path.startswith("/api/"):
        return False
    # 本机登录 / 简易注册 / 密钥找回 / 客户端配置 一律不代理
    if (
        path.startswith("/api/auth/login")
        or path.startswith("/api/auth/register")
        or path.startswith("/api/auth/forgot-password")
        or path.startswith("/api/client/")
    ):
        return False
    return not is_local_client_api(path)


def proxy_to_team_server(method: str, path: str, query_string: str = "", body: Any = None) -> Tuple[Any, int]:
    full_path = path
    if query_string:
        full_path = f"{path}?{query_string}"
    json_body = None
    if body is not None and method.upper() in ("POST", "PUT", "PATCH"):
        if isinstance(body, bytes):
            try:
                json_body = json.loads(body.decode("utf-8"))
            except Exception:
                json_body = {}
        elif isinstance(body, dict):
            json_body = body
    try:
        data, status = request_json(method, full_path, body=json_body)
        return data, status
    except TeamServerError as e:
        return {"success": False, "error": str(e)}, 502
