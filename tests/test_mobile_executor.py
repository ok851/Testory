# -*- coding: utf-8 -*-
"""MobileExecutor 单元测试（mock Appium driver）。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMobileAutomation(unittest.TestCase):
    def test_normalize_mobile_action_aliases(self):
        from modules.mobile.mobile_automation import normalize_mobile_action

        self.assertEqual(normalize_mobile_action("click"), "tap")
        self.assertEqual(normalize_mobile_action("input"), "input_text")

    def test_normalize_strategy_default(self):
        from modules.mobile.mobile_automation import normalize_strategy

        self.assertEqual(normalize_strategy({}), "accessibility_id")
        self.assertEqual(normalize_strategy({"selector_type": "id"}), "id")

    def test_validate_step_for_mobile(self):
        from modules.mobile.mobile_automation import validate_step_for_mobile

        self.assertIsNone(validate_step_for_mobile("tap"))
        self.assertIn("不支持", validate_step_for_mobile("navigate") or "")


class TestMobileExecutorMocked(unittest.TestCase):
    @patch.dict(os.environ, {"ENABLE_MOBILE": "1"}, clear=False)
    @patch("mobile_executor.mobile_runtime_available", return_value=True)
    @patch("mobile_executor.check_appium_server", return_value=(True, "ok"))
    def test_execute_tap_step(self, _avail, _appium_ok):
        from modules.mobile.mobile_executor import MobileExecutor

        mock_el = MagicMock()
        mock_driver = MagicMock()
        mock_driver.find_element = MagicMock(return_value=mock_el)

        executor = MobileExecutor()
        executor._driver = mock_driver

        with patch.object(executor, "_find_element", return_value=mock_el):
            result = executor.execute_step({
                "action": "tap",
                "automation_layer": "android",
                "selector_value": "login_btn",
                "strategy": "accessibility_id",
                "description": "点击登录",
            })

        self.assertEqual(result.get("status"), "success")
        mock_el.click.assert_called_once()

    @patch.dict(os.environ, {"ENABLE_MOBILE": "0"}, clear=False)
    def test_mobile_disabled(self):
        from modules.mobile.mobile_env_config import mobile_enabled

        self.assertFalse(mobile_enabled())


class TestStepExecutorRouting(unittest.TestCase):
    def test_case_steps_include_android(self):
        from modules.execution.step_executor import case_steps_include_android, case_steps_include_web

        steps = [{"automation_layer": "android", "action": "tap"}]
        self.assertTrue(case_steps_include_android(steps))
        self.assertFalse(case_steps_include_web(steps))

    def test_mixed_web_android_rejected(self):
        from modules.execution.step_executor import ensure_mixed_run_environment

        steps = [
            {"automation_layer": "web", "action": "click"},
            {"automation_layer": "android", "action": "tap"},
        ]
        msg = ensure_mixed_run_environment(steps)
        self.assertIsNotNone(msg)
        self.assertIn("混排", msg or "")


if __name__ == "__main__":
    unittest.main()
