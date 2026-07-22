# -*- coding: utf-8 -*-
"""意图粗判：发消息正文含「你好」不得误判为闲聊。"""
from __future__ import annotations

import unittest


class TestMessageNeedsAutomation(unittest.TestCase):
    def test_wechat_send_nl_is_automation(self):
        from agent_intent import message_needs_automation

        msg = "用微信给舒琪宝宝大王发：你好，我是AI"
        self.assertTrue(message_needs_automation(msg), msg)

    def test_wechat_nl_enables_tools_even_when_platform_web(self):
        from ai_chat_tool_loop import _should_enable_desktop_windows_tools

        msg = "用微信给舒琪宝宝大王发：我是胡哥的AI助手"
        self.assertTrue(_should_enable_desktop_windows_tools("web", msg))
        self.assertTrue(_should_enable_desktop_windows_tools("auto", msg))

    def test_structured_search_is_automation(self):
        from agent_intent import message_needs_automation

        msg = "聚焦微信 -> 点搜索 -> 输入「舒琪宝宝大王」"
        self.assertTrue(message_needs_automation(msg))

    def test_pure_greeting_is_chat(self):
        from agent_intent import message_needs_automation

        self.assertFalse(message_needs_automation("你好"))
        self.assertFalse(message_needs_automation("谢谢"))

    def test_capability_question_is_chat(self):
        from agent_intent import message_needs_automation

        self.assertFalse(message_needs_automation("你有什么能力"))


if __name__ == "__main__":
    unittest.main()
