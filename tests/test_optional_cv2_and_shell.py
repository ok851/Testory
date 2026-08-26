# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest


class OptionalCv2Tests(unittest.TestCase):
    def test_module_imports(self):
        from modules.core.optional_cv2 import CV2_AVAILABLE, get_cv2

        self.assertIsInstance(CV2_AVAILABLE, bool)
        # get_cv2 may be None on Lite; just ensure callable contract
        _ = get_cv2()


class DesktopShellKwargsTests(unittest.TestCase):
    def test_filter_drops_unsupported_icon(self):
        from packaging.desktop_shell import _filter_create_window_kwargs

        def create_window(title, frameless=False):
            return None

        filtered = _filter_create_window_kwargs(
            create_window,
            {"title": "Testory", "frameless": True, "icon": "x.ico", "easy_drag": True},
        )
        self.assertEqual(filtered, {"title": "Testory", "frameless": True})

    def test_filter_keeps_var_keyword(self):
        from packaging.desktop_shell import _filter_create_window_kwargs

        def create_window(**kwargs):
            return kwargs

        raw = {"title": "t", "icon": "x.ico"}
        self.assertEqual(_filter_create_window_kwargs(create_window, raw), raw)


if __name__ == "__main__":
    unittest.main()
