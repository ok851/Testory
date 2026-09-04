# -*- coding: utf-8 -*-
"""禁止向用户索要短信验证码；网页段后自动注入 mobile_extract_otp。"""

from types import SimpleNamespace

from modules.ai.ai_chat_tool_loop import (
    _looks_like_asking_user_for_sms_otp,
    _maybe_force_mobile_otp_tool_calls,
    _web_otp_flow_needs_mobile_extract,
)


def test_looks_like_asking_user_for_sms_otp():
    text = (
        "请查看手机短信，找到来自 163.com 或网易的验证码短信，"
        "将 6 位数字验证码告诉我（或者直接回复验证码内容）"
    )
    assert _looks_like_asking_user_for_sms_otp(text) is True
    assert _looks_like_asking_user_for_sms_otp("已填入验证码并点击下一步") is False


def test_force_otp_after_hermes():
    params = SimpleNamespace(
        message="进入 https://id.grow.163.com/ 输入手机号，从移动端获取验证码并填写"
    )
    meta = {
        "tools_used": ["hermes_execute"],
        "_web_task_browser_touched": True,
        "cross_end_vars": {},
    }
    tools = [
        {
            "type": "function",
            "function": {"name": "mobile_extract_otp", "parameters": {}},
        }
    ]
    assert _web_otp_flow_needs_mobile_extract(params, meta) is True
    calls = _maybe_force_mobile_otp_tool_calls(
        "请把验证码告诉我", params=params, meta=meta, tools=tools
    )
    assert calls and calls[0]["function"]["name"] == "mobile_extract_otp"
    # 只注入一次
    assert _maybe_force_mobile_otp_tool_calls(
        "请把验证码告诉我", params=params, meta=meta, tools=tools
    ) is None


def test_no_force_when_sms_already_present():
    params = SimpleNamespace(message="打开 https://x.com 获取验证码")
    meta = {
        "tools_used": ["hermes_execute"],
        "_web_task_browser_touched": True,
        "cross_end_vars": {"sms_otp": "123456"},
    }
    tools = [{"type": "function", "function": {"name": "mobile_extract_otp"}}]
    assert _web_otp_flow_needs_mobile_extract(params, meta) is False
