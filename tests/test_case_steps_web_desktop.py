# -*- coding: utf-8 -*-
import unittest

from modules.execution.step_executor import case_steps_include_desktop, case_steps_include_web


class TestCaseStepsLayer(unittest.TestCase):
    def test_pure_desktop(self):
        steps = [{"automation_layer": "desktop", "action": "launch_app"}]
        self.assertTrue(case_steps_include_desktop(steps))
        self.assertFalse(case_steps_include_web(steps))

    def test_mixed(self):
        steps = [
            {"automation_layer": "desktop"},
            {"automation_layer": "web"},
        ]
        self.assertTrue(case_steps_include_desktop(steps))
        self.assertTrue(case_steps_include_web(steps))


if __name__ == "__main__":
    unittest.main()
