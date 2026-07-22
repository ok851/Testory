# -*- coding: utf-8 -*-
"""失败执行不得弹出可保存用例；create-from-plan 不含 created_by。"""
from __future__ import annotations

import inspect
import json
import unittest

from database import Database


class TestStripInventedCaseJson(unittest.TestCase):
    def test_strips_fenced_json(self):
        from ai_chat_tool_loop import _strip_invented_case_json

        text = (
            "Hermes 代理未返回有效轨迹。\n\n"
            "```json\n"
            '{"case_name":"x","steps":[{"action":"launch_app"}]}\n'
            "```\n"
            "请检查 Gateway。"
        )
        out = _strip_invented_case_json(text)
        self.assertNotIn("launch_app", out)
        self.assertIn("Gateway", out)


class TestCreateTestCaseV2Signature(unittest.TestCase):
    def test_no_created_by_param(self):
        sig = inspect.signature(Database.create_test_case_v2)
        self.assertNotIn("created_by", sig.parameters)

    def test_accepts_generated_by_ai_and_platform(self):
        sig = inspect.signature(Database.create_test_case_v2)
        self.assertIn("generated_by_ai", sig.parameters)
        self.assertIn("platform", sig.parameters)


class TestStreamEmptyMarksFailed(unittest.TestCase):
    def test_empty_no_tools_sets_failed_flags(self):
        from ai_chat_tool_loop import _result_is_stream_empty

        text = json.dumps(
            {
                "ok": False,
                "stream_empty_text": True,
                "had_tool_activity": False,
                "error": "空流",
            },
            ensure_ascii=False,
        )
        self.assertTrue(_result_is_stream_empty(text))


if __name__ == "__main__":
    unittest.main()
