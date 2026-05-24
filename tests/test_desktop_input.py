# -*- coding: utf-8 -*-
import unittest

from desktop_input import (
    infer_effect_keyword,
    is_desktop_shell_hwnd,
    should_verify_desktop_effect,
    verify_pointer_delivered,
)


class TestDesktopInput(unittest.TestCase):
    def test_infer_from_uia_path(self):
        spec = {
            "uia_path": [
                {"name": "桌面"},
                {"name": "控制面板"},
            ]
        }
        self.assertEqual(
            infer_effect_keyword(spec, ""),
            "控制面板",
        )

    def test_infer_from_description(self):
        self.assertEqual(
            infer_effect_keyword({}, "录制：双击（自动）「控制面板」"),
            "控制面板",
        )

    def test_verify_on_by_default(self):
        self.assertTrue(
            should_verify_desktop_effect({"surface": "desktop_shell"})
        )
        self.assertFalse(
            should_verify_desktop_effect(
                {"surface": "desktop_shell", "verify_effect": False}
            )
        )

    def test_physical_click_non_shell(self):
        self.assertTrue(
            verify_pointer_delivered(0, 0, desktop_shell=False, physical=True)
        )

    def test_desktop_shell_hwnd_rejects_zero(self):
        self.assertFalse(is_desktop_shell_hwnd(0))


if __name__ == "__main__":
    unittest.main()
