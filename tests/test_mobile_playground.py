# -*- coding: utf-8 -*-
"""移动端 Playground（Tap / Assert / Query / Act）单元测试。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision_action_port import ActResult


class TestPlaygroundTap(unittest.TestCase):
    @patch("mobile_playground._record_playground_step", return_value=None)
    @patch("mobile_playground.time.sleep")
    @patch("mobile_playground.MobileVisionActionPort")
    def test_tap_success(self, mock_port_cls, _sleep, _replay):
        port = MagicMock()
        port.capture.return_value = MagicMock(png_bytes=b"png")
        port.tap.return_value = ActResult(ok=True, message="已点击登录")
        mock_port_cls.return_value = port

        from mobile_playground import playground_tap

        out = playground_tap("emulator-5554", "登录按钮")
        self.assertTrue(out["success"])
        self.assertIn("点击", out["message"])
        port.tap.assert_called_once_with("登录按钮")

    def test_tap_empty_locate(self):
        from mobile_playground import playground_tap

        out = playground_tap("emulator-5554", "  ")
        self.assertFalse(out["success"])
        self.assertIn("描述", out["error"])


class TestPlaygroundAssert(unittest.TestCase):
    @patch("mobile_playground._record_playground_step", return_value=None)
    @patch("mobile_playground.MobileVisionActionPort")
    def test_assert_pass(self, mock_port_cls, _replay):
        port = MagicMock(unsafe=True)
        port.capture.return_value = MagicMock(png_bytes=b"png")
        port.assert_vision.return_value = ActResult(ok=True, message="页面显示登录成功")
        mock_port_cls.return_value = port

        from mobile_playground import playground_assert

        out = playground_assert("dev1", "页面显示登录成功")
        self.assertTrue(out["success"])
        self.assertTrue(out["passed"])


class TestPlaygroundQuery(unittest.TestCase):
    @patch("mobile_playground._record_playground_step", return_value=None)
    @patch("mobile_playground.MobileVisionActionPort")
    def test_query_success(self, mock_port_cls, _replay):
        port = MagicMock()
        port.capture.return_value = MagicMock(png_bytes=b"png")
        port.query.return_value = ("user-42", "")
        mock_port_cls.return_value = port

        from mobile_playground import playground_query

        out = playground_query("dev1", "当前用户 ID")
        self.assertTrue(out["success"])
        self.assertEqual(out["data"], "user-42")


class TestPlaygroundAct(unittest.TestCase):
    @patch("mobile_playground._wait_after_action_ms", return_value=0)
    @patch("mobile_playground._plan_next_act_step")
    @patch("mobile_playground.MobileVisionActionPort")
    def test_act_done_on_first_plan(self, mock_port_cls, mock_plan, _wait):
        port = MagicMock()
        port.capture.return_value = MagicMock(png_bytes=b"png")
        mock_port_cls.return_value = port
        mock_plan.return_value = ({"action": "done", "summary": "已打开设置页"}, "")

        with patch("mobile_playground.vision_replay_enabled", create=True):
            with patch("mobile_playground.VisionReplaySession", create=True):
                from mobile_playground import playground_act

                with patch("vision_step_report.vision_replay_enabled", return_value=False):
                    out = playground_act("dev1", "打开设置")
        self.assertTrue(out["success"])
        self.assertEqual(out["message"], "已打开设置页")
        self.assertEqual(len(out["steps"]), 1)

    @patch("mobile_playground._wait_after_action_ms", return_value=0)
    @patch("mobile_playground._plan_next_act_step")
    @patch("mobile_playground.MobileVisionActionPort")
    def test_act_tap_then_done(self, mock_port_cls, mock_plan, _wait):
        port = MagicMock()
        port.capture.return_value = MagicMock(png_bytes=b"png")
        port.tap.return_value = ActResult(ok=True, message="ok")
        mock_port_cls.return_value = port
        mock_plan.side_effect = [
            ({"action": "tap", "target": "搜索框"}, ""),
            ({"action": "done", "summary": "完成"}, ""),
        ]

        from mobile_playground import playground_act

        with patch("vision_step_report.vision_replay_enabled", return_value=False):
            out = playground_act("dev1", "搜索 Midscene")
        self.assertTrue(out["success"])
        port.tap.assert_called_once_with("搜索框")
        self.assertEqual(len(out["steps"]), 2)


class TestParseActPlan(unittest.TestCase):
    def test_parse_json_tap(self):
        from mobile_playground import _parse_act_plan

        plan = _parse_act_plan('说明文字 {"action":"tap","target":"按钮"}')
        self.assertEqual(plan["action"], "tap")
        self.assertEqual(plan["target"], "按钮")

    def test_parse_done_chinese(self):
        from mobile_playground import _parse_act_plan

        plan = _parse_act_plan("任务已完成，无需继续")
        self.assertEqual(plan["action"], "done")


if __name__ == "__main__":
    unittest.main()
