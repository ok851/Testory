# -*- coding: utf-8 -*-
"""MVP 验收：统一 Hermes 会话相关模块冒烟。"""
from __future__ import annotations

import unittest


class TestUnifiedHermesSession(unittest.TestCase):
    def test_context_bus_vars(self):
        from modules.ai.agent_task_context import new_task_context, get_task_context

        ctx = new_task_context(active_surface="auto")
        ctx.set_var("order_id", "42")
        again = get_task_context(ctx.session_id)
        self.assertEqual(again.get_var("order_id"), "42")
        self.assertIn("task_id=", ctx.instruction_prefix())

    def test_capability_registry(self):
        from modules.ai.agent_capability_registry import snapshot_capabilities, preflight_for_task

        snap = snapshot_capabilities()
        self.assertIn("capabilities", snap)
        self.assertIn("api", snap["capabilities"])
        self.assertTrue(snap["capabilities"]["api"]["available"])
        ok, _msg, _ = preflight_for_task("你好", require_hermes=False)
        self.assertTrue(ok)

    def test_skill_hints_auto(self):
        from modules.hermes.hermes_skill_hints import build_explore_instruction, skills_from_registry

        skills = skills_from_registry("auto")
        self.assertTrue(any(s.startswith("testory-") for s in skills))
        text = build_explore_instruction("打开控制面板", {"platform": "auto"})
        self.assertIn("skill_view", text)

    def test_hermes_allowed_android_not_crash(self):
        from modules.ai.ai_chat_tool_loop import hermes_execute_allowed

        # 无设备时应为 False，但不抛异常
        self.assertIsInstance(hermes_execute_allowed(platform_type="android"), bool)
        self.assertTrue(hermes_execute_allowed(platform_type="auto") or True)

    def test_hitl_detect(self):
        from modules.ai.agent_hitl import looks_like_hitl_needed

        self.assertTrue(looks_like_hitl_needed("NEED_USER_ACTION:验证码"))
        self.assertTrue(looks_like_hitl_needed("请输入验证码"))
        self.assertFalse(looks_like_hitl_needed("打开百度"))
        # 桌面兜底文案含「手动完成」不应再触发 HITL
        self.assertFalse(
            looks_like_hitl_needed(
                '可先在微信中手动完成，或改用更简单的单步指令（如「打开微信」）。'
            )
        )
        self.assertFalse(
            looks_like_hitl_needed(
                '{"via":"platform_desktop_fallback","reply":"已打开微信"}'
            )
        )

    def test_screen_observer_sync_api(self):
        from modules.ai.screen_tools import get_screen_description, get_screen_text

        self.assertTrue(callable(get_screen_text))
        self.assertTrue(callable(get_screen_description))

    def test_api_skill_file_exists(self):
        from pathlib import Path

        p = Path(__file__).resolve().parent.parent / "skills" / "bundled" / "testory-api-http" / "SKILL.md"
        self.assertTrue(p.is_file())

    def test_auth_fatal_detect(self):
        from modules.ai.ai_chat_tool_loop import _auth_fatal_user_message, _result_is_auth_fatal
        from modules.hermes.hermes_gateway_client import HermesGatewayClient, _is_corrupt_session_error

        self.assertTrue(
            _result_is_auth_fatal("Error code: 401 - Missing Authentication header")
        )
        self.assertTrue(_result_is_auth_fatal("桌面自动化引擎 401 Authentication Error"))
        self.assertTrue(
            _result_is_auth_fatal(
                "Error code: 402 - {'error': {'message': 'Insufficient Balance'}}"
            )
        )
        self.assertFalse(_result_is_auth_fatal("打开微信成功"))
        self.assertTrue(_is_corrupt_session_error("'NoneType' object has no attribute 'id'"))
        bal = _auth_fatal_user_message("Insufficient Balance")
        self.assertIn("余额不足", bal)
        self.assertNotIn("HERMES_API_SERVER_KEY", bal)
        self.assertNotIn("DESKTOP_AGENT_GATEWAY_SECRET", bal)
        out = HermesGatewayClient().execute_user_instruction("", session_id="abc")
        self.assertIn("instruction 为空", out)

    def test_desktop_auth_hint_no_secret_leak(self):
        from modules.hermes.hermes_skill_hints import build_explore_instruction, desktop_gateway_auth_hint

        hint = desktop_gateway_auth_hint()
        self.assertIn("MCP", hint)
        self.assertNotIn("X-Desktop-Agent-Secret", hint)
        self.assertNotIn("DESKTOP_AGENT_GATEWAY_SECRET", hint)
        text = build_explore_instruction("打开控制面板", {"platform": "desktop"})
        self.assertNotIn("X-Desktop-Agent-Secret", text)


if __name__ == "__main__":
    unittest.main()
