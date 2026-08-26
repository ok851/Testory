# -*- coding: utf-8 -*-
"""远程 desktop_automation_gateway HTTP 客户端。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


def desktop_agent_config() -> Tuple[str, str]:
    """(base_url, secret)；URL 可经 ``DESKTOP_FARM_GATEWAY=1`` 从农场在线节点回退。"""
    try:
        from ai_modules.enterprise.gateway_resolve import desktop_agent_config as _resolve

        return _resolve()
    except Exception:
        base = (os.environ.get("DESKTOP_AGENT_GATEWAY_URL") or "").strip().rstrip("/")
        secret = (os.environ.get("DESKTOP_AGENT_GATEWAY_SECRET") or "").strip()
        return base, secret


def desktop_agent_enabled() -> bool:
    base, secret = desktop_agent_config()
    return bool(base and secret)


def desktop_agent_json(
    method: str,
    path: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 120.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    base, secret = desktop_agent_config()
    if not base or not secret:
        return None, "desktop_agent_disabled"
    if not path.startswith("/"):
        path = "/" + path
    url = base + path
    headers = {
        "X-Desktop-Agent-Secret": secret,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}, None
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(err_body) if err_body.strip() else {}
            return parsed, parsed.get("error") or err_body or str(e)
        except Exception:
            return None, str(e)
    except Exception as e:
        return None, str(e)


def remote_execute_step(step: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
    sid = session_id or os.environ.get("DESKTOP_AGENT_SESSION_ID") or "default"
    payload, err = desktop_agent_json(
        "POST",
        f"/internal/session/{sid}/run-steps",
        body={"steps": [step]},
    )
    if err and not payload:
        raise RuntimeError(err)
    if not payload or not payload.get("success"):
        raise RuntimeError((payload or {}).get("error") or err or "远程桌面 Agent 执行失败")
    results = payload.get("results") or []
    if results and isinstance(results[0], dict):
        r0 = results[0]
        if r0.get("status") == "error":
            raise RuntimeError(r0.get("error") or "远程步骤失败")
        return r0
    return {"status": "success", "action": step.get("action")}
