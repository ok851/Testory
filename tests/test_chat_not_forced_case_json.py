# -*- coding: utf-8 -*-
"""对话不应被强制解析成空壳用例 JSON；执行路径应自动提升为 hermes_execute。"""
from __future__ import annotations

import inspect
import json

from modules.ai.ai_chat_tool_loop import (
    _instruction_from_case_json_hint,
    _is_plausible_case_plan,
    _looks_like_case_json_blob,
    _looks_like_fake_completion_claim,
    _nl_fallback_when_case_json,
    _resolve_no_tool_assistant_text,
    _strip_invented_case_json,
    _synthesize_auto_execute_tool_calls,
)
from modules.ai.ai_multi_provider import dispatch_chat, openai_compatible_chat


def test_garbage_assert_plan_rejected() -> None:
    plan = {
        "case_name": "",
        "case_url": "",
        "steps": [{"name": "使用", "action": "assert"}],
    }
    assert not _is_plausible_case_plan(plan)


def test_real_click_plan_accepted() -> None:
    plan = {
        "case_name": "登录",
        "steps": [{"action": "click", "target": "登录"}],
    }
    assert _is_plausible_case_plan(plan)


def test_strip_nested_case_json() -> None:
    text = (
        "你好\n"
        '{"case_name":"","case_url":"","description":"","precondition":"",'
        '"expected_result":"","steps":[{"name":"使用","action":"assert"}]}\n'
        "再见"
    )
    out = _strip_invented_case_json(text)
    assert "case_name" not in out
    assert "你好" in out or "再见" in out


def test_strip_fenced_empty_case_returns_empty_not_original() -> None:
    """剥空后不得回退原文——这是用户看到胡乱 JSON 的根因。"""
    blob = """```json
{
    "case_name": "",
    "case_url": "",
    "description": "",
    "precondition": "",
    "expected_result": "",
    "steps": [
        {
            "name": "使用",
            "action": "assert"
        }
    ]
}
```"""
    out = _strip_invented_case_json(blob)
    assert out == ""
    assert _looks_like_case_json_blob(blob)
    assert "case_name" not in _nl_fallback_when_case_json(blob)


def test_strip_raw_case_object_no_fallback() -> None:
    blob = (
        '{"case_name":"x","case_url":"","description":"","precondition":"",'
        '"expected_result":"","steps":[{"action":"assert","name":"使用"}]}'
    )
    assert _strip_invented_case_json(blob) == ""


def test_fake_completion_claim() -> None:
    assert _looks_like_fake_completion_claim("完成")
    assert _looks_like_fake_completion_claim("已完成。")
    assert _looks_like_fake_completion_claim("任务执行完成")
    assert not _looks_like_fake_completion_claim("未能完成，请检查浏览器是否启动")


def test_case_json_auto_promotes_to_hermes_execute() -> None:
    """执行路径：模型吐用例 JSON → 自动提升为 hermes_execute，而非硬失败。"""

    class _P:
        message = "打开百度搜索 AI"
        allow_refine_test_plan = False

    blob = (
        '{"case_name":"百度","steps":[{"action":"navigate","input_value":"https://www.baidu.com"},'
        '{"action":"input","target":"搜索框","input_value":"AI"}]}'
    )
    meta: dict = {"expect_tools": True, "tools_used": []}
    tools = [{"type": "function", "function": {"name": "hermes_execute"}}]
    r = _resolve_no_tool_assistant_text(
        blob, params=_P(), meta=meta, tools=tools, round_idx=0
    )
    assert r["action"] == "tool_calls"
    assert r.get("auto_injected") is True
    assert r["failed"] is False
    tcs = r["tool_calls"]
    assert isinstance(tcs, list) and tcs
    assert tcs[0]["function"]["name"] == "hermes_execute"
    args = json.loads(tcs[0]["function"]["arguments"])
    assert "打开百度搜索 AI" in args.get("instruction", "")
    assert "navigate" in args.get("instruction", "") or "参考步骤" in args.get("instruction", "")


def test_fake_complete_also_auto_promotes_when_hermes_available() -> None:
    class _P:
        message = "打开百度搜索 AI"
        allow_refine_test_plan = False

    meta: dict = {"expect_tools": True, "tools_used": []}
    tools = [{"type": "function", "function": {"name": "hermes_execute"}}]
    r = _resolve_no_tool_assistant_text(
        "完成", params=_P(), meta=meta, tools=tools, round_idx=0
    )
    assert r["action"] == "tool_calls"
    assert r.get("auto_injected") is True
    assert meta.get("_auto_injected_execute") is True
    # 二次不应重复注入
    r2 = _resolve_no_tool_assistant_text(
        "完成", params=_P(), meta=meta, tools=tools, round_idx=1
    )
    assert r2["action"] in ("nudge", "reply")


def test_no_hermes_then_nudge_then_fail() -> None:
    class _P:
        message = "打开记事本写 hello"
        allow_refine_test_plan = False

    meta: dict = {"expect_tools": True, "tools_used": []}
    tools = [{"type": "function", "function": {"name": "windows_launch_app"}}]
    r1 = _resolve_no_tool_assistant_text(
        "完成", params=_P(), meta=meta, tools=tools, round_idx=0
    )
    assert r1["action"] == "nudge"
    r2 = _resolve_no_tool_assistant_text(
        "完成", params=_P(), meta=meta, tools=tools, round_idx=1
    )
    assert r2["action"] == "reply"
    assert r2["failed"] is True
    assert "未调用" in (r2["clean_text"] or "") or "自动转交" in (r2["clean_text"] or "")


def test_synthesize_instruction_keeps_user_goal() -> None:
    class _P:
        message = "访问 https://example.com 点登录"

    meta: dict = {}
    tools = [{"type": "function", "function": {"name": "hermes_execute"}}]
    calls = _synthesize_auto_execute_tool_calls(
        params=_P(),
        tools=tools,
        text='{"case_name":"x","steps":[{"action":"click","target":"登录"}]}',
        meta=meta,
    )
    assert calls
    args = json.loads(calls[0]["function"]["arguments"])
    assert "example.com" in args["instruction"]
    hint = _instruction_from_case_json_hint(
        '{"case_name":"x","steps":[{"action":"click","target":"登录"}]}'
    )
    assert "click" in hint and "登录" in hint


def test_resolve_chat_case_json_to_nl() -> None:
    class _P:
        message = "你好"
        allow_refine_test_plan = True

    meta: dict = {"expect_tools": False, "tools_used": []}
    blob = '{"case_name":"","steps":[{"action":"assert","name":"使用"}]}'
    r = _resolve_no_tool_assistant_text(
        blob, params=_P(), meta=meta, tools=[], round_idx=0
    )
    assert r["action"] == "reply"
    assert r["failed"] is False
    assert "case_name" not in (r["clean_text"] or "")
    assert "不会用用例 JSON" in (r["clean_text"] or "")


def test_dispatch_chat_accepts_purpose() -> None:
    sig = inspect.signature(dispatch_chat)
    assert "purpose" in sig.parameters
    sig2 = inspect.signature(openai_compatible_chat)
    assert "purpose" in sig2.parameters


if __name__ == "__main__":
    test_garbage_assert_plan_rejected()
    test_real_click_plan_accepted()
    test_strip_nested_case_json()
    test_strip_fenced_empty_case_returns_empty_not_original()
    test_strip_raw_case_object_no_fallback()
    test_fake_completion_claim()
    test_case_json_auto_promotes_to_hermes_execute()
    test_fake_complete_also_auto_promotes_when_hermes_available()
    test_no_hermes_then_nudge_then_fail()
    test_synthesize_instruction_keeps_user_goal()
    test_resolve_chat_case_json_to_nl()
    test_dispatch_chat_accepts_purpose()
    print("ok")
