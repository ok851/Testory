# -*- coding: utf-8 -*-

from web_capture.session import (
    get_session_status,
    report_pick,
    start_session,
    stop_session,
    validate_session_id,
)


def test_legacy_inject_session_start():
    stop_session(fast=True)
    r = start_session(mode="legacy_inject", platform_origin="http://localhost:5000")
    assert r["success"] is True
    sid = r["session_id"]
    assert validate_session_id(sid)
    pick = report_pick(
        sid,
        {
            "selector": "#btn",
            "elementInfo": {"tagName": "BUTTON", "id": "btn", "attributes": {}},
        },
    )
    assert pick["success"] is True
    st = get_session_status(consume_last_pick=True)
    assert st["selected_element"]["selector_value"]


def test_stop_session():
    stop_session(fast=True)
    st = get_session_status()
    assert st["active"] is False
