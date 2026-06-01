# -*- coding: utf-8 -*-
"""网页捕获会话（extension / legacy_inject / cdp 运维）。"""

from __future__ import annotations

import secrets
import threading
from typing import Any, Dict, Optional

_lock = threading.Lock()
_session: Dict[str, Any] = {
    "active": False,
    "session_id": "",
    "mode": "extension",
    "record_mode": False,
    "case_id": 0,
    "platform_origin": "",
    "last_pick": None,
    "picker_closed": False,
    "error": "",
    "message": "",
    "cdp_port": 0,
    "armed": False,
    "preferred_browser": "",
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


def build_bookmarklet(platform_origin: str, session_id: str) -> str:
    base = (platform_origin or "").rstrip("/")
    inject_url = f"{base}/api/web-capture/highlight.js?session={session_id}&page_only=1"
    escaped = inject_url.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "javascript:(function(){"
        "var u='" + escaped + "';"
        "var s=document.createElement('script');"
        "s.src=u+(u.indexOf('?')>=0?'&':'?')+'_='+Date.now();"
        "(document.head||document.documentElement).appendChild(s);"
        "})();"
    )


def _build_bookmarklet(platform_origin: str, session_id: str) -> str:
    return build_bookmarklet(platform_origin, session_id)


def _toolbar_url(origin: str, session_id: str) -> str:
    if not origin or not session_id:
        return ""
    return f"{origin.rstrip('/')}/web-capture/toolbar?session={session_id}"


def capture_shell_url(origin: str, session_id: str) -> str:
    """捕获专用浏览器启动页（同页加载悬浮窗脚本，不依赖扩展注入 about:blank）。"""
    if not origin or not session_id:
        return ""
    return f"{origin.rstrip('/')}/web-capture/shell?session={session_id}"


def _ingest_extension_pick() -> None:
    """将扩展 WebSocket 拾取合并进会话（若存在）。"""
    try:
        from web_capture.extension_bridge import consume_extension_pick

        payload = consume_extension_pick()
        if not payload:
            return
        snap = _snap()
        sid = str(snap.get("session_id") or "")
        if not sid:
            return
        report_pick(sid, payload)
    except Exception:
        pass


def start_session(
    *,
    mode: str = "extension",
    record_mode: bool = False,
    case_id: Optional[int] = None,
    platform_origin: str = "",
    browser: str = "edge",
    start_url: str = "",
) -> Dict[str, Any]:
    stop_session(fast=True)
    m = (mode or "extension").strip().lower()
    if m not in ("cdp", "extension", "legacy_inject"):
        m = "extension"
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
        armed=False,
    )

    toolbar_url = _toolbar_url(origin, session_id)
    workspace_legacy = f"{origin}/web-capture/workspace?session={session_id}" if origin else ""
    inject_url = (
        f"{origin}/api/web-capture/highlight.js?session={session_id}&page_only=1" if origin else ""
    )
    bookmarklet = _build_bookmarklet(origin, session_id) if origin else ""

    out: Dict[str, Any] = {
        "success": True,
        "mode": m,
        "session_id": session_id,
        "workspace_url": toolbar_url,
        "toolbar_url": toolbar_url,
        "workspace_url_legacy": workspace_legacy,
        "inject_url": inject_url,
        "bookmarklet": bookmarklet,
        "web_capture_mode": m,
    }

    if m == "extension":
        from web_capture.extension_bridge import ensure_bridge_started
        from web_capture.plugin_market import (
            browser_label,
            get_capture_browser_options,
            get_preferred_browser_for_capture,
        )

        bridge = ensure_bridge_started(session_id=session_id)
        out.update(bridge)
        browsers = get_capture_browser_options()
        preferred = get_preferred_browser_for_capture()
        out["capture_browsers"] = browsers
        out["preferred_browser"] = preferred
        out["preferred_browser_label"] = browser_label(preferred) if preferred else ""
        _set(preferred_browser=browser or preferred)
        out["hint"] = "已在平台显示捕获悬浮窗，请打开待测页后点「开始捕获」"
        out["message"] = "网页捕获器已就绪"
        boot = bootstrap_extension_capture(browser=browser)
        out["browser_bootstrap"] = boot
        if boot.get("success"):
            if boot.get("message"):
                out["message"] = boot["message"]
                out["hint"] = boot["message"]
            out["browser"] = boot.get("browser") or preferred
            out["browser_label"] = boot.get("browser_label") or browser_label(preferred)
        else:
            out["warning"] = boot.get("error") or "未能打开捕获浏览器"
            out["hint"] = boot.get("error") or out["hint"]
    elif m == "cdp":
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
            out["hint"] = "CDP 调试模式（运维）"
            out["workspace_url"] = (
                f"{origin}/web-capture/workspace-v2?session={session_id}" if origin else ""
            )
    else:
        out["hint"] = "在捕获窗口加载 URL 后点「开始捕获」，或使用书签在真实页面拾取"
        out["workspace_url"] = workspace_legacy

    out["message"] = out.get("message") or "网页捕获已启动"
    return out


def bootstrap_extension_capture(browser: str = "") -> Dict[str, Any]:
    """打开捕获浏览器（控制面板在平台悬浮窗，不在浏览器内注入）。"""
    import time

    from web_capture.extension_bridge import get_extension_status
    from web_capture.plugin_market import browser_label, ensure_uat_capture_browser

    snap = _snap()
    sid = str(snap.get("session_id") or "")
    origin = str(snap.get("platform_origin") or "")
    if not sid:
        return {"success": False, "error": "捕获会话无效"}

    shell_url = capture_shell_url(origin, sid)
    browser_ready = ensure_uat_capture_browser(browser, shell_url=shell_url)
    if not browser_ready.get("success"):
        return browser_ready

    for _ in range(12):
        if get_extension_status().get("extension_connected"):
            break
        time.sleep(0.35)

    label = browser_ready.get("browser_label") or browser_label(browser)
    return {
        "success": True,
        "browser": browser_ready.get("browser") or browser,
        "browser_label": label,
        "launched": bool(browser_ready.get("launched")),
        "message": f"已打开{label or '浏览器'}，请使用平台悬浮窗点「开始捕获」",
    }


def arm_session(session_id: str, *, api_base: str = "", browser: str = "") -> Dict[str, Any]:
    if not validate_session_id(session_id):
        return {"success": False, "error": "捕获会话无效或已结束"}
    origin = api_base or _snap().get("platform_origin") or ""
    _set(armed=True, message="捕获中：请在浏览器页面悬停并单击元素")
    snap = _snap()
    mode = snap.get("mode") or "extension"
    if mode == "extension":
        import time

        from web_capture.extension_bridge import broadcast_arm, get_extension_status
        from web_capture.plugin_market import browser_label

        for _ in range(6):
            if get_extension_status().get("extension_connected"):
                break
            time.sleep(0.35)

        if not get_extension_status().get("extension_connected"):
            _set(armed=False)
            return {
                "success": False,
                "error": "捕获浏览器扩展未连接，请返回平台重新点击「网页捕获」",
            }

        result = broadcast_arm(api_base=origin, session_id=session_id)
        connected = bool(get_extension_status().get("extension_connected"))
        result["extension_connected"] = connected
        result["browser"] = browser or snap.get("preferred_browser") or ""
        result["browser_label"] = browser_label(result["browser"])
        if connected:
            result["message"] = "捕获已启动，请悬停高亮后单击元素"
        else:
            result["message"] = "捕获指令已发送，若仍无高亮请再次点击「开始捕获」"
        return result
    if mode == "cdp":
        from web_capture import cdp_browser
        from web_capture.cdp_picker import arm_picker, inject_all_frames

        page = cdp_browser.get_active_page()
        if not page:
            return {"success": False, "error": "无 CDP 页面"}
        inject_all_frames(page, api_base=origin, session_id=session_id)
        res = arm_picker(page)
        return res if res.get("success") else {"success": True, "message": "CDP 已武装"}
    return {"success": True, "message": "已武装；请在待测页使用书签注入后拾取"}


def disarm_session(session_id: str) -> Dict[str, Any]:
    if not validate_session_id(session_id):
        return {"success": False, "error": "捕获会话无效或已结束"}
    _set(armed=False, message="网页捕获已就绪")
    try:
        from web_capture.extension_bridge import broadcast_disarm

        broadcast_disarm()
    except Exception:
        pass
    return {"success": True}


def report_pick(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not validate_session_id(session_id):
        return {"success": False, "error": "捕获会话无效或已结束"}
    if not isinstance(payload, dict):
        return {"success": False, "error": "无效的拾取数据"}
    snap = _snap()
    mode = snap.get("mode") or "extension"
    try:
        from web_capture.locator_generator import format_dom_pick_payload

        formatted = format_dom_pick_payload(payload, capture_mode=mode)
        if mode == "cdp":
            from web_capture.cdp_picker import _enrich_pick_from_cdp_page

            formatted = _enrich_pick_from_cdp_page(formatted)
    except Exception as exc:
        return {"success": False, "error": str(exc) or "格式化拾取结果失败"}
    _set(last_pick=formatted, picker_closed=False, message="已捕获网页元素", armed=False)
    try:
        from web_capture.extension_bridge import broadcast_disarm

        broadcast_disarm()
    except Exception:
        pass
    return {"success": True, "selected_element": formatted}


def capture_browser_chrome(session_id: str, target: str) -> Dict[str, Any]:
    """捕获浏览器原生控件（地址栏/标签页等，无法 DOM 拾取）。"""
    if not validate_session_id(session_id):
        return {"success": False, "error": "捕获会话无效或已结束"}
    t = (target or "").strip().lower()
    if t not in ("address_bar", "tab", "back", "forward", "reload"):
        return {"success": False, "error": "不支持的浏览器控件类型"}

    tab: Dict[str, Any] = {}
    snap = _snap()
    mode = snap.get("mode") or "extension"
    if mode == "extension":
        try:
            from web_capture.extension_bridge import get_active_capture_tab

            tab = get_active_capture_tab()
        except Exception:
            tab = {}
    elif mode == "cdp":
        try:
            from web_capture import cdp_browser

            page = cdp_browser.get_active_page()
            if page:
                tab = {"url": page.url or "", "title": ""}
        except Exception:
            tab = {}

    url = str(tab.get("url") or "").strip()
    title = str(tab.get("title") or "").strip()
    blocked_prefix = ("chrome://", "edge://", "about:", "devtools://")
    if t in ("address_bar", "tab") and (not url or url.startswith(blocked_prefix)):
        return {
            "success": False,
            "error": "当前标签页无可用 URL，请先在捕获浏览器中打开待测 http(s) 页面",
        }

    if t == "address_bar":
        formatted: Dict[str, Any] = {
            "capture_kind": "browser_chrome",
            "chrome_target": "address_bar",
            "name": "navigate_地址栏",
            "suggested_action": "navigate",
            "input_value": url,
            "source_url": url,
            "page_name": url,
            "selector_type": "css",
            "selector_value": "",
            "tag_name": "address_bar",
            "description": "浏览器地址栏（导航到当前页 URL）",
            "locator_message": "浏览器原生控件，执行时将导航到捕获时的 URL",
        }
    elif t == "tab":
        formatted = {
            "capture_kind": "browser_chrome",
            "chrome_target": "tab",
            "name": "tab_" + (title[:24] or "当前标签页"),
            "suggested_action": "navigate",
            "input_value": url,
            "source_url": url,
            "page_name": title or url,
            "selector_type": "css",
            "selector_value": "",
            "tag_name": "browser_tab",
            "description": "浏览器标签页「" + (title or url) + "」",
            "tab_title": title,
            "tab_url": url,
            "locator_message": "浏览器标签页，执行时将导航到该页 URL",
        }
    else:
        action_map = {
            "back": ("browser_back", "浏览器后退"),
            "forward": ("browser_forward", "浏览器前进"),
            "reload": ("browser_reload", "浏览器刷新"),
        }
        act, label = action_map[t]
        formatted = {
            "capture_kind": "browser_chrome",
            "chrome_target": t,
            "name": act,
            "suggested_action": act,
            "input_value": "",
            "source_url": url,
            "page_name": title or url,
            "selector_type": "css",
            "selector_value": "",
            "tag_name": t,
            "description": label,
            "locator_message": label + "（浏览器原生控件）",
        }

    _set(armed=False, message="已捕获浏览器控件")
    try:
        from web_capture.extension_bridge import broadcast_disarm

        broadcast_disarm()
    except Exception:
        pass
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
    if snap.get("mode") == "extension":
        try:
            from web_capture.extension_bridge import broadcast_disarm

            broadcast_disarm()
        except Exception:
            pass
    _set(active=False, picker_closed=True, armed=False)
    return {"success": True, "stopped": True, "fast": fast}


def get_session_status(*, consume_last_pick: bool = False) -> Dict[str, Any]:
    _ingest_extension_pick()
    snap = _snap()
    selected = snap.get("last_pick")
    out_pick = selected
    if out_pick is not None and consume_last_pick:
        _set(last_pick=None)

    active = bool(snap.get("active")) and not bool(snap.get("picker_closed"))
    picker_closed = bool(snap.get("picker_closed")) or (
        not snap.get("active") and out_pick is None
    )
    origin = snap.get("platform_origin") or ""
    sid = snap.get("session_id") or ""

    return {
        "success": True,
        "active": active,
        "picker_closed": picker_closed,
        "armed": bool(snap.get("armed")),
        "selected_element": out_pick,
        "session_id": sid,
        "mode": snap.get("mode") or "extension",
        "web_capture_mode": snap.get("mode") or "extension",
        "record_mode": bool(snap.get("record_mode")),
        "case_id": int(snap.get("case_id") or 0),
        "message": snap.get("message") or "",
        "error": snap.get("error") or "",
        "cdp_port": int(snap.get("cdp_port") or 0),
        "inject_url": (
            f"{origin}/api/web-capture/highlight.js?session={sid}&page_only=1"
            if origin and sid
            else ""
        ),
        "workspace_url": _toolbar_url(origin, sid),
        "toolbar_url": _toolbar_url(origin, sid),
        "bookmarklet": _build_bookmarklet(origin, sid) if origin and sid else "",
    }
