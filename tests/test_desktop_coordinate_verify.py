# -*- coding: utf-8 -*-
import unittest

from desktop_automation import (
    DesktopAutomation,
    _is_client_coord_selector,
    _is_relative_coord_selector,
    _is_screen_position_selector,
    _parse_locator_candidate_attempts,
    normalize_automation_layer,
)
from desktop_input import infer_effect_keyword


class TestDesktopCoordinateVerify(unittest.TestCase):
    def test_infer_skips_folderview(self):
        self.assertEqual(
            infer_effect_keyword({"target_name": "FolderView"}, ""),
            "",
        )
        self.assertEqual(
            infer_effect_keyword({}, "录制：双击「控制面板」"),
            "控制面板",
        )

    def test_normalize_layer_from_desktop_spec(self):
        step = {
            "action": "double_click",
            "desktop_spec": '{"hwnd": 1, "window_title": "设置"}',
        }
        self.assertEqual(normalize_automation_layer(step), "desktop")

    def test_locator_candidates_skip_spurious_name(self):
        raw = (
            '[{"selector_type": "coordinate", "selector_value": "1,2", "score": 88},'
            '{"selector_type": "name", "selector_value": "FolderView", "score": 72}]'
        )
        attempts = _parse_locator_candidate_attempts(raw)
        self.assertEqual(attempts, [("coordinate", "1,2")])

    def test_screen_position_selector_flag(self):
        self.assertTrue(_is_screen_position_selector("coordinate"))
        self.assertTrue(_is_screen_position_selector("client_coord"))
        self.assertTrue(_is_screen_position_selector("relative_coord"))
        self.assertFalse(_is_screen_position_selector("uia_path"))

    def test_client_coord_selector_flag(self):
        self.assertTrue(_is_client_coord_selector("client_coord"))
        self.assertFalse(_is_client_coord_selector("coordinate"))

    def test_relative_coord_selector_flag(self):
        self.assertTrue(_is_relative_coord_selector("relative_coord"))
        self.assertFalse(_is_relative_coord_selector("client_coord"))

    def test_client_coord_resolve_attempts(self):
        auto = DesktopAutomation()
        spec = {"hwnd": 12345, "pick_center": "10,20"}
        attempts = auto._build_resolve_attempts(
            "client_coord",
            "100,200",
            spec,
        )
        self.assertEqual(attempts, [("client_coord", "100,200")])

    def test_relative_coord_resolve_attempts(self):
        auto = DesktopAutomation()
        spec = {"hwnd": 12345}
        rel = '{"x_pct": 0.5, "y_pct": 0.25}'
        attempts = auto._build_resolve_attempts("relative_coord", rel, spec)
        self.assertEqual(attempts, [("relative_coord", rel)])

    def test_coordinate_prefers_uia_and_template_before_screen_coord(self):
        auto = DesktopAutomation()
        spec = {
            "hwnd": 1,
            "window_title": "设置",
            "pick_center": "10,20",
            "uia_path": [{"name": "控制面板"}],
        }
        vt_payload = '{"png_b64": "abc", "threshold": 0.72}'
        attempts = auto._build_resolve_attempts(
            "coordinate",
            "37,941",
            spec,
            locator_candidates=[
                {"selector_type": "name", "selector_value": "FolderView", "score": 90},
                {
                    "selector_type": "visual_template",
                    "selector_value": vt_payload,
                    "score": 96,
                },
            ],
        )
        self.assertEqual(attempts[0][0], "uia_path")
        self.assertEqual(attempts[1][0], "visual_template")
        self.assertEqual(attempts[-1], ("coordinate", "37,941"))


if __name__ == "__main__":
    unittest.main()
