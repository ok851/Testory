# -*- coding: utf-8 -*-
"""浏览器路由强制 Hermes + 实时用例平台步骤白名单。"""

from __future__ import annotations

import unittest


class TestCaseWorthyFilter(unittest.TestCase):
    def test_web_excludes_launch_app(self):
        from modules.ai.ai_action_recorder import is_case_worthy_for_platform

        self.assertFalse(is_case_worthy_for_platform("launch_app", "web"))
        self.assertFalse(is_case_worthy_for_platform("windows_launch_app", "web"))
        self.assertFalse(is_case_worthy_for_platform("focus_app", "web"))

    def test_web_keeps_navigate_click_otp(self):
        from modules.ai.ai_action_recorder import is_case_worthy_for_platform

        self.assertTrue(is_case_worthy_for_platform("navigate", "web"))
        self.assertTrue(is_case_worthy_for_platform("click", "web"))
        self.assertTrue(is_case_worthy_for_platform("type", "web"))
        self.assertTrue(is_case_worthy_for_platform("extract_otp", "web"))
        self.assertTrue(is_case_worthy_for_platform("browser_click", "web"))

    def test_desktop_keeps_launch(self):
        from modules.ai.ai_action_recorder import is_case_worthy_for_platform

        self.assertTrue(is_case_worthy_for_platform("launch_app", "desktop"))
        self.assertTrue(is_case_worthy_for_platform("windows_type_text", "desktop"))


class TestWebToolMounting(unittest.TestCase):
    def test_build_tools_web_url_no_windows(self):
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas

        schemas = chat_tool_schemas(
            allow_hermes=True,
            platform_type="web",
            message="打开 https://example.com 登录并取验证码",
            allow_desktop_windows_tools=False,
            connected_hands={"desktop": True, "phone": True, "browser": True},
            allow_refine_test_plan=False,
        )
        names = [((s.get("function") or {}).get("name") or "") for s in schemas]
        self.assertIn("hermes_execute", names)
        self.assertNotIn("windows_launch_app", names)
        self.assertNotIn("windows_click_element", names)


class TestBrowserRouteIntent(unittest.TestCase):
    def test_url_login_otp_is_web_browser(self):
        from modules.ai.agent_intent import resolve_task_route, message_needs_browser

        msg = (
            "进入这个地址https://qwsyjc.sztobacco.cn/.输入账号:sztest2密码:Hu123456789"
            "手机号:16608943238，点击获取验证码，从移动端设备中读取验证码然后填写进验证码中，然后点击登录"
        )
        self.assertTrue(message_needs_browser(msg))
        route = resolve_task_route(msg, ui_platform="auto")
        self.assertTrue(route.needs_browser)
        self.assertEqual(route.platform, "web")
        self.assertFalse(route.needs_desktop_tools)


if __name__ == "__main__":
    unittest.main()
