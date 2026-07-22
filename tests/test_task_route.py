# -*- coding: utf-8 -*-
"""统一任务路由：web / desktop / android / chat 不得互串。"""
from __future__ import annotations

import unittest


class TestResolveTaskRoute(unittest.TestCase):
    def test_wechat_send_is_desktop_even_if_ui_web(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route(
            "用微信给舒琪宝宝大王发：我是胡哥的AI助手",
            ui_platform="web",
        )
        self.assertEqual(r.mode, "automation")
        self.assertEqual(r.platform, "desktop")
        self.assertTrue(r.needs_desktop_tools)
        self.assertFalse(r.needs_browser)

    def test_notepad_is_desktop(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route("打开记事本输入 hello", ui_platform="auto")
        self.assertEqual(r.platform, "desktop")
        self.assertTrue(r.needs_desktop_tools)

    def test_url_is_web(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route("打开 https://example.com 登录", ui_platform="auto")
        self.assertEqual(r.platform, "web")
        self.assertTrue(r.needs_browser)
        self.assertFalse(r.needs_desktop_tools)

    def test_web_words_keep_web(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route("在浏览器里打开百度搜索天气", ui_platform="auto")
        self.assertEqual(r.platform, "web")
        self.assertTrue(r.needs_browser)

    def test_url_beats_desktop_ui(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route(
            "访问 https://shop.example.com 下单",
            ui_platform="desktop",
        )
        self.assertEqual(r.platform, "web")
        self.assertTrue(r.needs_browser)

    def test_desktop_overrides_wrong_web_ui(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route("打开控制面板", ui_platform="web")
        self.assertEqual(r.platform, "desktop")

    def test_greeting_is_chat(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route("你好", ui_platform="auto")
        self.assertEqual(r.mode, "chat")
        self.assertFalse(r.needs_automation)

    def test_capability_question_is_chat(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route("你有什么能力", ui_platform="web")
        self.assertEqual(r.mode, "chat")

    def test_android_signals(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route("用 adb 打开安卓真机上的设置", ui_platform="auto")
        self.assertEqual(r.platform, "android")

    def test_message_needs_automation_compat(self):
        from agent_intent import message_needs_automation, message_needs_browser

        self.assertTrue(message_needs_automation("用微信给张三发：你好"))
        self.assertFalse(message_needs_browser("用微信给张三发：你好"))
        self.assertTrue(message_needs_browser("打开 https://a.com"))


class TestDesktopToolsFollowRoute(unittest.TestCase):
    def test_web_ui_wechat_still_gets_windows_tools(self):
        from ai_chat_tool_loop import _should_enable_desktop_windows_tools

        self.assertTrue(
            _should_enable_desktop_windows_tools(
                "web", "用微信给舒琪宝宝大王发：你好"
            )
        )

    def test_web_url_does_not_get_windows_tools(self):
        from ai_chat_tool_loop import _should_enable_desktop_windows_tools

        self.assertFalse(
            _should_enable_desktop_windows_tools(
                "web", "打开 https://example.com 点击登录"
            )
        )


if __name__ == "__main__":
    unittest.main()
