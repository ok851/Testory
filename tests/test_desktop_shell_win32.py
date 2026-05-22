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


if __name__ == "__main__":
    unittest.main()
