# -*- coding: utf-8 -*-
import unittest

from modules.desktop.desktop_automation import normalize_automation_layer, validate_step_for_layer
from modules.execution.step_executor import case_steps_include_desktop, enrich_execution_step, is_desktop_step


class TestDesktopStepRouter(unittest.TestCase):
    def test_normalize_layer_default_web(self):
        self.assertEqual(normalize_automation_layer({}), "web")
        self.assertEqual(normalize_automation_layer({"automation_layer": "desktop"}), "desktop")

    def test_normalize_desktop_only_action_overrides_web_layer(self):
        step = {"automation_layer": "web", "action": "launch_app"}
        self.assertEqual(normalize_automation_layer(step), "desktop")
        self.assertTrue(is_desktop_step(step))

    def test_normalize_visual_overrides_web_layer(self):
        step = {"automation_layer": "web", "action": "click", "selector_type": "visual"}
        self.assertEqual(normalize_automation_layer(step), "desktop")
        self.assertTrue(is_desktop_step(step))

    def test_is_desktop_step(self):
        self.assertFalse(is_desktop_step({"automation_layer": "web"}))
        self.assertTrue(is_desktop_step({"automation_layer": "desktop", "action": "click"}))

    def test_case_steps_include_desktop(self):
        steps = [{"automation_layer": "web"}, {"automation_layer": "desktop"}]
        self.assertTrue(case_steps_include_desktop(steps))
        self.assertFalse(case_steps_include_desktop([{"automation_layer": "web"}]))

    def test_validate_desktop_launch(self):
        self.assertIsNone(validate_step_for_layer("launch_app", "desktop"))
        self.assertIsNotNone(validate_step_for_layer("navigate", "desktop"))

    def test_validate_web_no_launch(self):
        self.assertIsNotNone(validate_step_for_layer("launch_app", "web"))
        self.assertIsNone(validate_step_for_layer("navigate", "web"))

    def test_enrich_execution_step(self):
        s = enrich_execution_step({
            "action": "click",
            "automation_layer": "desktop",
            "desktop_spec": '{"path":"C:\\\\a.exe"}',
        })
        self.assertEqual(s["automation_layer"], "desktop")
        self.assertIsInstance(s["desktop_spec"], dict)


if __name__ == "__main__":
    unittest.main()
