# -*- coding: utf-8 -*-
"""CDP 模式下注入拾取脚本并处理拾取结果。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, Optional

from web_capture import cdp_browser
from web_capture.locator_generator import format_dom_pick_payload

_HIGHLIGHT_JS: Optional[str] = None


def _highlight_script_path() -> Path:
    return Path(__file__).resolve().parent.parent / "static" / "js" / "web_capture_highlight.js"


def get_highlight_js(api_base: str = "", session_id: str = "") -> str:
    global _HIGHLIGHT_JS
    if _HIGHLIGHT_JS is None:
        p = _highlight_script_path()
        try:
            _HIGHLIGHT_JS = p.read_text(encoding="utf-8")
        except OSError:
            _HIGHLIGHT_JS = "// web_capture_highlight.js missing\n"
    return (
        _HIGHLIGHT_JS.replace("__API_BASE__", api_base or "")
        .replace("__SESSION__", session_id or "")
        .replace("__PICK_MODE__", "http")
    )


def inject_picker(page, *, api_base: str = "", session_id: str = "") -> bool:
    js = get_highlight_js(api_base, session_id)
    try:
        page.add_init_script(js)
    except Exception:
        pass
    try:
        page.evaluate(js)
        return True
    except Exception:
        return False


def inject_all_frames(page, *, api_base: str = "", session_id: str = "") -> int:
    n = 0
    if inject_picker(page, api_base=api_base, session_id=session_id):
        n += 1
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            frame.evaluate(get_highlight_js(api_base, session_id))
            n += 1
        except Exception:
            pass
    return n


def arm_picker(page) -> Dict[str, Any]:
    try:
        page.evaluate("() => window.__uatWebCaptureArm && window.__uatWebCaptureArm()")
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _enrich_pick_from_cdp_page(formatted: Dict[str, Any]) -> Dict[str, Any]:
    page = cdp_browser.get_active_page()
    if not page:
        return formatted
    st = formatted.get("selector_type") or "css"
    sv = formatted.get("selector_value") or ""
    if not sv:
        return formatted
    try:
        from web_capture.playwright_locator import convert_selector

        loc_sel, loc_st = convert_selector(sv, st)
        if loc_st == "xpath":
            loc = page.locator(f"xpath={loc_sel}")
        elif st == "partial_text":
            loc = page.get_by_text(sv, exact=False)
        else:
            loc = page.locator(loc_sel)
        count = loc.count()
        formatted["match_count"] = count
        formatted["locator_unique"] = count == 1
        formatted["locator_message"] = f"已找到 {count} 个元素" if count else "未找到元素"
        if count >= 1:
            shot = loc.first.screenshot(timeout=5000)
            formatted["preview_image_b64"] = base64.b64encode(shot).decode("ascii")
    except Exception:
        pass
    return formatted


def process_cdp_pick_payload(raw: Dict[str, Any], *, capture_mode: str = "cdp") -> Dict[str, Any]:
    formatted = format_dom_pick_payload(raw, capture_mode=capture_mode)
    return _enrich_pick_from_cdp_page(formatted)


def start_cdp_pick_session(
    *,
    session_id: str,
    api_base: str,
    url: str = "",
    browser: str = "edge",
) -> Dict[str, Any]:
    launch = cdp_browser.launch_debug_browser(browser=browser, url=url)
    if not launch.get("success"):
        return launch
    conn = cdp_browser.connect_playwright_over_cdp(launch.get("debug_port"))
    if not conn.get("success"):
        return conn
    page = cdp_browser.get_active_page()
    if not page:
        return {"success": False, "error": "无活动页面"}
    if url and launch.get("already_running"):
        cdp_browser.navigate(url)
        page = cdp_browser.get_active_page()
    inject_all_frames(page, api_base=api_base, session_id=session_id)
    arm_picker(page)
    return {
        "success": True,
        "debug_port": launch.get("debug_port"),
        "page_url": page.url if page else "",
        "message": "CDP 捕获已就绪，请在浏览器中悬停并单击元素",
    }
