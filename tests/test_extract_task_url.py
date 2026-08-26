# -*- coding: utf-8 -*-
"""从用户任务原文解析浏览器起始 URL（无独立 URL 输入框）。"""
from __future__ import annotations

import unittest


class TestExtractTaskUrl(unittest.TestCase):
    def test_https_in_sentence(self):
        from modules.ai.agent_intent import extract_task_url

        u = extract_task_url("打开 https://demo.example.com/login ，用 admin 登录")
        self.assertTrue(u.startswith("https://demo.example.com/login"))

    def test_bare_host_after_open(self):
        from modules.ai.agent_intent import extract_task_url

        self.assertEqual(
            extract_task_url("访问 demo.example.com/orders 并搜索", allow_seed=False),
            "http://demo.example.com/orders",
        )

    def test_localhost(self):
        from modules.ai.agent_intent import extract_task_url

        self.assertEqual(
            extract_task_url("打开 localhost:8080/admin", allow_seed=False),
            "http://localhost:8080/admin",
        )

    def test_baidu_seed(self):
        from modules.ai.agent_intent import extract_task_url

        self.assertIn("baidu.com", extract_task_url("在百度搜索自动化测试"))

    def test_hermes_prefers_message_url(self):
        from modules.ai.ai_chat_tool_loop import ChatToolLoopParams, _resolve_start_url_for_hermes

        params = ChatToolLoopParams(
            message="打开 https://shop.example.com/orders 并搜索商品",
            project_name="p",
            current_plan={"case_url": ""},
            history=[],
            profile=None,
            legacy_model="",
            page_snapshot=None,
            probe_registry=None,
            probe_url="https://stale.example.com/",
            memory_context=None,
            dom_context_pack=None,
            interaction_context={},
        )
        self.assertIn("shop.example.com", _resolve_start_url_for_hermes(params, {}))


if __name__ == "__main__":
    unittest.main()
