# -*- coding: utf-8 -*-
import unittest

from desktop_input import (
    client_point_in_hwnd,
    infer_effect_keyword,
    is_desktop_shell_hwnd,
    is_valid_hwnd,
    screen_coords_in_virtual_bounds,
    should_verify_desktop_effect,
    verify_client_message_delivered,
    verify_pointer_delivered,
    virtual_screen_rect,
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

    def test_message_mode_requires_target_hwnd_match(self):
        self.assertFalse(
            verify_pointer_delivered(
                0,
                0,
                desktop_shell=False,
                target_hwnd=999999,
            )
        )

    def test_client_delivery_mode_skips_window_from_point(self):
        import ctypes

        hwnd = int(ctypes.windll.user32.GetDesktopWindow() or 0)
        if not is_valid_hwnd(hwnd):
            return
        self.assertTrue(
            verify_pointer_delivered(
                0,
                0,
                target_hwnd=hwnd,
                client_x=10,
                client_y=10,
                delivery_mode="client",
            )
        )

    def test_virtual_screen_rect_positive_size(self):
        left, top, w, h = virtual_screen_rect()
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        self.assertTrue(screen_coords_in_virtual_bounds(left + 1, top + 1))

    def test_desktop_shell_hwnd_rejects_zero(self):
        self.assertFalse(is_desktop_shell_hwnd(0))


if __name__ == "__main__":
    unittest.main()
