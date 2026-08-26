"""Ollama /api/chat 响应解析：list 型 content、thinking 回退、顶层 error。"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from modules.ai.ai_local_inference import LocalAIService, _ollama_api_chat_assistant_text


def test_list_content_parts_joined() -> None:
    out = _ollama_api_chat_assistant_text(
        {"message": {"content": [{"type": "text", "text": '{"a":1}'}, {"type": "text", "text": " tail"}]}}
    )
    assert '{"a":1}' in out and "tail" in out


def test_thinking_fallback_when_content_empty() -> None:
    assert _ollama_api_chat_assistant_text({"message": {"content": "", "thinking": "  hi  "}}) == "hi"


def test_top_level_error_raises() -> None:
    try:
        _ollama_api_chat_assistant_text({"error": "model runner failed"})
    except ValueError as e:
        assert "Ollama" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_parse_json_after_think_strip_empty_uses_raw() -> None:
    # 与 _strip_llm_noise 使用相同的 <think>…</think> 标签（此处用 chr 拼接以免与注释混淆）
    ot = chr(60) + chr(116) + chr(104) + chr(105) + chr(110) + chr(107) + chr(62)
    ct = chr(60) + chr(47) + chr(116) + chr(104) + chr(105) + chr(110) + chr(107) + chr(62)
    raw = (
        ot
        + '{"case_name":"x","case_url":"","description":"","precondition":"","expected_result":"","steps":[]}'
        + ct
    )
    svc = LocalAIService()
    plan = svc._parse_json_response(raw)
    assert plan.get("case_name") == "x"


if __name__ == "__main__":
    test_list_content_parts_joined()
    test_thinking_fallback_when_content_empty()
    test_top_level_error_raises()
    test_parse_json_after_think_strip_empty_uses_raw()
    print("ok")
