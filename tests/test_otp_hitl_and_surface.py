# -*- coding: utf-8 -*-
"""HITL / 网页层路由：已有 sms_otp 时不得请用户手填；网页禁用 windows_type。"""

from modules.ai.agent_hitl import looks_like_hitl_needed
from modules.ai.ai_chat_tool_loop import (
    _reject_web_task_desktop_input,
    _rewrite_spurious_otp_need_user_action,
)


def test_hitl_suppressed_when_sms_otp_and_need_user_fill():
    text = (
        "NEED_USER_ACTION: 验证码输入状态，需在浏览器窗口手动完成登录。"
        "请用户在本机浏览器中：在验证码输入框填入 `025392`"
    )
    assert looks_like_hitl_needed(
        text,
        tools_used=["hermes_execute", "mobile_extract_otp"],
        cross_end_vars={"sms_otp": "025392"},
    ) is False


def test_hitl_still_true_for_captcha_without_otp():
    text = "NEED_USER_ACTION: 请完成滑块人机验证"
    assert looks_like_hitl_needed(text, tools_used=[], cross_end_vars={}) is True


def test_rewrite_otp_need_user_action():
    raw = (
        '{"ok": true, "result": "NEED_USER_ACTION: 无法直接操作验证码输入框，'
        '请用户手动填入 025392 并登录。"}'
    )
    out = _rewrite_spurious_otp_need_user_action(
        raw, {"cross_end_vars": {"sms_otp": "025392"}}
    )
    assert "NEED_USER_ACTION" not in out or "forbid_hitl" in out
    assert "025392" in out
    assert "forbid_hitl" in out
    assert "browser_type" in out or "browser_console" in out


def test_reject_windows_type_on_web_after_browser_touched():
    err = _reject_web_task_desktop_input(
        "windows_type_text",
        {"text": "025392"},
        {
            "_web_task_browser_touched": True,
            "cross_end_vars": {"sms_otp": "025392"},
        },
        None,
    )
    assert err
    assert "hermes_execute" in err
    assert "windows_type_text" in err
