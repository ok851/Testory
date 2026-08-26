# -*- coding: utf-8 -*-
"""desktop_visual_engine 单元测试。"""

import base64
import unittest
import unittest.mock

import numpy as np


class TestVisualEngine(unittest.TestCase):
    def test_legacy_step_detection(self):
        from modules.desktop.desktop_visual_engine import is_legacy_desktop_step

        self.assertTrue(
            is_legacy_desktop_step(
                {
                    "automation_layer": "desktop",
                    "action": "click",
                    "selector_type": "name",
                    "selector_value": "FolderView",
                }
            )
        )
        self.assertFalse(
            is_legacy_desktop_step(
                {
                    "automation_layer": "desktop",
                    "action": "click",
                    "selector_type": "visual",
                    "selector_value": "{}",
                }
            )
        )

    def test_payload_roundtrip(self):
        from modules.desktop.desktop_visual_engine import VisualStepPayload

        p = VisualStepPayload(
            template_image_base64="abc",
            click_offset_x=5,
            click_offset_y=6,
            match_threshold=0.8,
            match_method="orb",
            template_width=40,
            template_height=40,
            search_anchor_x=100,
            search_anchor_y=200,
            element_snapshot={"selector": {"anchor_props": "ListItem"}},
        )
        raw = p.to_json()
        p2 = VisualStepPayload.from_json(raw)
        self.assertEqual(p2.click_offset_x, 5)
        self.assertEqual(p2.match_threshold, 0.8)
        self.assertEqual(p2.search_anchor_x, 100)
        self.assertIsNotNone(p2.element_snapshot)

    def test_need_relearn_band(self):
        from modules.desktop.desktop_visual_engine import _need_relearn_score

        self.assertTrue(_need_relearn_score(0.60, 0.72, "template"))
        self.assertFalse(_need_relearn_score(0.40, 0.72, "template"))

    def test_template_match_on_synthetic_scene(self):
        import cv2

        from modules.desktop.desktop_visual_engine import encode_png_bgr, locate_template_on_screen

        tpl = np.zeros((32, 32, 3), dtype=np.uint8)
        tpl[8:24, 8:24] = (0, 255, 0)
        scene = np.zeros((120, 120, 3), dtype=np.uint8)
        scene[40:72, 50:82] = tpl
        tpl_png = encode_png_bgr(tpl)
        scene_png = encode_png_bgr(scene)
        hit = locate_template_on_screen(
            tpl_png, scene_png, match_method="template", threshold=0.5
        )
        self.assertGreaterEqual(hit.score, 0.5)
        self.assertLess(abs(hit.left - 50), 4)
        self.assertLess(abs(hit.top - 40), 4)

    def test_failure_artifact_is_smaller_than_full_screen(self):
        import cv2

        from modules.desktop.desktop_visual_engine import (
            VisualStepPayload,
            build_visual_failure_artifact_png,
            encode_png_bgr,
        )

        tpl = np.zeros((24, 24, 3), dtype=np.uint8)
        tpl[4:20, 4:20] = (255, 0, 0)
        payload = VisualStepPayload(
            template_image_base64=base64.b64encode(encode_png_bgr(tpl)).decode("ascii"),
            click_offset_x=12,
            click_offset_y=12,
            match_threshold=0.9,
            match_method="template",
            template_width=24,
            template_height=24,
        )
        with unittest.mock.patch(
            "desktop_visual_engine.capture_virtual_desktop_png",
            return_value=encode_png_bgr(np.zeros((600, 800, 3), dtype=np.uint8)),
        ), unittest.mock.patch(
            "desktop_visual_engine.capture_region_png",
            return_value=encode_png_bgr(np.zeros((200, 300, 3), dtype=np.uint8)),
        ):
            png = build_visual_failure_artifact_png(payload)
        arr = np.frombuffer(png, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        self.assertIsNotNone(img)
        self.assertLess(img.shape[1], 800)
        self.assertLess(img.shape[0], 600)

    def test_refine_corners(self):
        import cv2

        from modules.desktop.desktop_visual_engine import refine_template_by_corners

        roi = np.zeros((80, 80, 3), dtype=np.uint8)
        cv2.rectangle(roi, (30, 30), (50, 50), (255, 255, 255), 2)
        refined, ox, oy = refine_template_by_corners(roi, max_side=48)
        self.assertGreater(refined.shape[0], 4)
        self.assertGreaterEqual(ox, 0)


class TestDesktopRuntime(unittest.TestCase):
    def test_parse_desktop_spec(self):
        from modules.desktop.desktop_runtime import parse_desktop_spec

        self.assertEqual(parse_desktop_spec('{"hwnd":1}'), {"hwnd": 1})
        self.assertEqual(parse_desktop_spec(None), {})


if __name__ == "__main__":
    unittest.main()
