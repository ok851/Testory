# -*- coding: utf-8 -*-
import unittest

from desktop_automation import DesktopAutomation
from desktop_locator import is_desktop_shell_spec


class TestDesktopShellResolve(unittest.TestCase):
    def test_program_manager_is_shell(self):
        spec = {"window_title": "Program Manager", "process": "explorer.exe"}
        self.assertTrue(is_desktop_shell_spec(spec))

    def test_coordinate_on_shell_uses_screen_coords_only(self):
        auto = DesktopAutomation()
        spec = {
            "surface": "desktop_shell",
            "window_title": "Program Manager",
            "pick_center": "38,941",
            "uia_path": [{"control_type": "ListItem", "name": "控制面板"}],
        }
        attempts = auto._build_resolve_attempts("coordinate", "38,941", spec)
        self.assertEqual(attempts, [("coordinate", "38,941")])

    def test_uia_path_still_prefers_hit_before_coordinate(self):
        auto = DesktopAutomation()
        spec = {
            "surface": "desktop_shell",
            "window_title": "Program Manager",
            "pick_center": "38,941",
            "uia_path": [{"control_type": "ListItem", "name": "控制面板"}],
        }
        attempts = auto._build_resolve_attempts(
            "uia_path",
            '[{"control_type": "ListItem", "name": "控制面板"}]',
            spec,
        )
        self.assertEqual(attempts[0][0], "uia_path")
        self.assertEqual(attempts[1], ("__desktop_hit__", "38,941"))
        self.assertEqual(attempts[-1], ("coordinate", "38,941"))


if __name__ == "__main__":
    unittest.main()
