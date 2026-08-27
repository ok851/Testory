# -*- coding: utf-8 -*-
"""指针执行 smoke（默认 PostMessage，不调用 SendInput）。"""

import unittest
from unittest.mock import patch


class TestSendInput(unittest.TestCase):
    @patch("modules.desktop.desktop_input.message_click_at_screen", return_value=1)
    @patch("modules.desktop.desktop_input.physical_mouse_enabled", return_value=False)
    def test_postmessage_when_not_physical(self, _phys, mock_msg):
        from modules.desktop.desktop_input import sendinput_pointer_at_screen

        sendinput_pointer_at_screen(100, 200, "click")
        mock_msg.assert_called_once()

    @patch("modules.desktop.desktop_input._user32")
    @patch("modules.desktop.desktop_input.physical_mouse_enabled", return_value=True)
    @patch("modules.desktop.desktop_input.should_focus_desktop_before_pointer", return_value=False)
    @patch("modules.desktop.desktop_input.restore_cursor_after_pointer", return_value=False)
    def test_sendinput_when_physical(self, _restore, _focus, _phys, mock_u32):
        from modules.desktop.desktop_input import sendinput_pointer_at_screen

        sendinput_pointer_at_screen(100, 200, "click")
        self.assertTrue(mock_u32().SendInput.called)


if __name__ == "__main__":
    unittest.main()
