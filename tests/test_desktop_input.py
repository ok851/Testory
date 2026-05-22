# -*- coding: utf-8 -*-
import unittest

from desktop_input import infer_effect_keyword, should_verify_desktop_effect


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

    def test_shell_verify_off_by_default(self):
        self.assertFalse(
            should_verify_desktop_effect({"surface": "desktop_shell"})
        )
        self.assertTrue(
            should_verify_desktop_effect(
                {"surface": "desktop_shell", "verify_effect": True}
            )
        )


if __name__ == "__main__":
    unittest.main()
