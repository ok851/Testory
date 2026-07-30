# -*- coding: utf-8 -*-
"""统一 Agent 会话：一脑多端双手 — 跨入口共享 cross_end_vars。"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_LOCK = threading.RLock()
# key: (user_id, session_id) -> session dict
_SESSIONS: Dict[Tuple[int, str], Dict[str, Any]] = {}

DEFAULT_SESSION_ID = "default"


def normalize_session_id(session_id: Optional[str] = None) -> str:
    sid = (session_id or "").strip()
    return sid or DEFAULT_SESSION_ID


def session_key(user_id: int, session_id: Optional[str] = None) -> Tuple[int, str]:
    return (int(user_id or 0), normalize_session_id(session_id))


def get_or_create_session(
    user_id: int,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    key = session_key(user_id, session_id)
    with _LOCK:
        sess = _SESSIONS.get(key)
        if sess is None:
            sess = {
                "user_id": key[0],
                "session_id": key[1],
                "cross_end_vars": {},
                "tools_used": [],
                "last_reply": "",
                "connected_hands": {},
                "updated_at": time.time(),
                "created_at": time.time(),
            }
            _SESSIONS[key] = sess
        return dict(sess)


def merge_cross_end_vars(
    user_id: int,
    vars_map: Optional[Dict[str, Any]],
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(vars_map, dict) or not vars_map:
        return get_or_create_session(user_id, session_id).get("cross_end_vars") or {}
    key = session_key(user_id, session_id)
    with _LOCK:
        sess = _SESSIONS.get(key)
        if sess is None:
            get_or_create_session(user_id, session_id)
            sess = _SESSIONS[key]
        cur = sess.setdefault("cross_end_vars", {})
        for k, v in vars_map.items():
            if k is None:
                continue
            ks = str(k).strip()
            if not ks or v is None:
                continue
            if isinstance(v, str) and not v.strip():
                continue
            cur[ks] = v
        sess["updated_at"] = time.time()
        return dict(cur)


def set_session_meta(
    user_id: int,
    *,
    session_id: Optional[str] = None,
    tools_used: Optional[List[str]] = None,
    last_reply: Optional[str] = None,
    connected_hands: Optional[Dict[str, Any]] = None,
) -> None:
    key = session_key(user_id, session_id)
    with _LOCK:
        get_or_create_session(user_id, session_id)
        sess = _SESSIONS[key]
        if tools_used is not None:
            sess["tools_used"] = list(tools_used)[-40:]
        if last_reply is not None:
            sess["last_reply"] = str(last_reply)[:4000]
        if connected_hands is not None:
            sess["connected_hands"] = dict(connected_hands)
        sess["updated_at"] = time.time()


def snapshot_connected_hands(user_id: int = 0) -> Dict[str, Any]:
    """按连接态描述可用双手（非入口平台）。"""
    phone_ok = False
    phone_devices: List[Dict[str, Any]] = []
    try:
        from mobile_sync_store import list_paired_devices_for_user

        phone_devices = list_paired_devices_for_user(int(user_id or 0))
        phone_ok = len(phone_devices) > 0
    except Exception:
        phone_ok = False

    desktop_ok = False
    desktop_detail = ""
    try:
        from ai_modules.execute.desktop_preflight import check_desktop_preflight

        pre = check_desktop_preflight(timeout_sec=0.8)
        desktop_ok = bool(pre.get("ok"))
        desktop_detail = str(pre.get("detail") or pre.get("mode") or "")
    except Exception as e:
        desktop_detail = str(e)[:120]

    browser_ok = False
    try:
        from agent_gateway_client import agent_gateway_configured
        from hermes_config import hermes_cdp_attached

        browser_ok = bool(agent_gateway_configured()) and bool(hermes_cdp_attached())
    except Exception:
        try:
            from agent_gateway_client import agent_gateway_configured

            browser_ok = bool(agent_gateway_configured())
        except Exception:
            browser_ok = False

    return {
        "phone": phone_ok,
        "desktop": desktop_ok,
        "browser": browser_ok,
        "phone_devices": phone_devices[:8],
        "desktop_detail": desktop_detail,
        "ts": time.time(),
    }


def reset_sessions_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()
