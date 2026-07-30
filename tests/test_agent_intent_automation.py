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

    def test_ui_desktop_enables_tools_without_keywords(self):
        from agent_intent import resolve_task_route

        r = resolve_task_route("帮我操作一下那个软件", ui_platform="desktop")
        self.assertEqual(r.mode, "automation")
        self.assertEqual(r.platform, "desktop")
        self.assertTrue(r.needs_desktop_tools)

    def test_flow_profile_generic_vs_im(self):
        from ai_chat_tool_loop import _resolve_desktop_flow_profile

        self.assertEqual(
            _resolve_desktop_flow_profile("打开记事本写一段字"),
            "generic",
        )
        self.assertEqual(
            _resolve_desktop_flow_profile("用微信给张三发消息你好"),
            "im_search",
        )

    def test_cross_end_login_otp_example_route(self):
        from agent_intent import resolve_task_route

        msg = "帮我登录微信，手机号为13800138000，自动从移动端获取验证码并填写登录"
        r = resolve_task_route(msg, ui_platform="auto")
        self.assertTrue(r.needs_automation)
        self.assertTrue(r.needs_desktop_tools)
        self.assertTrue(r.needs_mobile_await)
        self.assertEqual(r.platform, "desktop")
        self.assertEqual(r.reason, "cross_end_capabilities")

    def test_otp_only_and_register_variants(self):
        from agent_intent import resolve_task_route

        only = resolve_task_route("请从手机通知里取验证码", ui_platform="auto")
        self.assertTrue(only.needs_mobile_await)
        self.assertTrue(only.needs_automation)

        reg = resolve_task_route("帮我在桌面注册账号，用短信验证码回填", ui_platform="auto")
        self.assertTrue(reg.needs_desktop_tools)
        self.assertTrue(reg.needs_mobile_await)
        self.assertEqual(reg.reason, "cross_end_capabilities")

    def test_seed_vars_and_flexible_prompt(self):
        from agent_intent import extract_cross_end_seed_vars
        from ai_chat_tool_loop import _build_system_prompt, _cross_end_strategy_lines

        seeds = extract_cross_end_seed_vars(
            "帮我登录飞书，手机号为13912345678，自动取验证码"
        )
        self.assertEqual(seeds.get("phone_number"), "13912345678")
        self.assertEqual(seeds.get("app_name"), "飞书")

        lines = "\n".join(_cross_end_strategy_lines())
        self.assertIn("能力面", lines)
        self.assertIn("mobile_extract_otp", lines)
        self.assertNotIn("必须先 launch 再 input 手机号", lines)

        prompt = _build_system_prompt(
            project_name="t",
            current_plan={},
            page_snapshot="",
            dom_pack="",
            memory_context="",
            interaction_note="",
            test_scope="",
            platform_type="desktop",
        )
        self.assertIn("跨端工具原则", prompt)
        self.assertNotIn("必须先 launch 再 input 手机号 再 click 发码", prompt)


if __name__ == "__main__":
    unittest.main()
