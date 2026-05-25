# -*- coding: utf-8 -*-
import unittest

from step_executor import (
    DESKTOP_POINTER_ACTIONS,
    validate_desktop_step_result,
)


class TestStepExecutorVerify(unittest.TestCase):
    def test_pointer_requires_verified(self):
        with self.assertRaises(RuntimeError):
            validate_desktop_step_result(
                {"status": "success", "verified": False, "pointer_executed": True},
                "click",
            )

    def test_non_dict_raises(self):
        with self.assertRaises(RuntimeError):
            validate_desktop_step_result(None, "wait")

    def test_wait_skips_pointer_gate(self):
        out = validate_desktop_step_result({"status": "success"}, "wait")
        self.assertEqual(out["status"], "success")

    def test_pointer_actions_set(self):
        self.assertIn("double_click", DESKTOP_POINTER_ACTIONS)


if __name__ == "__main__":
    unittest.main()
