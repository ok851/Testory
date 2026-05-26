# -*- coding: utf-8 -*-
"""网页捕获会话（cdp / extension / legacy_inject）。"""

from __future__ import annotations

import secrets
import threading
from typing import Any, Dict, Optional

_lock = threading.Lock()
_session: Dict[str, Any] = {
    "active": False,
    "session_id": "",
    "mode": "cdp",
    "record_mode": False,
    "case_id": 0,
    "platform_origin": "",
    "last_pick": None,
    "picker_closed": False,
    "error": "",
    "message": "",
    "cdp_port": 0,
}


def _set(**kwargs: Any) -> None:
    with _lock:
        _session.update(kwargs)


def _snap() -> Dict[str, Any]:
    with _lock:
        return dict(_session)


def get_session_debug_snapshot() -> Dict[str, Any]:
    """供 workspace 模板读取（不含 last_pick 消费副作用）。"""
    return _snap()


def validate_session_id(session_id: str) -> bool:
    sid = (session_id or "").strip()
    if not sid or len(sid) < 8:
        return False
    snap = _snap()
    return bool(snap.get("active")) and snap.get("session_id") == sid


def _build_bookmarklet(platform_origin: str, session_id: str) -> str:
    base = (platform_origin or "").rstrip("/")
    inject_url = f"{base}/api/web-dom-picker/inject.js?session={session_id}"
    escaped = inject_url.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "javascript:(function(){"
        "var u='" + escaped + "';"
        "var s=document.createElement('script');"
        "s.src=u+(u.indexOf('?')>=0?'&':'?')+'_='+Date.now();"
        "(document.head||document.documentElement).appendChild(s);"
        "})();"
    )


def start_session(
    *,
    mode: str = "cdp",
    record_mode: bool = False,
    case_id: Optional[int] = None,
    platform_origin: str = "",
    browser: str = "edge",
    start_url: str = "",
) -> Dict[str, Any]:
    stop_session(fast=True)
    m = (mode or "cdp").strip().lower()
    if m not in ("cdp", "extension", "legacy_inject"):
        m = "cdp"
    origin = (platform_origin or "").strip().rstrip("/")
    session_id = secrets.token_hex(16)
    _set(
        active=True,
        session_id=session_id,
        mode=m,
        record_mode=bool(record_mode),
        case_id=int(case_id or 0),
        platform_origin=origin,
        last_pick=None,
        picker_closed=False,
        error="",
        message="网页捕获已就绪",
        cdp_port=0,
    )

    workspace_v2 = f"{origin}/web-capture/workspace-v2?session={session_id}" if origin else ""
    workspace_legacy = f"{origin}/web-capture/workspace?session={session_id}" if origin else ""
    inject_url = f"{origin}/api/web-dom-picker/inject.js?session={session_id}" if origin else ""
    bookmarklet = _build_bookmarklet(origin, session_id) if origin else ""

    out: Dict[str, Any] = {
        "success": True,
        "mode": m,
        "session_id": session_id,
        "workspace_url": workspace_v2,
        "workspace_url_legacy": workspace_legacy,
        "inject_url": inject_url,
        "bookmarklet": bookmarklet,
        "web_capture_mode": m,
    }

    if m == "cdp":
        from web_capture.cdp_picker import start_cdp_pick_session

        cdp_res = start_cdp_pick_session(
            session_id=session_id,
            api_base=origin,
            url=start_url or "",
            browser=browser,
        )
        out.update(cdp_res)
        if cdp_res.get("debug_port"):
            _set(cdp_port=int(cdp_res["debug_port"]), message=cdp_res.get("message") or "")
        if not cdp_res.get("success"):
            _set(active=False, error=cdp_res.get("error") or "CDP 启动失败")
            out["success"] = False
        else:
            out["hint"] = "调试浏览器已打开：悬停高亮 → 单击拾取元素"
    elif m == "extension":
        from web_capture.extension_bridge import ensure_bridge_started

        bridge = ensure_bridge_started(session_id=session_id)
        out.update(bridge)
        out["hint"] = "请确保已安装并启用 UAT 浏览器助手扩展"
    else:
        out["hint"] = "在捕获窗口加载 URL 后点「开始捕获」，或使用书签在真实页面拾取"
        out["workspace_url"] = workspace_legacy

    out["message"] = out.get("message") or "网页捕获已启动"
    return out


def report_pick(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not validate_session_id(session_id):
        return {"success": False, "error": "捕获会话无效或已结束"}
    if not isinstance(payload, dict):
        return {"success": False, "error": "无效的拾取数据"}
    snap = _snap()
    mode = snap.get("mode") or "cdp"
    try:
        if mode == "cdp":
            from web_capture.cdp_picker import process_cdp_pick_payload

            formatted = process_cdp_pick_payload(payload, capture_mode="cdp")
        else:
            from web_capture.locator_generator import format_dom_pick_payload

            formatted = format_dom_pick_payload(payload, capture_mode=mode)
    except Exception as exc:
        return {"success": False, "error": str(exc) or "格式化拾取结果失败"}
    _set(last_pick=formatted, picker_closed=False, message="已捕获网页元素")
    return {"success": True, "selected_element": formatted}


def close_session(session_id: str) -> Dict[str, Any]:
    if validate_session_id(session_id):
        _set(picker_closed=True, active=False, message="网页捕获已结束")
        return {"success": True}
    return {"success": False, "error": "会话无效"}


def stop_session(*, fast: bool = False) -> Dict[str, Any]:
    snap = _snap()
    if snap.get("mode") == "cdp":
        try:
            from web_capture import cdp_browser

            cdp_browser.disconnect(stop_browser=False)
        except Exception:
            pass
    _set(active=False, picker_closed=True)
    return {"success": True, "stopped": True, "fast": fast}


def get_session_status(*, consume_last_pick: bool = False) -> Dict[str, Any]:
    snap = _snap()
    selected = snap.get("last_pick")
    out_pick = selected
    if out_pick is not None and consume_last_pick:
        _set(last_pick=None)

    active = bool(snap.get("active")) and not bool(snap.get("picker_closed"))
    picker_closed = bool(snap.get("picker_closed")) or (
        not snap.get("active") and out_pick is None
    )

    return {
        "success": True,
        "active": active,
        "picker_closed": picker_closed,
        "selected_element": out_pick,
        "session_id": snap.get("session_id") or "",
        "mode": snap.get("mode") or "cdp",
        "web_capture_mode": snap.get("mode") or "cdp",
        "record_mode": bool(snap.get("record_mode")),
        "case_id": int(snap.get("case_id") or 0),
        "message": snap.get("message") or "",
        "error": snap.get("error") or "",
        "cdp_port": int(snap.get("cdp_port") or 0),
        "inject_url": (
            f"{snap.get('platform_origin', '')}/api/web-dom-picker/inject.js"
            f"?session={snap.get('session_id', '')}"
            if snap.get("platform_origin") and snap.get("session_id")
            else ""
        ),
        "workspace_url": (
            f"{snap.get('platform_origin', '')}/web-capture/workspace-v2"
            f"?session={snap.get('session_id', '')}"
            if snap.get("platform_origin") and snap.get("session_id")
            else ""
        ),
    }
