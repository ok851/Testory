# -*- coding: utf-8 -*-
"""
统一元素捕获入口：协调 Windows 桌面（desktop_picker）与网页 DOM（web_dom_picker）。

桌面与网页使用独立捕获器实现，互不影响。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "active": False,
    "record_mode": False,
    "capture_channel": "desktop",
    "web_enabled": False,
    "web_url": "",
    "web_error": "",
    "case_id": 0,
}


def _set_state(**kwargs: Any) -> None:
    with _lock:
        _state.update(kwargs)


def element_picker_available() -> bool:
    try:
        from desktop_picker import desktop_picker_available as desk_ok

        return desk_ok()
    except ImportError:
        return False


def _start_web_channel_picker(
    *,
    record_mode: bool = False,
    case_id: Optional[int] = None,
    platform_origin: str = "",
    web_capture_mode: str = "cdp",
    browser: str = "edge",
    start_url: str = "",
) -> Dict[str, Any]:
    """网页捕获：CDP / 扩展 / legacy_inject（与桌面捕获隔离）。"""
    from web_dom_picker import sync_start_web_dom_picker

    return sync_start_web_dom_picker(
        record_mode=record_mode,
        case_id=case_id,
        platform_origin=platform_origin,
        web_capture_mode=web_capture_mode,
        browser=browser,
        start_url=start_url,
    )


def _desktop_picker_unavailable_message() -> str:
    try:
        from desktop_runtime import desktop_runtime_unavailable_reason

        reason = desktop_runtime_unavailable_reason()
        if reason:
            return reason
    except ImportError:
        pass
    return "桌面捕获环境未就绪（请检查 opencv-python / mss / 权限）"


def start_element_picker(
    *,
    desktop_spec: Optional[Dict[str, Any]] = None,
    record_mode: bool = False,
    capture_channel: str = "desktop",
    web_url: str = "",
    web_fallback_url: str = "",
    enable_web: bool = False,
    web_navigate: bool = False,
    web_attach_existing: bool = True,
    case_id: Optional[int] = None,
    platform_origin: str = "",
    web_capture_mode: str = "cdp",
    browser: str = "edge",
    start_url: str = "",
) -> Dict[str, Any]:
    """启动元素捕获。

    capture_channel:
    - desktop: 仅 Windows 桌面拾取（UIA/视觉），不注入网页 DOM 拾取。
    - web: 网页 DOM 捕获（独立会话 + 目标页注入脚本），不启动桌面悬浮窗。
    """
    stop_element_picker(fast=True)

    channel = (capture_channel or "desktop").strip().lower()
    if channel not in ("desktop", "web"):
        channel = "desktop"

    desk_result: Dict[str, Any] = {"success": False, "skipped": True}
    web_result: Dict[str, Any] = {"success": False, "skipped": True}
    nav = (web_url or "").strip() if web_navigate else ""

    if channel == "desktop":
        try:
            from desktop_picker import desktop_picker_available, sync_start_desktop_picker

            if desktop_picker_available():
                desk_result = sync_start_desktop_picker(
                    dict(desktop_spec or {}),
                    record_mode=bool(record_mode),
                    unified_mode=True,
                    prefer_web_clicks=False,
                    case_id=case_id,
                    skip_initial_stop=True,
                )
            else:
                desk_result = {
                    "success": False,
                    "error": _desktop_picker_unavailable_message(),
                    "skipped": True,
                }
        except Exception as exc:
            desk_result = {"success": False, "error": str(exc)}
    else:
        desk_result = {
            "success": True,
            "skipped": True,
            "message": "网页捕获模式未启动桌面拾取器",
        }

    web_enabled = False
    web_error = ""
    use_web = channel == "web" or bool(enable_web)
    if channel == "web":
        web_navigate = False
        web_attach_existing = True
        web_result = _start_web_channel_picker(
            record_mode=bool(record_mode),
            case_id=case_id,
            platform_origin=platform_origin,
            web_capture_mode=web_capture_mode,
            browser=browser,
            start_url=start_url,
        )
        web_enabled = bool(web_result.get("success"))
        web_error = str(web_result.get("error") or "").strip()
    elif use_web and web_navigate and nav:
        web_result = {
            "success": False,
            "skipped": True,
            "error": "网页捕获不再自动打开浏览器；请使用工具栏「网页捕获」附着当前页",
        }
    elif use_web and web_attach_existing:
        web_result = {
            "success": False,
            "skipped": True,
            "error": "请使用工具栏「网页捕获」在已有浏览器页拾取元素",
        }
    elif use_web:
        web_result = {
            "success": False,
            "skipped": True,
            "error": "未请求打开浏览器（请使用工具栏「网页捕获」）",
        }
    else:
        web_result = {
            "success": False,
            "skipped": True,
            "error": "未请求 Web 捕获",
        }

    if channel == "web" and not web_result.get("success"):
        ok = False
    else:
        ok = bool(desk_result.get("success")) or bool(web_result.get("success"))
    err_parts: list = []
    if channel == "desktop" and not desk_result.get("success"):
        e = str(desk_result.get("error") or "").strip()
        if e:
            err_parts.append(e)
    if use_web and not web_result.get("success"):
        e = str(web_result.get("error") or web_error or "").strip()
        if e:
            err_parts.append(e)
    _set_state(
        active=ok,
        record_mode=bool(record_mode),
        capture_channel=channel,
        web_enabled=web_enabled,
        web_url=(web_url or web_fallback_url or "").strip(),
        web_error=web_error,
        case_id=int(case_id or 0),
    )
    msg = "元素捕获已启动" if ok else ""
    if ok and channel == "web":
        msg = (
            web_result.get("hint")
            or "网页捕获已启动：在待测页运行捕获书签，面板内开始捕获后单击元素"
        )
    elif ok and channel == "desktop":
        msg = "桌面捕获已启动：在目标窗口使用悬浮条智能点选（勿在浏览器内用桌面模式拾取网页）"
    return {
        "success": ok,
        "record_mode": bool(record_mode),
        "capture_channel": channel,
        "unified": True,
        "desktop": desk_result,
        "web": web_result,
        "message": msg,
        "error": "；".join(err_parts) if not ok and err_parts else "",
    }


def stop_element_picker(*, fast: bool = False) -> Dict[str, Any]:
    """停止统一捕获。"""
    desk_out: Dict[str, Any] = {}
    web_out: Dict[str, Any] = {"success": True}
    try:
        from desktop_picker import sync_stop_desktop_picker

        desk_out = sync_stop_desktop_picker(fast=fast)
    except Exception as exc:
        desk_out = {"success": False, "error": str(exc)}
    with _lock:
        was_web = bool(_state.get("web_enabled"))
    if was_web:
        try:
            from web_dom_picker import sync_stop_web_dom_picker

            web_out = sync_stop_web_dom_picker(fast=fast)
        except Exception as exc:
            web_out = {"success": False, "error": str(exc)}

    _set_state(active=False, web_enabled=False)
    return {
        "success": True,
        "stopped": True,
        "desktop": desk_out,
        "web": web_out,
        "recorded_steps": list(desk_out.get("recorded_steps") or []),
    }


def get_element_picker_status(*, consume_last_pick: bool = False) -> Dict[str, Any]:
    """聚合桌面与 Web 拾取状态。"""
    desk: Dict[str, Any] = {"success": False, "active": False}
    web: Dict[str, Any] = {"success": True, "selected_element": None}

    try:
        from desktop_picker import sync_get_desktop_picker_status

        desk = sync_get_desktop_picker_status(consume_last_pick=consume_last_pick)
    except Exception as exc:
        desk = {"success": False, "error": str(exc)}

    with _lock:
        web_on = bool(_state.get("web_enabled"))

    if web_on:
        try:
            from web_dom_picker import sync_get_web_dom_picker_status

            wst = sync_get_web_dom_picker_status(
                consume_last_pick=consume_last_pick
            )
            sel = wst.get("selected_element")
            if wst.get("picker_closed"):
                web = {
                    "success": True,
                    "selected_element": sel,
                    "picker_closed": True,
                }
            elif sel:
                web = {"success": True, "selected_element": sel}
            else:
                web = {
                    "success": bool(wst.get("success", True)),
                    "selected_element": None,
                    "active": bool(wst.get("active")),
                    "error": wst.get("error") or "",
                }
        except Exception as exc:
            web = {"success": False, "error": str(exc), "selected_element": None}

    desk_closed = bool(desk.get("picker_closed"))
    web_closed = bool(web.get("picker_closed"))

    with _lock:
        channel = (_state.get("capture_channel") or "desktop").strip().lower()
        state_active = bool(_state.get("active"))

    if channel == "web":
        picker_closed = web_closed if web_on else not state_active
        active = state_active and web_on and not picker_closed
    elif channel == "desktop":
        picker_closed = desk_closed
        desk_active = bool(desk.get("active"))
        active = (state_active or desk_active) and not picker_closed
    else:
        picker_closed = desk_closed and (not web_on or web_closed)
        desk_active = bool(desk.get("active"))
        active = (state_active or desk_active) and not picker_closed

    new_steps = list(desk.get("new_recorded_steps") or [])
    if picker_closed and not new_steps:
        rec_all = list(desk.get("recorded_steps") or [])
        sent = int(desk.get("_sent_count") or 0)
        if len(rec_all) > sent:
            new_steps = rec_all[sent:]

    return {
        "success": True,
        "active": active,
        "unified": True,
        "capture_channel": channel,
        "record_mode": bool(_state.get("record_mode")),
        "case_id": int(desk.get("case_id") or _state.get("case_id") or 0),
        "web_enabled": web_on,
        "web_error": _state.get("web_error") or "",
        "desktop": desk,
        "web": web,
        "picker_closed": picker_closed,
        "last_pick": desk.get("last_pick"),
        "new_recorded_steps": new_steps,
        "recorded_steps": list(desk.get("recorded_steps") or []) if desk_closed else [],
        "selected_element": web.get("selected_element"),
        "error": desk.get("error") or "",
        "message": desk.get("message") or "",
    }


def sync_start_element_picker(**kwargs: Any) -> Dict[str, Any]:
    return start_element_picker(**kwargs)


def sync_stop_element_picker() -> Dict[str, Any]:
    return stop_element_picker()


def sync_get_element_picker_status(**kwargs: Any) -> Dict[str, Any]:
    return get_element_picker_status(**kwargs)
