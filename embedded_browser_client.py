# -*- coding: utf-8 -*-
"""调用独立进程 embedded_browser_gateway 的轻量 HTTP 客户端（urllib，无额外依赖）。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


def embedded_gateway_config() -> Tuple[str, str, str]:
    """
    Returns:
        (gateway_base_url, shared_secret, public_ws_base)
        gateway_base_url / secret 任一为空表示功能未配置。
    """
    base = (os.environ.get("EMBEDDED_BROWSER_GATEWAY_URL") or "").strip().rstrip("/")
    secret = (os.environ.get("EMBEDDED_BROWSER_GATEWAY_SECRET") or "").strip()
    pub = (os.environ.get("EMBEDDED_BROWSER_PUBLIC_WS_BASE") or "").strip().rstrip("/")
    return base, secret, pub


def embedded_gateway_enabled() -> bool:
    base, secret, _ = embedded_gateway_config()
    return bool(base and secret)


def embedded_gateway_json(
    method: str,
    path: str,
    *,
    user_id: Optional[int] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 60.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    向网关发起 JSON 请求。

    Args:
        method: HTTP 方法。
        path: 以 / 开头的路径。
        user_id: 写入 X-Embedded-Browser-User-Id，供会话归属校验。
        body: JSON 请求体；GET 时忽略。
        timeout_sec: 超时秒数。

    Returns:
        (response_dict, error_message)。成功时 error_message 为 None。
    """
    base, secret, _ = embedded_gateway_config()
    if not base or not secret:
        return None, "embedded_browser_disabled"
    if not path.startswith("/"):
        path = "/" + path
    url = base + path
    headers = {
        "X-Embedded-Browser-Secret": secret,
        "Accept": "application/json",
    }
    data = None
    if body is not None and method.upper() != "GET":
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if user_id is not None:
        headers["X-Embedded-Browser-User-Id"] = str(int(user_id))
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw), None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(raw)
            detail = j.get("detail")
            if isinstance(detail, list) and detail:
                detail = detail[0].get("msg", raw)
            return j, str(detail or raw or e.code)
        except Exception:
            return None, raw or str(e.code)
    except Exception as e:
        return None, str(e)
