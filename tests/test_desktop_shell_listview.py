# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch

from modules.desktop.desktop_shell_listview import (
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

    @patch("desktop_shell_listview._get_listview_item_text", return_value="test_report")
    @patch("desktop_shell_listview._client_rect_to_screen", return_value=(100, 200, 180, 300))
    @patch("desktop_shell_listview._get_listview_item_rect", return_value=(10, 20, 90, 120))
    @patch("desktop_shell_listview._screen_to_client", return_value=(40, 50))
    @patch("desktop_shell_listview.get_desktop_listview_hwnd", return_value=42)
    @patch("desktop_win32_snapshot.get_window_rect", return_value=(0, 0, 1920, 1080))
    def test_peek_desktop_icon_at_point(self, *_mocks):
        from modules.desktop.desktop_shell_listview import peek_desktop_icon_at_point
        import ctypes
        from ctypes import wintypes

        class FakeInfo:
            def __init__(self):
                self.iItem = 3
                self.flags = 0x0002 | 0x0004
                self.iSubItem = 0
                self.pt = type("P", (), {"x": 0, "y": 0})()

        # Patch SendMessageW to fill LVHITTESTINFO via byref — simpler: patch hit_test
        with patch(
            "desktop_shell_listview.hit_test_listview_item",
            return_value=(3, (100, 200, 180, 300), "test_report"),
        ):
            t = peek_desktop_icon_at_point(120, 250)
        self.assertIsNotNone(t)
        self.assertEqual(t.index, 3)
        self.assertEqual(t.icon_name, "test_report")
        self.assertEqual(t.screen_rect, (100, 200, 180, 300))
        self.assertEqual(t.control_type, "ListItem")


class TestLayeredLocatePrefersShell(unittest.TestCase):
    def test_noise_label(self):
        from modules.desktop.desktop_visual_picker import _is_shell_noise_label

        self.assertTrue(_is_shell_noise_label("FolderView"))
        self.assertTrue(_is_shell_noise_label("SysListView32"))
        self.assertFalse(_is_shell_noise_label("test_report.xlsx"))

    @patch("desktop_visual_picker._locate_via_uia", return_value={})
    @patch("desktop_visual_picker._locate_via_win32")
    @patch("desktop_shell_listview.peek_desktop_icon_at_point")
    def test_layered_prefers_shell_icon(self, mock_icon, mock_win32, _uia):
        from modules.desktop.desktop_visual_picker import _layered_locate
        from modules.desktop.desktop_shell_listview import ShellIconTarget

        mock_win32.return_value = {
            "rect": (0, 0, 1920, 1080),
            "label": "FolderView",
        }
        mock_icon.return_value = ShellIconTarget(
            listview_hwnd=1,
            index=2,
            icon_name="Chrome",
            client_x=10,
            client_y=10,
            screen_x=50,
            screen_y=60,
            screen_rect=(20, 30, 100, 110),
        )
        r = _layered_locate(50, 60)
        self.assertEqual(r["source"], "shell_listview")
        self.assertEqual(r["label"], "Chrome")
        self.assertEqual(r["rect"], (20, 30, 100, 110))


if __name__ == "__main__":
    unittest.main()
