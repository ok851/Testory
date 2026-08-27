# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


class TestDesktopAppCatalog(unittest.TestCase):
    def test_build_and_load_catalog(self):
        from modules.desktop.desktop_app_catalog import build_catalog_from_start_menu, _catalog_path

        with patch("modules.desktop.desktop_discovery._refresh_start_menu_index") as mock_idx:
            mock_idx.return_value = {
                "notepad": r"C:\Windows\System32\notepad.exe",
                "calc.exe": r"C:\Windows\System32\calc.exe",
            }
            with tempfile.TemporaryDirectory() as td:
                cat_file = os.path.join(td, "desktop_app_catalog.json")
                with patch("modules.desktop.desktop_app_catalog._catalog_path", return_value=__import__("pathlib").Path(cat_file)):
                    data = build_catalog_from_start_menu()
                    self.assertGreaterEqual(len(data.get("apps") or []), 1)

    def test_find_catalog_installer_style_exe_name(self):
        from modules.desktop.desktop_app_catalog import find_catalog_app

        apps = [
            {
                "id": "awesun",
                "display_name": "AweSun",
                "exe_name": "AweSun.exe",
                "path": r"D:\AweSun\AweSun.exe",
                "aliases": ["awesun", "awesun.exe"],
            }
        ]
        with patch("modules.desktop.desktop_app_catalog.list_catalog_apps", return_value=apps):
            app = find_catalog_app("AweSun_16.2.0.27059_x64.exe")
            self.assertIsNotNone(app)
            self.assertEqual(app["path"], r"D:\AweSun\AweSun.exe")

    @unittest.skipUnless(sys.platform == "win32", "windows only")
    def test_attachment_spec_has_hwnd(self):
        from modules.desktop.desktop_discovery import attachment_spec_for_window, list_visible_windows

        wins = list_visible_windows()
        if not wins:
            self.skipTest("no visible windows")
        spec, _title = attachment_spec_for_window(wins[0]["hwnd"])
        self.assertIn("hwnd", spec)
        self.assertEqual(spec["hwnd"], wins[0]["hwnd"])


if __name__ == "__main__":
    unittest.main()
