# -*- coding: utf-8 -*-
"""Abort / tool-loop 文案不得误报「用户取消」。"""
from __future__ import annotations

import threading
import unittest


class TestAbortReasonMessages(unittest.TestCase):
    def test_timeout_not_user_cancel(self):
        from ai_chat_tool_loop import _abort_user_message

        ev = threading.Event()
        setattr(ev, "_timed_out", True)
        ev.set()
        msg = _abort_user_message(ev, None)
        self.assertIn("超时", msg)
        self.assertNotIn("用户取消", msg)

    def test_tool_loop_not_user_cancel(self):
        from ai_chat_tool_loop import _abort_user_message

        ev = threading.Event()
        setattr(ev, "_abort_reason", "tool_loop")
        ev.set()
        msg = _abort_user_message(ev, None)
        self.assertNotIn("操作已被用户取消", msg)
        self.assertTrue("中止" in msg or "死循环" in msg or "重复" in msg)

    def test_web_prompt_forbids_skill_tools(self):
        from ai_chat_tool_loop import _web_hermes_system_prompt
        from hermes_skill_hints import build_explore_instruction

        sp = _web_hermes_system_prompt()
        self.assertIn("禁止", sp)
        self.assertIn("skill_view", sp)
        self.assertIn("DOM", sp)
        self.assertIn("browser_navigate", sp)
        text = build_explore_instruction(
            "打开 https://example.com",
            {"platform": "web", "already_on_target_page": True, "start_url": "https://example.com"},
        )
        self.assertNotIn("请先用 skill_view", text)
        self.assertNotIn("参考技能", text)
        self.assertIn("DOM", text)

    def test_gateway_abort_helper(self):
        from hermes_gateway_client import _abort_error_message

        ev = threading.Event()
        setattr(ev, "_abort_reason", "tool_loop")
        ev.set()
        self.assertNotIn("操作已被用户取消", _abort_error_message(ev))
        self.assertIn("死循环", _abort_error_message(ev))


if __name__ == "__main__":
    unittest.main()
