# -*- coding: utf-8 -*-
"""web_dom_picker 会话与格式化测试（不依赖 Playwright）。"""

from modules.web.web_dom_picker import (
    format_dom_pick_payload,
    get_web_dom_picker_status,
    report_web_dom_pick,
    start_web_dom_picker,
    stop_web_dom_picker,
    validate_session_id,
)


def test_start_always_succeeds_without_playwright():
    out = start_web_dom_picker(platform_origin="http://127.0.0.1:5000")
    assert out["success"] is True
    assert out.get("session_id")
    assert out.get("bookmarklet", "").startswith("javascript:")
    assert "/web-capture/toolbar" in (out.get("workspace_url") or out.get("toolbar_url") or "")
    assert "highlight.js" in (out.get("bookmarklet") or "")
    assert validate_session_id(out["session_id"])


def test_report_pick_and_consume():
    start_web_dom_picker(platform_origin="http://127.0.0.1:5000")
    sid = get_web_dom_picker_status()["session_id"]
    raw = {
        "selector": "#login-btn",
        "elementInfo": {
            "tagName": "BUTTON",
            "id": "login-btn",
            "className": "btn primary",
            "textContent": "登录",
            "attributes": {},
        },
    }
    rep = report_web_dom_pick(sid, raw)
    assert rep["success"] is True
    assert rep["selected_element"]["selector_type"] == "id"
    assert rep["selected_element"]["selector_value"] == "login-btn"

    st = get_web_dom_picker_status(consume_last_pick=True)
    assert st["selected_element"]["selector_value"] == "login-btn"
    assert get_web_dom_picker_status()["selected_element"] is None


def test_format_dom_pick_partial_text():
    formatted = format_dom_pick_payload({
        "selector": "div",
        "elementInfo": {
            "tagName": "DIV",
            "id": "",
            "className": "",
            "textContent": "这是一段足够长的可见文本",
            "attributes": {},
        },
    })
    assert formatted["selector_type"] == "partial_text"


def test_stop_clears_session():
    start_web_dom_picker(platform_origin="http://127.0.0.1:5000")
    stop_web_dom_picker()
    assert get_web_dom_picker_status()["active"] is False
