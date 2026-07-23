# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch


class LocalAuthHelpersTests(unittest.TestCase):
    def test_generate_recovery_key_format(self):
        from app import _generate_recovery_key

        key = _generate_recovery_key()
        parts = key.split("-")
        self.assertEqual(len(parts), 4)
        self.assertTrue(all(len(p) == 4 and p.isalnum() for p in parts))

    def test_allow_local_auth_desktop(self):
        from app import _allow_local_auth

        with patch("deployment_config.is_client_mode", return_value=True), patch(
            "deployment_config.is_standalone_mode", return_value=False
        ), patch("deployment_config.is_local_standalone_desktop", return_value=False):
            self.assertTrue(_allow_local_auth())

    def test_begin_resize_edges_map(self):
        from packaging.desktop_shell import _HT_EDGES

        self.assertIn("left", _HT_EDGES)
        self.assertIn("bottom-right", _HT_EDGES)

    def test_desktop_api_bounds_helpers_exist(self):
        from packaging.desktop_shell import DesktopWindowApi
        from pathlib import Path

        api = DesktopWindowApi(Path("."))
        self.assertTrue(callable(api.get_bounds))
        self.assertTrue(callable(api.set_bounds))
        self.assertTrue(callable(api.begin_resize))


if __name__ == "__main__":
    unittest.main()
