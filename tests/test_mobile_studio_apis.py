# -*- coding: utf-8 -*-
"""移动端工作室 API 与助手事件测试。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEmulatorRecognition(unittest.TestCase):
    def test_is_emulator_udid_localhost_port(self):
        from modules.mobile.mobile_device_manager import is_emulator_udid

        self.assertTrue(is_emulator_udid("emulator-5554"))
        self.assertTrue(is_emulator_udid("127.0.0.1:5555"))
        self.assertTrue(is_emulator_udid("localhost:7555"))
        self.assertFalse(is_emulator_udid("ABC123DEF"))

    def test_list_emulators_includes_localhost(self):
        from modules.mobile.mobile_device_manager import list_emulators

        with patch(
            "mobile_device_manager.list_usb_devices",
            return_value=[
                {"udid": "127.0.0.1:5555", "state": "device", "display_name": "LDPlayer"},
                {"udid": "ABC", "state": "device", "display_name": "Phone"},
            ],
        ):
            with patch("mobile_device_manager.get_device_info", return_value={"width": 1080, "height": 1920}):
                emus = list_emulators()
        self.assertEqual(len(emus), 1)
        self.assertEqual(emus[0]["udid"], "127.0.0.1:5555")


class TestAssistantEvents(unittest.TestCase):
    def test_normalize_click_with_resource_id(self):
        """v3: text 优先于 resource_id 作为定位策略。"""
        from modules.mobile.mobile_assistant_events import normalize_assistant_event

        step = normalize_assistant_event({
            "type": "click",
            "node": {"resource_id": "com.app:id/login", "text": "登录"},
            "bounds": [10, 20, 110, 70],
        })
        self.assertEqual(step["action"], "tap")
        # v3: text 优先 — "登录" 比 "com.app:id/login" 更易读
        self.assertEqual(step["selector_type"], "accessibility_id")
        self.assertEqual(step["selector_value"], "登录")
        self.assertEqual(step["automation_layer"], "android")

    def test_normalize_swipe(self):
        from modules.mobile.mobile_assistant_events import normalize_assistant_event

        step = normalize_assistant_event({
            "type": "scroll",
            "x1": 100, "y1": 800, "x2": 100, "y2": 200,
        })
        self.assertEqual(step["action"], "swipe")
        self.assertIn("x1", step["mobile_spec"])


class TestStudioState(unittest.TestCase):
    def test_arm_and_clear(self):
        from modules.mobile.mobile_studio_state import clear_arm_state, get_arm_state, set_arm_state

        clear_arm_state()
        self.assertEqual(get_arm_state()["mode"], "idle")
        set_arm_state(mode="capture_element", udid="emulator-5554", case_id=9, source="assistant")
        st = get_arm_state()
        self.assertEqual(st["mode"], "capture_element")
        self.assertEqual(st["udid"], "emulator-5554")
        self.assertEqual(st["case_id"], 9)
        clear_arm_state()
        self.assertEqual(get_arm_state()["mode"], "idle")


class TestPluginCatalog(unittest.TestCase):
    def test_assistant_plugin_in_catalog(self):
        from web_capture.plugin_market import _all_catalog_items

        ids = {p["id"] for p in _all_catalog_items()}
        self.assertIn("mobile-testory-assistant", ids)


if __name__ == "__main__":
    unittest.main()

