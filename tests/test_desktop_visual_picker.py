# -*- coding: utf-8 -*-
import json
import unittest
from unittest.mock import MagicMock, patch

from modules.desktop.desktop_uia_snapshot import SnapshotCaptureResult
from modules.desktop.desktop_visual_picker import (
    CAPTURE_MODE_SMART,
    VisualRegionPickerOverlay,
    build_pick_from_smart_click,
)


class TestDesktopVisualPicker(unittest.TestCase):
    @patch("modules.desktop.desktop_visual_engine.build_visual_step_payload")
    @patch("modules.desktop.desktop_uia_snapshot.capture_element_snapshot_at_point")
    def test_build_pick_from_smart_click_merges_snapshot(
        self, mock_snap, mock_build
    ):
        from modules.desktop.desktop_visual_engine import VisualStepPayload

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

    @patch("modules.desktop.desktop_precise_locator.capture_rect_preview_b64")
    @patch("modules.desktop.desktop_visual_engine.build_visual_step_payload")
    @patch("modules.desktop.desktop_uia_snapshot.capture_element_snapshot_at_point")
    def test_frozen_preview_not_replaced_after_ui_change(
        self, mock_snap, mock_build, mock_preview
    ):
        """点击后 UI 已变时，仍使用点击瞬间冻结的预览与名称。"""
        from modules.desktop.desktop_visual_engine import VisualStepPayload

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
        mock_preview.return_value = "POST_CLICK_WRONG_PREVIEW"
        # 模拟点击后界面已切到「扫码登录」
        mock_snap.return_value = SnapshotCaptureResult(
            ok=True,
            element_snapshot={"selector": {"key_candidates": [{"property": "uia-name", "value": "扫码登录"}]}},
            screen_center=(12, 22),
            bounding_rect=(0, 0, 48, 48),
            element_label="扫码登录",
            control_type="Text",
        )
        pick = build_pick_from_smart_click(
            10,
            20,
            action="click",
            frozen_preview_b64="FROZEN_ACCOUNT_LOGIN",
            frozen_rect=(100, 200, 180, 230),
            frozen_label="账密登录",
        )
        self.assertTrue(pick.get("preview_frozen"))
        self.assertIn("账密登录", pick["label"])
        self.assertNotIn("扫码登录", pick["label"])
        # 预览与回放模板必须同源，否则列表/详情会出现两套图
        data = json.loads(pick["selector_value"])
        self.assertEqual(pick["preview_image_b64"], data["template_image_base64"])
        self.assertTrue(mock_build.called)
        last_kwargs = mock_build.call_args_list[-1].kwargs or {}
        self.assertEqual(last_kwargs.get("template_image_b64"), "FROZEN_ACCOUNT_LOGIN")
        self.assertTrue(last_kwargs.get("preserve_full_template"))
        # 快照关键属性应与冻结名一致
        snap = pick.get("element_snapshot") or {}
        kc = ((snap.get("selector") or {}).get("key_candidates") or [])
        self.assertTrue(kc)
        self.assertEqual(kc[0].get("value"), "账密登录")
        self.assertEqual(pick.get("name"), "账密登录")
        self.assertEqual(pick["structure_info"]["element_text"], "账密登录")

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

    def test_freeze_capture_prefers_hover_rect(self):
        picker = VisualRegionPickerOverlay(
            on_record=lambda _d: None,
            on_message=lambda _m: None,
            on_error=lambda _e: None,
            on_close=lambda: None,
        )
        picker._hover_rect = (10, 20, 90, 50)
        picker._hover_label = "账密登录"
        with patch(
            "modules.desktop.desktop_precise_locator.capture_rect_preview_b64",
            return_value="IMG",
        ):
            picker._freeze_capture_at_point(40, 30)
        self.assertEqual(picker._frozen_label, "账密登录")
        self.assertEqual(picker._frozen_rect, (10, 20, 90, 50))
        self.assertEqual(picker._frozen_preview_b64, "IMG")

    def test_capture_click_guard_queue(self):
        from modules.desktop.desktop_capture_click_shield import CaptureClickShield

        sink = CaptureClickShield()
        self.assertFalse(sink.enabled)
        sink._events.put(("click", 12, 34))
        self.assertEqual(sink.pop_click(), (12, 34))
        sink.uninstall()
        self.assertFalse(sink.enabled)

    def test_reliable_hit_rejects_cursor_fallback(self):
        from modules.desktop.desktop_visual_picker import _is_reliable_element_hit, _is_shell_noise_label

        self.assertFalse(_is_shell_noise_label(""))
        self.assertFalse(
            _is_reliable_element_hit(
                {"rect": (60, 60, 140, 140), "source": "fallback", "label": ""},
                100,
                100,
            )
        )
        self.assertTrue(
            _is_reliable_element_hit(
                {
                    "rect": (80, 90, 180, 130),
                    "source": "uia",
                    "label": "确定",
                    "control_type": "Button",
                },
                100,
                100,
            )
        )

    def test_uia_factory_creates_instance(self):
        from modules.desktop.desktop_uia_core import _element_from_point, _get_uia

        uia = _get_uia()
        self.assertIsNotNone(uia, "IUIAutomation 工厂不可用，应用内捕获会全部失败")
        el = _element_from_point(uia, 8, 8)
        # 桌面角落通常能命中某个元素（桌面/任务栏/图标）
        self.assertIsNotNone(el)


if __name__ == "__main__":
    unittest.main()
