# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from desktop_input import (
    infer_effect_keyword,
    screen_coords_in_virtual_bounds,
    should_verify_desktop_effect,
    verify_pointer_delivered,
    virtual_screen_rect,
    wait_for_desktop_effect,
)


class TestDesktopInput(unittest.TestCase):
    def test_infer_from_description(self):
        self.assertEqual(
            infer_effect_keyword({}, "录制：双击（自动）「控制面板」"),
            "控制面板",
        )

    def test_should_verify_effect_defaults(self):
        # 普通单击：默认不按窗口标题验收
        self.assertFalse(should_verify_desktop_effect({}, action="click"))
        self.assertFalse(should_verify_desktop_effect({}, action="right_click"))
        # 双击默认验收
        self.assertTrue(should_verify_desktop_effect({}, action="double_click"))
        # 显式声明
        self.assertTrue(
            should_verify_desktop_effect({"verify_effect": 1}, action="click")
        )
        self.assertFalse(
            should_verify_desktop_effect({"verify_effect": 0}, action="double_click")
        )
        self.assertTrue(
            should_verify_desktop_effect({"desktop_shell": True}, action="click")
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

    @patch("desktop_input._enum_visible_windows")
    @patch("desktop_input.get_foreground_hwnd", return_value=0)
    def test_wait_ignores_preexisting_title(self, _fg, mock_enum):
        mock_enum.return_value = [(100, "控制面板", "CabinetWClass")]
        self.assertFalse(
            wait_for_desktop_effect(
                "控制面板",
                timeout=0.6,
                poll=0.1,
                titles_before={"控制面板"},
                hwnds_before={100},
            )
        )

    @patch("desktop_input._enum_visible_windows")
    @patch("desktop_input.get_foreground_hwnd", return_value=0)
    def test_wait_detects_new_title(self, _fg, mock_enum):
        mock_enum.return_value = [
            (100, "控制面板", "CabinetWClass"),
            (200, "控制面板", "CabinetWClass"),
        ]
        self.assertTrue(
            wait_for_desktop_effect(
                "控制面板",
                timeout=0.6,
                poll=0.1,
                titles_before=set(),
                hwnds_before={100},
            )
        )


if __name__ == "__main__":
    unittest.main()
