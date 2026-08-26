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


def _squash_gateway_http_error_body(raw: str) -> str:
    """非 JSON 的网关错误体（常为 HTML 500）转成可读说明，避免 UI 只显示 Internal Server Error。"""
    s = (raw or "").strip()
    if not s:
        return "网关返回错误（无正文）"
    low = s.lower()
    if s.startswith("<") or "<!doctype" in low[:120] or "<html" in low[:120]:
        return (
            "嵌入式网关返回了 HTML 错误页（多为网关内异常）。"
            "请查看 UAT_DATA_DIR/logs/embedded_gateway.log。"
        )
    if len(s) > 480:
        return s[:480] + "…"
    return s


def _embedded_gateway_friendly_error(exc_str: str, base_url: str) -> str:
    """将 urllib 底层异常转为用户可操作的说明（不改变 HTTP 成功路径）。"""
    s = (exc_str or "").strip()
    low = s.lower()
    # Windows: 10061；Linux 常见 111 Connection refused
    if (
        "10061" in s
        or "111" in s
        or "actively refused" in low
        or "connection refused" in low
        or "拒绝" in s
    ):
        return (
            f"无法连接内置浏览器网关（{base_url}）：目标计算机积极拒绝连接。"
            "平台会在启动时自动拉起 Browser Runtime；请重启 Testory 或刷新 AI 测试页重试。"
            "若仍失败，请查看 UAT_DATA_DIR/logs/embedded_gateway.log，"
            "并确认 .env 中 EMBEDDED_BROWSER_GATEWAY_URL 与端口一致（默认 http://127.0.0.1:8765）。"
        )
    if "timed out" in low or "timeout" in low:
        return (
            f"连接网关超时（{base_url}）。请确认网关进程已启动且未被防火墙拦截。"
        )
    return s


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
            return None, _squash_gateway_http_error_body(raw or str(e.code))
    except Exception as e:
        raw = str(e)
        return None, _embedded_gateway_friendly_error(raw, base)
