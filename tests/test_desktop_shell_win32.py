# -*- coding: utf-8 -*-
import sys
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class TestDesktopShellWin32(unittest.TestCase):
    def test_collect_desktop_icons(self):
        from desktop_shell_win32 import refresh_win32_desktop_icon_cache

        bounds = refresh_win32_desktop_icon_cache(force=True)
        self.assertIsInstance(bounds, list)
        if bounds:
            b = bounds[0]
            self.assertIn("left", b)
            self.assertLess(b["left"], b["right"])
            self.assertLess(b["top"], b["bottom"])

    def test_sanitize_rejects_fullscreen_rect(self):
        from desktop_shell_win32 import _screen_size, sanitize_hover_rect

        sw, sh = _screen_size()
        self.assertIsNone(sanitize_hover_rect((0, 0, sw, sh)))

    def test_hwnd_under_cursor_returns_int_or_none(self):
        from desktop_shell_win32 import hwnd_under_cursor

        h = hwnd_under_cursor(8, 8, set())
        self.assertTrue(h is None or isinstance(h, int))


if __name__ == "__main__":
    unittest.main()
