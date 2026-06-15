# -*- coding: utf-8 -*-
"""远程 mobile_automation_gateway HTTP 客户端。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


def mobile_agent_config() -> Tuple[str, str]:
    base = (os.environ.get("MOBILE_AGENT_GATEWAY_URL") or "").strip().rstrip("/")
    secret = (os.environ.get("MOBILE_AGENT_GATEWAY_SECRET") or "").strip()
    return base, secret


def mobile_agent_enabled() -> bool:
    base, secret = mobile_agent_config()
    return bool(base and secret)


def mobile_agent_json(
    method: str,
    path: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 120.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    base, secret = mobile_agent_config()
    if not base or not secret:
        return None, "mobile_agent_disabled"
    if not path.startswith("/"):
        path = "/" + path
    url = base + path
    headers = {
        "X-Mobile-Agent-Secret": secret,
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
            return parsed, parsed.get("error") or parsed.get("detail") or err_body or str(e)
        except Exception:
            return None, str(e)
    except Exception as e:
        return None, str(e)


def mobile_agent_ws_url() -> str:
    base, secret = mobile_agent_config()
    if not base:
        return ""
    parsed = base.replace("http://", "ws://").replace("https://", "wss://")
    sep = "&" if "?" in parsed else "?"
    return f"{parsed}/internal/events{sep}secret={secret}"


def agent_scan_devices() -> Dict[str, Any]:
    payload, err = mobile_agent_json("POST", "/internal/devices/scan", body={})
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_connect_device(**kwargs: Any) -> Dict[str, Any]:
    payload, err = mobile_agent_json("POST", "/internal/devices/connect", body=kwargs)
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_disconnect_device(udid: str = "") -> Dict[str, Any]:
    payload, err = mobile_agent_json("POST", "/internal/devices/disconnect", body={"udid": udid})
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_install_plugin(udid: str = "") -> Dict[str, Any]:
    payload, err = mobile_agent_json("POST", "/internal/plugin/install", body={"udid": udid})
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_plugin_status(udid: str = "") -> Dict[str, Any]:
    payload, err = mobile_agent_json("POST", "/internal/plugin/status", body={"udid": udid})
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_start_recording(udid: str = "", *, screenshot_per_step: bool = True) -> Dict[str, Any]:
    payload, err = mobile_agent_json(
        "POST",
        "/internal/recording/start",
        body={"udid": udid, "screenshot_per_step": screenshot_per_step},
    )
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_stop_recording(udid: str = "") -> Dict[str, Any]:
    payload, err = mobile_agent_json("POST", "/internal/recording/stop", body={"udid": udid})
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_replay_steps(udid: str, steps: list, *, from_index: int = 0) -> Dict[str, Any]:
    payload, err = mobile_agent_json(
        "POST",
        "/internal/replay/run",
        body={"udid": udid, "steps": steps, "from_index": from_index},
        timeout_sec=600.0,
    )
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_replay_step(udid: str, step: Dict[str, Any], *, step_index: int = 0) -> Dict[str, Any]:
    payload, err = mobile_agent_json(
        "POST",
        "/internal/replay/step",
        body={"udid": udid, "step": step, "step_index": step_index},
    )
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_screenshot(udid: str = "", *, use_plugin: bool = True) -> Dict[str, Any]:
    payload, err = mobile_agent_json(
        "POST",
        "/internal/inspect/screenshot",
        body={"udid": udid, "use_plugin": use_plugin},
    )
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}


def agent_page_source(udid: str = "") -> Dict[str, Any]:
    payload, err = mobile_agent_json(
        "POST",
        "/internal/inspect/page-source",
        body={"udid": udid},
    )
    if err and not payload:
        return {"success": False, "error": err}
    return payload or {"success": False, "error": err or "unknown"}
