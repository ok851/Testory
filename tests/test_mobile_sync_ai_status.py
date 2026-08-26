# -*- coding: utf-8 -*-
"""移动端 sync AI 使用 PC 已绑定 LLM profile。"""
from __future__ import annotations

from modules.mobile.mobile_sync_store import _safe_llm_status_payload


def test_safe_llm_status_empty():
    p = _safe_llm_status_payload(None)
    assert p["ready"] is False
    assert "绑定" in (p.get("message") or "")


def test_safe_llm_status_ready():
    p = _safe_llm_status_payload({
        "id": "p1",
        "provider": "openai_compatible",
        "model": "qwen-plus",
    })
    assert p["ready"] is True
    assert p["model"] == "qwen-plus"
    assert p["provider"] == "openai_compatible"


def test_mobile_ai_chitchat_skips_llm():
    from modules.mobile.mobile_sync_store import _mobile_ai_chitchat_reply, _normalize_phone_ai_action

    r = _mobile_ai_chitchat_reply("你是谁？")
    assert r is not None
    assert r["steps"] == []
    assert "助手" in (r.get("description") or "")
    assert _mobile_ai_chitchat_reply("打开设置并开启飞行模式") is None
    assert _normalize_phone_ai_action("open_app") == "tap"
    assert _normalize_phone_ai_action("input_text") == "input"


def test_mobile_ai_mode_defaults_chat_not_force_json_path():
    """对话模式应走 free chat，而不是 generate_case_and_steps。"""
    from unittest.mock import MagicMock, patch

    from modules.mobile.mobile_sync_store import _mobile_ai_free_chat

    profile = {"provider": "custom_openai", "model_id": "x", "api_key": "k", "base_url": "http://x"}
    status = {"ready": True, "provider": "custom_openai", "model": "x"}
    with patch(
        "ai_multi_provider.dispatch_chat_completion_messages",
        return_value={"role": "assistant", "content": "可以，切换到生成用例模式再说一次。"},
    ):
        with patch("ai_local_inference.local_ai_service", MagicMock()):
            out = _mobile_ai_free_chat("帮我打开QQ", profile, status)
    assert out["success"] is True
    assert out["steps"] == []
    assert out["mode"] == "chat"
    assert "生成用例" in out["description"]
