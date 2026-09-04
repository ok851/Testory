# -*- coding: utf-8 -*-
"""用例录制保真：禁止工具名占位；多端 PC→手机→PC 全收录。"""

from modules.ai.ai_action_recorder import (
    ActionRecorder,
    is_tool_name_placeholder,
    is_case_worthy_for_platform,
)
from modules.hermes.hermes_gateway_client import merge_hermes_tool_events


def test_is_tool_name_placeholder():
    assert is_tool_name_placeholder("browser_type")
    assert is_tool_name_placeholder("browser_navigate")
    assert is_tool_name_placeholder("mobile_extract_otp")
    assert not is_tool_name_placeholder("https://example.com/login")
    assert not is_tool_name_placeholder("获取验证码")
    assert not is_tool_name_placeholder("13800138000")


def test_merge_hermes_tool_events_joins_args_and_result():
    events = [
        {
            "name": "browser_type",
            "args": {"ref": "@e3", "text": "13800138000"},
            "status": "running",
            "sse_event": "tool_calls_delta",
        },
        {
            "name": "browser_type",
            "args": {},
            "status": "completed",
            "result": {"ok": True},
        },
    ]
    merged = merge_hermes_tool_events(events)
    assert len(merged) == 1
    assert merged[0]["args"].get("text") == "13800138000"
    assert merged[0].get("result", {}).get("ok") is True


def test_capture_rejects_empty_args_tool_name_target():
    rec = ActionRecorder(platform="web", case_url="https://example.com/login")
    out = rec.capture_from_tool_event(
        name="browser_type",
        args={},
        result={"ok": True},
        status="completed",
    )
    assert out == []


def test_capture_navigate_uses_case_url_when_args_empty():
    rec = ActionRecorder(platform="web", case_url="https://example.com/login")
    out = rec.capture_from_tool_event(
        name="browser_navigate",
        args={},
        result={"ok": True},
        status="completed",
    )
    assert len(out) == 1
    assert out[0].input_data.startswith("https://example.com/login")
    assert not is_tool_name_placeholder(out[0].target)


def test_capture_type_keeps_input_and_description_selector():
    rec = ActionRecorder(platform="web")
    out = rec.capture_from_tool_event(
        name="browser_type",
        args={"elementDescription": "手机号", "text": "13800138000"},
        result={"ok": True},
        status="completed",
    )
    assert len(out) == 1
    assert out[0].input_data == "13800138000"
    assert out[0].target == "手机号"
    steps = rec.to_case_steps()
    assert len(steps) == 1
    assert steps[0]["action"] == "input"
    assert steps[0]["input_value"] == "13800138000"
    assert steps[0]["selector_value"] == "手机号"
    assert steps[0]["selector_type"] == "text"


def test_capture_navigate_with_url_arg():
    rec = ActionRecorder(platform="web")
    out = rec.capture_from_tool_event(
        name="browser_navigate",
        args={"url": "https://app.example.com/otp"},
        result={"ok": True},
        status="completed",
    )
    assert len(out) == 1
    plan, _ = rec.build_normalized_plan(case_name="t", case_url="", instruction="")
    assert plan["steps"][0]["action"] == "navigate"
    assert plan["steps"][0]["input_value"] == "https://app.example.com/otp"
    assert plan["steps"][0]["selector_value"] == ""


def test_cross_end_pc_mobile_pc_all_recorded():
    rec = ActionRecorder(platform="web", case_url="https://example.com")
    rec.capture_from_tool_event(
        name="browser_navigate",
        args={"url": "https://example.com/login"},
        result={"ok": True},
        status="completed",
    )
    rec.capture_from_tool_event(
        name="browser_type",
        args={"elementDescription": "手机号", "text": "13900001111"},
        result={"ok": True},
        status="completed",
    )
    rec.capture_from_tool_event(
        name="browser_click",
        args={"elementDescription": "获取验证码"},
        result={"ok": True},
        status="completed",
    )
    rec.capture_from_tool_event(
        name="mobile_extract_otp",
        args={"timeout_sec": 60},
        result={"ok": True, "otp": "123456"},
        status="completed",
    )
    # 回 PC：填入验证码并登录
    rec.capture_from_tool_event(
        name="browser_type",
        args={"elementDescription": "验证码", "text": "123456"},
        result={"ok": True},
        status="completed",
    )
    rec.capture_from_tool_event(
        name="browser_click",
        args={"elementDescription": "登录"},
        result={"ok": True},
        status="completed",
    )
    steps = rec.to_case_steps()
    actions = [s["action"] for s in steps]
    assert "navigate" in actions
    assert "extract_otp" in actions
    assert actions.count("input") >= 2
    assert actions.count("click") >= 2
    # 回程 Web 步骤必须在 OTP 之后
    otp_i = next(i for i, s in enumerate(steps) if s["action"] == "extract_otp")
    assert any(s["action"] in ("input", "click") and i > otp_i for i, s in enumerate(steps))
    otp_step = steps[otp_i]
    assert otp_step["automation_layer"] == "android"
    # 最后一步仍是 web
    assert steps[-1]["automation_layer"] == "web"
    assert steps[-1]["selector_value"] == "登录"


def test_web_platform_still_allows_extract_otp():
    assert is_case_worthy_for_platform("extract_otp", "web")
    assert is_case_worthy_for_platform("navigate", "auto")
    assert is_case_worthy_for_platform("click", "auto")


def test_build_plan_strips_tool_name_placeholders():
    rec = ActionRecorder(platform="web", case_url="https://x.com")
    # 模拟历史脏数据
    from modules.ai.ai_action_recorder import ActionRecord

    rec.records.append(
        ActionRecord(
            action_id="act_0",
            action_type="input",
            target="browser_type",
            input_data="",
            result="browser_type",
            status="success",
            platform_layer="web",
        )
    )
    steps = rec.to_case_steps()
    assert steps == []
