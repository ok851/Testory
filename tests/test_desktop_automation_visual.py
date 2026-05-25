# -*- coding: utf-8
"""desktop_automation 视觉单路径测试。"""

import unittest
from unittest.mock import MagicMock, patch


class TestDesktopAutomationVisual(unittest.TestCase):
    @patch("desktop_automation.sendinput_pointer_at_screen")
    @patch("desktop_automation.resolve_visual_click_point", return_value=(100, 200, 0.92))
    @patch("desktop_automation.desktop_runtime_available", return_value=True)
    def test_perform_visual_click(self, _rt, _resolve, sendinput):
        from desktop_automation import DesktopAutomation

        auto = DesktopAutomation()
        step = {
            "action": "click",
            "automation_layer": "desktop",
            "selector_type": "visual",
            "selector_value": '{"template_image_base64":"abc","click_offset":{"x":1,"y":2},"match_threshold":0.75,"match_method":"orb","template_size":{"w":10,"h":10}}',
        }
        out = auto.execute_step(step)
        self.assertEqual(out["status"], "success")
        self.assertTrue(out.get("pointer_executed"))
        sendinput.assert_called_once()

    @patch("desktop_automation.sendinput_pointer_at_screen")
    @patch(
        "desktop_automation.resolve_visual_click_point",
        side_effect=__import__(
            "desktop_visual_engine", fromlist=["VisualMatchFailed"]
        ).VisualMatchFailed("score too low"),
    )
    @patch(
        "desktop_automation.build_visual_failure_artifact_png",
        return_value=b"fake-png",
    )
    @patch("desktop_automation.desktop_runtime_available", return_value=True)
    def test_visual_match_failure_saves_roi_artifact(
        self, _rt, _artifact, _resolve, _sendinput
    ):
        from desktop_automation import DesktopAutomation
        from desktop_visual_engine import VisualMatchFailed

        auto = DesktopAutomation()
        step = {
            "action": "click",
            "automation_layer": "desktop",
            "selector_type": "visual",
            "selector_value": '{"template_image_base64":"abc","click_offset":{"x":1,"y":2},"match_threshold":0.75,"match_method":"orb","template_size":{"w":10,"h":10}}',
        }
        with self.assertRaises(VisualMatchFailed) as ctx:
            auto.execute_step(step)
        self.assertTrue(ctx.exception.failure_screenshot)
        self.assertIn("/static/desktop_screenshots/", ctx.exception.failure_screenshot)

    @patch("desktop_automation.desktop_runtime_available", return_value=True)
    def test_legacy_step_blocked(self, _rt):
        from desktop_automation import DesktopAutomation

        auto = DesktopAutomation()
        step = {
            "action": "click",
            "automation_layer": "desktop",
            "selector_type": "name",
            "selector_value": "FolderView",
        }
        with self.assertRaises(RuntimeError):
            auto.execute_step(step)


if __name__ == "__main__":
    unittest.main()
