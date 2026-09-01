# -*- coding: utf-8 -*-
"""adb serial 解析 + 平台预导航用例步骤。"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestDeviceSerialForUser(unittest.TestCase):
    def test_prefers_live_adb_over_android_id(self):
        from modules.mobile.mobile_scrcpy_vision import get_device_serial_for_user

        with patch(
            "modules.mobile.mobile_scrcpy_vision._adb_authorized_serials",
            return_value=["3B163L00CF800000"],
        ), patch(
            "modules.mobile.mobile_device_manager.get_connected_udid",
            return_value=None,
        ), patch(
            "modules.mobile.mobile_device_manager.pick_best_authorized_device",
            return_value={"udid": "3B163L00CF800000", "state": "device"},
        ), patch(
            "modules.mobile.mobile_sync_store.list_paired_devices_for_user",
            return_value=[{"device_id": "9fb4aba4caf76b09"}],
        ):
            self.assertEqual(get_device_serial_for_user(1), "3B163L00CF800000")

    def test_ignores_offline_paired_android_id(self):
        from modules.mobile.mobile_scrcpy_vision import get_device_serial_for_user, is_adb_serial_online

        with patch(
            "modules.mobile.mobile_scrcpy_vision._adb_authorized_serials",
            return_value=["3B163L00CF800000"],
        ):
            self.assertFalse(is_adb_serial_online("9fb4aba4caf76b09"))
            self.assertTrue(is_adb_serial_online("3B163L00CF800000"))

        with patch(
            "modules.mobile.mobile_scrcpy_vision._adb_authorized_serials",
            return_value=["3B163L00CF800000"],
        ), patch(
            "modules.mobile.mobile_device_manager.get_connected_udid",
            return_value=None,
        ), patch(
            "modules.mobile.mobile_device_manager.pick_best_authorized_device",
            return_value=None,
        ), patch(
            "modules.mobile.mobile_sync_store.list_paired_devices_for_user",
            return_value=[{"device_id": "9fb4aba4caf76b09"}],
        ):
            self.assertEqual(get_device_serial_for_user(1), "3B163L00CF800000")


class TestPlatformNavigateRecords(unittest.TestCase):
    def test_navigate_record_from_extras(self):
        from modules.ai.ai_chat_tool_loop import _platform_navigate_action_records

        recs = _platform_navigate_action_records({"navigate_url": "https://example.com/login"})
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["action_type"], "navigate")
        self.assertIn("example.com", recs[0]["target"])

    def test_skip_about_blank(self):
        from modules.ai.ai_chat_tool_loop import _platform_navigate_action_records

        self.assertEqual(_platform_navigate_action_records({"navigate_url": "about:blank"}), [])


if __name__ == "__main__":
    unittest.main()
