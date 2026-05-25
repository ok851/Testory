# -*- coding: utf-8 -*-
import unittest

from desktop_input import (
    infer_effect_keyword,
    screen_coords_in_virtual_bounds,
    verify_pointer_delivered,
    virtual_screen_rect,
)


class TestDesktopInput(unittest.TestCase):
    def test_infer_from_description(self):
        self.assertEqual(
            infer_effect_keyword({}, "录制：双击（自动）「控制面板」"),
            "控制面板",
        )

    def test_verify_sendinput_always_ok(self):
        self.assertTrue(
            verify_pointer_delivered(100, 200, used_physical_click=True)
        )

    def test_virtual_screen_rect(self):
        l, t, w, h = virtual_screen_rect()
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        self.assertTrue(screen_coords_in_virtual_bounds(l + 1, t + 1))


if __name__ == "__main__":
    unittest.main()
