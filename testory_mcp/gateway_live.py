# -*- coding: utf-8 -*-
"""MCP ↔ Desktop Gateway 实连接（探活 + 可选 wait 步骤）。

无网关 / 无密钥时诚实返回 ok=False，禁止空成功。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def probe_gateway_health(
    base_url: str = "",
    *,
    timeout_s: float = 2.0,
) -> Dict[str, Any]:
    """GET {base}/health；可达返回 ok=True。"""
    from ai_modules.enterprise.gateway_resolve import resolve_desktop_gateway

    resolved = resolve_desktop_gateway()
    base = (base_url or resolved.get("base_url") or "").strip().rstrip("/")
    if not base:
        return {
            "ok": False,
            "error_code": "GATEWAY_URL_MISSING",
            "error": "未配置 Gateway URL（可设 DESKTOP_AGENT_GATEWAY_URL 或 DESKTOP_FARM_GATEWAY=1）",
            "resolved": resolved,
        }
    url = base + "/health"
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=max(0.3, float(timeout_s))) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", None) or resp.getcode()
            body: Any
            try:
                body = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                body = {"raw": raw[:200]}
            ok = 200 <= int(status) < 300 and (
                body.get("ok") is True if isinstance(body, dict) and "ok" in body else True
            )
            return {
                "ok": ok,
                "probe_url": url,
                "status_code": status,
                "body": body if isinstance(body, dict) else {"raw": str(body)[:200]},
                "resolved": resolved,
                "error_code": None if ok else "GATEWAY_UNHEALTHY",
                "disclaimer": "health 可达 ≠ MCP 工具调用已成功，≠ 用例通过",
            }
    except (HTTPError, URLError, TimeoutError, OSError) as e:
        return {
            "ok": False,
            "probe_url": url,
            "error_code": "GATEWAY_UNREACHABLE",
            "error": str(e)[:200],
            "resolved": resolved,
            "disclaimer": "health 失败不得记为工具成功",
        }


def live_gateway_wait_step(*, duration_ms: int = 50) -> Dict[str, Any]:
    """经 desktop_agent_client 发一条 wait 步骤；失败诚实返回。"""
    from ai_modules.enterprise.gateway_resolve import resolve_desktop_gateway

    resolved = resolve_desktop_gateway()
    if not resolved.get("enabled"):
        return {
            "ok": False,
            "error_code": "GATEWAY_NOT_ENABLED",
            "error": "需要 DESKTOP_AGENT_GATEWAY_URL（或农场 opt-in）与 SECRET",
            "resolved": resolved,
        }
    try:
        from modules.desktop.desktop_agent_client import remote_execute_step

        result = remote_execute_step(
            {"action": "wait", "duration_ms": int(duration_ms), "desktop_spec": {}}
        )
        return {
            "ok": True,
            "result": result if isinstance(result, dict) else {"raw": str(result)},
            "resolved": resolved,
            "disclaimer": "单步 wait 成功仅证明网关可执行步骤，不代表业务用例通过",
        }
    except Exception as e:
        return {
            "ok": False,
            "error_code": "GATEWAY_STEP_FAILED",
            "error": str(e)[:300],
            "resolved": resolved,
            "disclaimer": "步骤失败不得美化为成功",
        }


def mcp_live_demo(*, try_step: bool = False) -> Dict[str, Any]:
    """给 Demo / Golden 用的聚合结果。"""
    health = probe_gateway_health()
    step: Optional[Dict[str, Any]] = None
    if try_step and health.get("ok"):
        step = live_gateway_wait_step()
    elif try_step:
        step = {
            "ok": False,
            "error_code": "SKIPPED_UNHEALTHY",
            "error": "health 未通过，跳过 wait 步骤",
        }
    return {
        "ok": True,  # Demo 脚本自身完成（非业务绿）
        "health": health,
        "step": step,
        "live_tool_success": bool(step and step.get("ok")),
        "honesty": {
            "health_ok_means_case_pass": False,
            "missing_gateway_reported_ok": False,
        },
    }
