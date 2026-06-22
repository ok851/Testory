# -*- coding: utf-8 -*-
"""移动端连接与环境配置测试。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMobileEnvConfig(unittest.TestCase):
    def test_adb_path_prefers_env_over_plugin(self):
        from mobile_env_config import adb_path_source

        with patch.dict(os.environ, {"ADB_PATH": __file__}, clear=False):
            with patch("mobile_plugin_bundles.get_installed_adb_path", return_value="C:/plugin/adb.exe"):
                self.assertEqual(adb_path_source(), "env")

    def test_pick_default_device_includes_emulator(self):
        from mobile_device_manager import pick_default_device

        with patch(
            "mobile_device_manager.list_usb_devices",
            return_value=[{"udid": "emulator-5554", "state": "device", "display_name": "LDPlayer"}],
        ):
            with patch("mobile_device_manager.get_device_info", return_value={"width": 1080, "height": 1920}):
                dev = pick_default_device()
        self.assertIsNotNone(dev)
        self.assertEqual(dev.get("udid"), "emulator-5554")

    def test_mobile_driver_adb_runtime_available(self):
        with patch.dict(os.environ, {"ENABLE_MOBILE": "1", "MOBILE_DRIVER": "adb"}, clear=False):
            from importlib import reload
            import mobile_env_config as cfg

            reload(cfg)
            self.assertTrue(cfg.mobile_runtime_available())
            self.assertFalse(cfg.requires_appium_for_execution())


class TestMobileConnect(unittest.TestCase):
    def test_format_connect_error_for_unauthorized(self):
        from mobile_device_manager import format_connect_error

        msg = format_connect_error({"udid": "DEV1", "state": "unauthorized"})
        self.assertIn("尚未授权", msg)

    def test_finish_studio_connect_sets_udid(self):
        from mobile_connect import finish_studio_connect

        with patch("mobile_connect.set_connected_udid") as mock_set:
            with patch("mobile_connect.get_device_info", return_value={"width": 1080, "height": 2400, "model": "Pixel"}):
                with patch("mobile_connect.list_user_apps", return_value=[]):
                    payload = finish_studio_connect("127.0.0.1:5555", try_appium=False)
        mock_set.assert_called_once_with("127.0.0.1:5555")
        self.assertEqual(payload["udid"], "127.0.0.1:5555")
        self.assertTrue(payload["is_emulator"])
