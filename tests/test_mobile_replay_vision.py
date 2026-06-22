# -*- coding: utf-8 -*-
"""移动端 replay 视觉步骤与 Playground 保存测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMobileReplayVision(unittest.TestCase):
    @patch("mobile_vision_tap.tap_mobile_by_description")
    @patch("mobile_automation_gateway.replay.plugin_rpc.take_screenshot")
    @patch("mobile_automation_gateway.replay._sleep_after_action")
    def test_ai_tap_step(self, _sleep, mock_shot, mock_tap):
        from mobile_automation_gateway import replay as replay_mod

        mock_tap.return_value = (True, "已点击")
        mock_shot.return_value = (b"jpg", {})

        out = replay_mod.execute_step("dev1", {
            "action": "ai_tap",
            "description": "登录按钮",
            "locate_prompt": "登录按钮",
        })
        self.assertEqual(out["status"], "success")
        mock_tap.assert_called_once_with("dev1", "登录按钮")

    @patch("mobile_automation_gateway.replay._capture_device_png")
    def test_assert_vision_fail(self, mock_cap):
        from mobile_automation_gateway import replay as replay_mod

        mock_cap.return_value = (b"png", "", 1080, 1920)
        with patch("ai_vision_insight.assert_vision_condition_on_png", return_value=(False, "未看到登录成功")):
                out = replay_mod.execute_step("dev1", {
                    "action": "assert_vision",
                    "description": "页面显示登录成功",
                })
        self.assertEqual(out["status"], "error")
        self.assertIn("未看到", out.get("error", ""))


class TestReplayMetaToSteps(unittest.TestCase):
    def test_convert_tap_and_assert(self):
        from mobile_playground import replay_meta_to_test_steps

        steps = replay_meta_to_test_steps([
            {"status": "success", "action": "ai_tap", "label": "登录按钮"},
            {"status": "success", "action": "assert_vision", "label": "显示首页"},
            {"status": "error", "action": "ai_tap", "label": "失败步"},
        ])
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["action"], "ai_tap")
        self.assertEqual(steps[1]["action"], "assert_vision")

    def test_convert_act_input(self):
        from mobile_playground import replay_meta_to_test_steps

        steps = replay_meta_to_test_steps([
            {"status": "success", "action": "input", "label": "输入「Midscene」到 搜索框"},
        ])
        self.assertEqual(steps[0]["action"], "ai_input")
        self.assertEqual(steps[0]["input_value"], "Midscene")


class TestPlaygroundSave(unittest.TestCase):
    def test_save_replay_to_case(self):
        from mobile_playground import playground_save_replay_to_case

        with tempfile.TemporaryDirectory() as tmp:
            run_id = "testrun001"
            run_dir = os.path.join(tmp, run_id)
            os.makedirs(run_dir)
            meta = {
                "run_id": run_id,
                "platform": "android",
                "steps": [
                    {"status": "success", "action": "ai_tap", "label": "设置"},
                ],
            }
            with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f)

            mock_db = MagicMock()
            mock_db.get_test_case_v2.return_value = {"project_id": 1, "unit_id": 9}
            mock_db.check_project_access.return_value = True
            mock_db.create_test_step.return_value = 101

            with patch("vision_step_report.replay_run_dir") as mock_root:
                from pathlib import Path

                mock_root.return_value = Path(run_dir)
                with patch("database.Database", return_value=mock_db):
                    out = playground_save_replay_to_case(run_id, 5, user_id=1)
            self.assertTrue(out["success"])
            self.assertEqual(out["unit_id"], 9)
            self.assertEqual(out["step_count"], 1)
            mock_db.create_test_step.assert_called_once()


if __name__ == "__main__":
    unittest.main()
