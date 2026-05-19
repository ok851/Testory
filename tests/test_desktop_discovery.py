# -*- coding: utf-8 -*-
import os
import sys
import unittest
from unittest.mock import patch

from desktop_discovery import (
    ResolveResult,
    _normalize_query,
    _resolve_via_app_paths,
    _resolve_via_system32,
    format_resolve_error,
    resolve_executable,
    resolve_executable_with_meta,
    smart_resolve_launch_path,
)


class TestDesktopDiscovery(unittest.TestCase):
    @patch("desktop_discovery.shutil.which", return_value=r"C:\Windows\System32\notepad.exe")
    def test_resolve_via_path(self, _which):
        self.assertEqual(
            resolve_executable("notepad.exe"),
            os.path.normpath(r"C:\Windows\System32\notepad.exe"),
        )

    @patch("desktop_discovery.shutil.which", return_value=None)
    @patch("desktop_discovery._resolve_via_deep_search", return_value="")
    @patch("desktop_discovery._resolve_via_start_menu", return_value="")
    @patch("desktop_discovery._resolve_via_uninstall", return_value="")
    @patch("desktop_discovery._resolve_via_system32", return_value="")
    @patch("desktop_discovery._resolve_via_where", return_value="")
    @patch("desktop_discovery._resolve_via_app_paths")
    def test_resolve_falls_through_to_app_paths(
        self, mock_app_paths, _wh, _s32, _un, _sm, _deep, _which
    ):
        exe = sys.executable
        mock_app_paths.return_value = exe
        meta = resolve_executable_with_meta("uat_fake_client_xyz")
        self.assertTrue(meta.found, meta.tried)
        self.assertEqual(meta.path, exe)
        self.assertEqual(meta.method, "registry_app_paths")

    @patch("desktop_discovery.shutil.which", return_value=None)
    @patch("desktop_discovery._resolve_via_where", return_value="")
    @patch("desktop_discovery._resolve_via_app_paths", return_value="")
    @patch("desktop_discovery._resolve_via_uninstall", return_value="")
    @patch("desktop_discovery._resolve_via_start_menu", return_value="")
    @patch("desktop_discovery._resolve_via_deep_search", return_value="")
    @patch("desktop_discovery._resolve_via_system32", return_value=r"C:\Windows\System32\calc.exe")
    def test_resolve_system32(self, _s32, _deep, _sm, _un, _ap, _wh, _which):
        meta = resolve_executable_with_meta("calc")
        self.assertEqual(meta.path, r"C:\Windows\System32\calc.exe")

    def test_normalize_query(self):
        q, base, exe, stem = _normalize_query('"MyApp.exe"')
        self.assertEqual(exe, "MyApp.exe")
        self.assertEqual(stem, "myapp")

    def test_format_resolve_error(self):
        msg = format_resolve_error(
            ResolveResult(query="foo", tried=["path_env", "system32"])
        )
        self.assertIn("foo", msg)
        self.assertIn("path_env", msg)

    def test_smart_resolve_existing_file(self):
        path = sys.executable
        self.assertEqual(smart_resolve_launch_path(path), os.path.normpath(path))

    @unittest.skipUnless(sys.platform == "win32", "windows only")
    def test_resolve_calc_on_windows(self):
        meta = resolve_executable_with_meta("calc.exe")
        self.assertTrue(meta.found, meta.tried)


if __name__ == "__main__":
    unittest.main()
