# -*- coding: utf-8 -*-
"""
统一元素捕获：同时支持 Windows 桌面（UIA）与 Web 浏览器（Playwright 可视化拾取）。

对外提供 start / stop / status，内部协调 desktop_picker 与 playwright 拾取会话。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "active": False,
    "record_mode": False,
    "web_enabled": False,
    "web_url": "",
    "web_error": "",
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


def start_element_picker(
    *,
    desktop_spec: Optional[Dict[str, Any]] = None,
    record_mode: bool = False,
    web_url: str = "",
    enable_web: bool = True,
) -> Dict[str, Any]:
    """启动统一捕获（桌面悬浮窗 + 可选 Web 浏览器拾取）。"""
    stop_element_picker()

    desk_result: Dict[str, Any] = {"success": False, "skipped": True}
    web_result: Dict[str, Any] = {"success": False, "skipped": True}
    nav = (web_url or "").strip()

    try:
        from desktop_picker import desktop_picker_available, sync_start_desktop_picker

        if desktop_picker_available():
            desk_result = sync_start_desktop_picker(
                dict(desktop_spec or {}),
                record_mode=bool(record_mode),
                unified_mode=True,
                prefer_web_clicks=bool(enable_web and nav),
            )
        else:
            desk_result = {
                "success": False,
                "error": "桌面捕获仅支持 Windows（需 pywinauto）",
                "skipped": True,
            }
    except Exception as exc:
        desk_result = {"success": False, "error": str(exc)}

    web_enabled = False
    web_error = ""
    if enable_web and nav:
        try:
            from playwright_automation import sync_enable_element_selection

            sync_enable_element_selection(nav, auto_arm=True)
            web_result = {
                "success": True,
                "url": nav,
                "auto_arm": True,
                "hint": "已在浏览器中开启拾取，按住 Ctrl 并点击页面元素即可",
            }
            web_enabled = True
        except Exception as exc:
            msg = str(exc)
            web_error = msg
            web_result = {"success": False, "error": msg, "skipped": False}
    elif enable_web:
        web_result = {
            "success": False,
            "skipped": True,
            "error": "无 Web 导航 URL，已跳过网页拾取（不会打开空白浏览器）",
        }
    else:
        web_result = {
            "success": False,
            "skipped": True,
            "error": "未请求 Web 捕获",
        }

    ok = bool(desk_result.get("success")) or bool(web_result.get("success"))
    _set_state(
        active=ok,
        record_mode=bool(record_mode),
        web_enabled=web_enabled,
        web_url=(web_url or "").strip(),
        web_error=web_error,
    )
    return {
        "success": ok,
        "record_mode": bool(record_mode),
        "unified": True,
        "desktop": desk_result,
        "web": web_result,
        "message": "元素捕获已启动",
    }


def stop_element_picker() -> Dict[str, Any]:
    """停止统一捕获。"""
    desk_out: Dict[str, Any] = {}
    web_out: Dict[str, Any] = {"success": True}
    try:
        from desktop_picker import sync_stop_desktop_picker

        desk_out = sync_stop_desktop_picker()
    except Exception as exc:
        desk_out = {"success": False, "error": str(exc)}
    with _lock:
        was_web = bool(_state.get("web_enabled"))
    if was_web:
        try:
            from playwright_automation import sync_disable_element_selection

            sync_disable_element_selection()
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
            from playwright_automation import sync_get_selected_element

            sel = sync_get_selected_element()
            if isinstance(sel, dict) and sel.get("_picker_closed"):
                web = {"success": True, "selected_element": None, "picker_closed": True}
            elif sel:
                web = {"success": True, "selected_element": sel}
            else:
                web = {"success": True, "selected_element": None}
        except Exception as exc:
            web = {"success": False, "error": str(exc), "selected_element": None}

    desk_closed = bool(desk.get("picker_closed"))
    web_closed = bool(web.get("picker_closed"))
    picker_closed = desk_closed and (not web_on or web_closed)

    with _lock:
        active = bool(_state.get("active")) and not picker_closed

    return {
        "success": True,
        "active": active,
        "unified": True,
        "record_mode": bool(_state.get("record_mode")),
        "web_enabled": web_on,
        "web_error": _state.get("web_error") or "",
        "desktop": desk,
        "web": web,
        "picker_closed": picker_closed,
        "last_pick": desk.get("last_pick"),
        "new_recorded_steps": list(desk.get("new_recorded_steps") or []),
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
