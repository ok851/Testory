# -*- coding: utf-8 -*-
"""统一 Agent 会话与双手连接态。"""
from __future__ import annotations


def test_session_vars_shared_across_logical_entries():
    from agent_unified_session import (
        get_or_create_session,
        merge_cross_end_vars,
        reset_sessions_for_tests,
    )

    reset_sessions_for_tests()
    merge_cross_end_vars(7, {"sms_otp": "112233"}, session_id="default")
    sess = get_or_create_session(7, "default")
    assert sess["cross_end_vars"]["sms_otp"] == "112233"
    # 另一入口同 user 默认会话可读到
    again = get_or_create_session(7, None)
    assert again["cross_end_vars"]["sms_otp"] == "112233"


def test_chat_schemas_respect_connected_hands():
    from ai_chat_tool_loop import chat_tool_schemas

    only_phone = chat_tool_schemas(
        allow_hermes=False,
        platform_type="android",
        allow_desktop_windows_tools=False,
        allow_refine_test_plan=False,
        connected_hands={"phone": True, "desktop": False, "browser": False},
    )
    names = {s["function"]["name"] for s in only_phone if "function" in s}
    assert "mobile_extract_otp" in names
    assert "desktop_launch" not in names

    both = chat_tool_schemas(
        allow_hermes=False,
        platform_type="auto",
        allow_desktop_windows_tools=True,
        allow_refine_test_plan=False,
        connected_hands={"phone": True, "desktop": True, "browser": False},
    )
    names2 = {s["function"]["name"] for s in both if "function" in s}
    assert "mobile_extract_otp" in names2
    assert "desktop_launch" in names2 or "windows_launch_app" in names2


def test_list_paired_devices_helper():
    import mobile_sync_store as mss

    with mss._LOCK:
        mss._DEVICE_TOKENS.clear()
        mss._DEVICE_TOKENS["tok-abc"] = {
            "user_id": 42,
            "device_id": "pixel-1",
            "paired_at": 1.0,
        }
    devices = mss.list_paired_devices_for_user(42)
    assert len(devices) == 1
    assert devices[0]["device_id"] == "pixel-1"
    assert mss.list_paired_devices_for_user(99) == []
