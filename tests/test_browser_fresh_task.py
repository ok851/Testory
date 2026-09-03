# -*- coding: utf-8 -*-
"""浏览器复用 / 新任务导航判定。"""
from __future__ import annotations


def test_urls_match_for_browser_reuse_same_host_different_path():
    from modules.ai.ai_external_browser_bridge import urls_match_for_browser_reuse

    assert urls_match_for_browser_reuse(
        "https://example.com/dashboard",
        "https://example.com/login",
    ) is False
    assert urls_match_for_browser_reuse(
        "https://example.com/login",
        "https://example.com/login",
    ) is True
    assert urls_match_for_browser_reuse(
        "about:blank",
        "https://example.com/login",
    ) is False
    assert urls_match_for_browser_reuse(
        "https://example.com/login?x=1",
        "https://example.com/login",
    ) is True


def test_cdp_disconnect_clears_debug_port_fields():
    from web_capture import cdp_browser as m

    m._set(debug_port=9222, cdp_ws="ws://127.0.0.1:9222/devtools/browser/x", page=object())
    m.disconnect(stop_browser=True)
    snap = m._snap()
    assert int(snap.get("debug_port") or 0) == 0
    assert not (snap.get("cdp_ws") or "")
    assert snap.get("page") is None
