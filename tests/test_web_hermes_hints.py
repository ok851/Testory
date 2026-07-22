# -*- coding: utf-8 -*-
"""Web Hermes 指令：DOM 优先，禁止重复 navigate / skill_view。"""
from __future__ import annotations

import unittest


class TestWebHermesHints(unittest.TestCase):
    def test_web_forbids_skill_view(self):
        from hermes_skill_hints import build_explore_instruction

        text = build_explore_instruction(
            "打开 https://example.com 登录并搜索",
            {"platform": "web", "already_on_target_page": True, "start_url": "https://example.com"},
        )
        self.assertIn("skill_*", text)
        self.assertNotIn("请先用 skill_view", text)
        self.assertIn("DOM", text)
        self.assertIn("禁止 browser_navigate", text)
        self.assertNotIn("优先调用已注册的 MCP windows_", text)

    def test_web_skills_exclude_desktop(self):
        from hermes_skill_hints import build_explore_instruction

        text = build_explore_instruction(
            "测网页",
            {
                "platform": "web",
                "skills": ["testory-web-browser", "testory-windows-desktop"],
            },
        )
        self.assertNotIn("testory-windows-desktop", text)
        self.assertIn("browser_*", text)

    def test_desktop_still_allows_one_skill_view(self):
        from hermes_skill_hints import build_explore_instruction

        text = build_explore_instruction("打开记事本", {"platform": "desktop"})
        self.assertIn("windows_", text)
        self.assertIn("skill_view", text)

    def test_web_surface_prefix_no_desktop_session(self):
        from agent_task_context import TaskContext

        ctx = TaskContext(session_id="t1", active_surface="web")
        p = ctx.instruction_prefix()
        self.assertIn("网页", p)
        self.assertNotIn("desktop_session_id=", p)
        self.assertIn("DOM", p)


if __name__ == "__main__":
    unittest.main()
