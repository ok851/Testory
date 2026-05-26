# -*- coding: utf-8 -*-
import json
import unittest
from unittest.mock import MagicMock, patch

from desktop_uia_snapshot import SnapshotCaptureResult
from desktop_visual_picker import (
    CAPTURE_MODE_SMART,
    VisualRegionPickerOverlay,
    build_pick_from_smart_click,
)


class TestDesktopVisualPicker(unittest.TestCase):
    @patch("desktop_visual_engine.build_visual_step_payload")
    @patch("desktop_uia_snapshot.capture_element_snapshot_at_point")
    def test_build_pick_from_smart_click_merges_snapshot(
        self, mock_snap, mock_build
    ):
        from desktop_visual_engine import VisualStepPayload

        payload = VisualStepPayload(
            template_image_base64="e30=",
            click_offset_x=5,
            click_offset_y=5,
            match_threshold=0.72,
            match_method="auto",
            template_width=40,
            template_height=40,
            search_anchor_x=10,
            search_anchor_y=20,
        )
        mock_build.return_value = payload
        mock_snap.return_value = SnapshotCaptureResult(
            ok=True,
            element_snapshot={"selector": {"key_candidates": [{"property": "uia-name", "value": "控制面板"}]}},
            screen_center=(12, 22),
            bounding_rect=(0, 0, 48, 48),
            element_label="控制面板",
            control_type="ListItem",
        )
        pick = build_pick_from_smart_click(10, 20, action="click")
        self.assertEqual(pick["capture_mode"], CAPTURE_MODE_SMART)
        self.assertIn("element_snapshot", pick)
        data = json.loads(pick["selector_value"])
        self.assertIn("element_snapshot", data)
        self.assertIn("控制面板", pick["label"])

    def test_arm_pick_enables_click_guard(self):
        armed_events = []
        picker = VisualRegionPickerOverlay(
            on_record=lambda _d: None,
            on_message=lambda _m: None,
            on_error=lambda _e: None,
            on_close=lambda: None,
            on_armed_change=armed_events.append,
        )
        picker._armed = False
        picker._begin_click_guard()
        self.assertTrue(picker._consume_click_guard)
        picker._armed = True
        picker._notify_armed_change()
        self.assertEqual(armed_events, [True])


if __name__ == "__main__":
    unittest.main()
