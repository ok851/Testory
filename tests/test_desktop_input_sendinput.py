# -*- coding: utf-8 -*-
"""SendInput 结构 smoke（不移动真实鼠标）。"""

import unittest
from unittest.mock import patch


class TestSendInput(unittest.TestCase):
    @patch("desktop_input._user32")
    def test_sendinput_pointer_click(self, mock_u32):
        from desktop_input import sendinput_pointer_at_screen

        sendinput_pointer_at_screen(100, 200, "click")
        self.assertTrue(mock_u32().SendInput.called)

    @patch("desktop_input._user32")
    def test_sendinput_double_click(self, mock_u32):
        from desktop_input import sendinput_pointer_at_screen

        sendinput_pointer_at_screen(100, 200, "double_click")
        self.assertGreaterEqual(mock_u32().SendInput.call_count, 2)


if __name__ == "__main__":
    unittest.main()
