# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch

from desktop_shell_listview import (
    _match_icon_name,
    find_icon_index_by_name,
    resolve_shell_listview_at_screen,
    shell_message_enabled,
)


class TestDesktopShellListview(unittest.TestCase):
    def test_match_icon_name(self):
        self.assertTrue(_match_icon_name("控制面板", "控制面板"))
        self.assertTrue(_match_icon_name("Control Panel", "control panel"))

    def test_shell_enabled_default(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(shell_message_enabled())

    @patch("desktop_shell_listview._get_listview_item_text")
    @patch("desktop_shell_listview._user32")
    def test_find_icon_index(self, mock_u32, mock_text):
        mock_u32.return_value.SendMessageW.return_value = 2
        mock_text.side_effect = ["回收站", "控制面板"]
        idx = find_icon_index_by_name(12345, "控制面板")
        self.assertEqual(idx, 1)


    @patch("desktop_shell_listview.get_desktop_listview_hwnd", return_value=999)
    def test_resolve_at_screen(self, _lv):
        target = resolve_shell_listview_at_screen(38, 941, icon_name="控制面板")
        self.assertIsNotNone(target)
        self.assertEqual(target.listview_hwnd, 999)
        self.assertEqual(target.icon_name, "控制面板")


if __name__ == "__main__":
    unittest.main()
