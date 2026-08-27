# -*- coding: utf-8 -*-
"""统一 Agent 会话与双手连接态。"""
from __future__ import annotations


def test_session_vars_shared_across_logical_entries():
    from modules.ai.agent_unified_session import (
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
    from modules.ai.ai_chat_tool_loop import chat_tool_schemas

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
    from modules.mobile import mobile_sync_store as mss

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


def test_snapshot_connected_hands_detects_pure_adb():
    """纯 ADB 已授权真机（未同步 / 未点连接）也应识别为 phone 手。

    这是 agent 多端联动失败的根因修复点：此前 snapshot_connected_hands
    只认 sync 配对与 connect 流程 udid，纯 ADB 手机永远 phone=False，
    导致 mobile_* 工具不挂载、跨端联动与共享屏幕（手机镜像）不生效。
    """
    from unittest import mock

    from modules.ai import agent_unified_session as mod

    with mock.patch(
        "modules.mobile.mobile_sync_store.list_paired_devices_for_user",
        return_value=[],
    ), mock.patch(
        "modules.mobile.mobile_device_manager.get_connected_udid",
        return_value=None,
    ), mock.patch(
        "modules.mobile.mobile_device_manager.pick_best_authorized_device",
        return_value={"udid": "ADB123DEVICE", "state": "device", "model": "Pixel"},
    ):
        hands = mod.snapshot_connected_hands(user_id=1)
    assert hands["phone"] is True
    assert hands["adb_serial"] == "ADB123DEVICE"
    assert hands["phone_devices"][0]["source"] == "adb"


def test_chat_schemas_mount_mobile_for_pure_adb_phone():
    """纯 ADB 手机识别为 phone 手后，chat_tool_schemas 必须挂载 mobile_* 工具。"""
    from unittest import mock

    from modules.ai import agent_unified_session as mod
    from modules.ai.ai_chat_tool_loop import chat_tool_schemas

    with mock.patch(
        "modules.mobile.mobile_sync_store.list_paired_devices_for_user",
        return_value=[],
    ), mock.patch(
        "modules.mobile.mobile_device_manager.get_connected_udid",
        return_value=None,
    ), mock.patch(
        "modules.mobile.mobile_device_manager.pick_best_authorized_device",
        return_value={"udid": "ADB123DEVICE", "state": "device", "model": "Pixel"},
    ):
        hands = mod.snapshot_connected_hands(user_id=1)

    schemas = chat_tool_schemas(
        allow_hermes=False,
        platform_type="android",
        allow_desktop_windows_tools=False,
        allow_refine_test_plan=False,
        connected_hands=hands,
    )
    names = {s["function"]["name"] for s in schemas if "function" in s}
    assert "mobile_extract_otp" in names
    assert "mobile_back" in names
