# -*- coding: utf-8 -*-
"""IOSDeviceManager 单元测试：设备发现、idb 检查、命令拼装（mock subprocess）。"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mobile_engine.device.ios_device import (
    IOSDeviceManager,
    _search_tree,
    check_ios_preflight,
    is_ios_supported,
)


# ------------------------------------------------------------------
# _search_tree 深度限制
# ------------------------------------------------------------------

class TestSearchTree:
    def test_find_by_label(self):
        tree = {
            "label": "root",
            "children": [
                {"label": "button_ok", "value": "OK", "children": []},
                {"label": "input_field", "value": "", "children": [
                    {"label": "hint_text", "value": "Enter name", "children": []},
                ]},
            ],
        }
        result = _search_tree(tree, "hint_text")
        assert result is not None
        assert result["value"] == "Enter name"

    def test_find_by_value(self):
        tree = {"label": "", "value": "Submit", "children": []}
        result = _search_tree(tree, "Submit")
        assert result is not None

    def test_not_found(self):
        tree = {"label": "root", "children": []}
        assert _search_tree(tree, "nonexistent") is None

    def test_case_insensitive(self):
        tree = {"label": "MyButton", "children": []}
        assert _search_tree(tree, "mybutton") is not None

    def test_depth_limit(self):
        node = {"label": "deep", "value": "target", "children": []}
        for _ in range(100):
            node = {"label": "parent", "children": [node]}
        result = _search_tree(node, "target")
        assert result is None

    def test_depth_limit_finds_shallow(self):
        tree = {"label": "root", "children": [
            {"label": "shallow_target", "value": "found", "children": []},
        ]}
        result = _search_tree(tree, "shallow_target")
        assert result is not None

    def test_list_input(self):
        """列表节点：递归搜索每个元素。"""
        tree = [
            {"label": "a", "children": []},
            {"label": "", "value": "target_val", "children": []},
        ]
        result = _search_tree(tree, "target_val")
        assert result is not None
        assert result["value"] == "target_val"


# ------------------------------------------------------------------
# IOSDeviceManager 命令拼装
# ------------------------------------------------------------------

class TestIOSDeviceManagerCommands:
    @pytest.fixture
    def mgr(self):
        return IOSDeviceManager(idb_path="idb")

    def test_idb_path_default(self):
        m = IOSDeviceManager()
        assert m._idb == "idb"

    def test_idb_path_custom(self):
        m = IOSDeviceManager(idb_path="/usr/local/bin/idb")
        assert m._idb == "/usr/local/bin/idb"

    @patch("subprocess.run")
    def test_check_idb_available_ok(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="idb 1.2.3", stderr="")
        ok, msg = mgr.check_idb_available()
        assert ok is True
        assert "1.2.3" in msg

    @patch("subprocess.run")
    def test_check_idb_available_not_found(self, mock_run, mgr):
        mock_run.side_effect = FileNotFoundError
        ok, msg = mgr.check_idb_available()
        assert ok is False
        assert "未安装" in msg

    @patch("subprocess.run")
    def test_list_devices_json(self, mock_run, mgr):
        devices_json = json.dumps([
            {"udid": "ABC-123", "name": "iPhone 15", "os_version": "17.4", "type": "device"},
            {"udid": "DEF-456", "name": "iPad Pro", "os_version": "17.4", "type": "simulator"},
        ])
        mock_run.return_value = MagicMock(returncode=0, stdout=devices_json, stderr="")
        with patch.object(mgr, "check_idb_available", return_value=(True, "ok")):
            devices = mgr.list_devices()
        assert len(devices) == 2
        assert devices[0].udid == "ABC-123"
        assert devices[0].platform == "ios"
        assert devices[1].is_emulator is True

    @patch("subprocess.run")
    def test_launch_app_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, msg = mgr.launch_app("UDID-ABC", "com.example.app")
        assert ok is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args
        assert "UDID-ABC" in args
        assert "launch" in args
        assert "com.example.app" in args

    @patch("subprocess.run")
    def test_install_app_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, msg = mgr.install_app("UDID-ABC", "/path/to/app.ipa")
        assert ok is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args
        assert "install" in args

    @patch("subprocess.run")
    def test_uninstall_app_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, msg = mgr.uninstall_app("UDID-ABC", "com.example.app")
        assert ok is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args
        assert "uninstall" in args

    @patch("subprocess.run")
    def test_tap_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = mgr.tap("UDID-ABC", 100, 200)
        assert result is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args
        assert "UDID-ABC" in args
        assert "tap" in args

    @patch("subprocess.run")
    def test_long_press_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = mgr.long_press("UDID-ABC", 100, 200, duration_ms=2000)
        assert result is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args

    @patch("subprocess.run")
    def test_swipe_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = mgr.swipe("UDID-ABC", 100, 200, 300, 400)
        assert result is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args

    @patch("subprocess.run")
    def test_press_button_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = mgr.press_button("UDID-ABC", "HOME")
        assert result is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args

    @patch("subprocess.run")
    def test_capture_screenshot_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("pathlib.Path.exists", return_value=True):
            result = mgr.capture_screenshot("UDID-ABC", "/tmp/shot.png")
        assert result is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args

    @patch("subprocess.run")
    def test_get_accessibility_tree_passes_udid(self, mock_run, mgr):
        tree_json = json.dumps({"label": "root", "children": []})
        mock_run.return_value = MagicMock(returncode=0, stdout=tree_json, stderr="")
        tree = mgr.get_accessibility_tree("UDID-ABC")
        assert tree is not None
        args = mock_run.call_args[0][0]
        assert "--udid" in args
        assert "describe-all" in args

    @patch("subprocess.run")
    def test_input_text_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = mgr.input_text("UDID-ABC", "hello world")
        assert result is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args
        assert "hello world" in args

    @patch("subprocess.run")
    def test_clear_text_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = mgr.clear_text("UDID-ABC")
        assert result is True
        args = mock_run.call_args[0][0]
        assert "--udid" in args

    @patch("subprocess.run")
    def test_get_foreground_app_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="com.example.app", stderr="")
        result = mgr.get_foreground_app("UDID-ABC")
        assert result == "com.example.app"
        args = mock_run.call_args[0][0]
        assert "--udid" in args

    @patch("subprocess.run")
    def test_list_apps_passes_udid(self, mock_run, mgr):
        mock_run.return_value = MagicMock(returncode=0, stdout="com.app1\ncom.app2\n", stderr="")
        apps = mgr.list_apps("UDID-ABC")
        args = mock_run.call_args[0][0]
        assert "--udid" in args
        assert len(apps) == 2


# ------------------------------------------------------------------
# 模拟器管理
# ------------------------------------------------------------------

class TestSimulatorManagement:
    def test_list_simulators_non_macos(self):
        with patch("sys.platform", "win32"):
            sims = IOSDeviceManager.list_simulators()
            assert sims == []


# ------------------------------------------------------------------
# iOS 支持检查
# ------------------------------------------------------------------

class TestIOSSupport:
    def test_is_ios_supported_no_config(self):
        with patch.dict("sys.modules", {"mobile_env_config": None}):
            result = is_ios_supported()
            assert isinstance(result, bool)

    def test_check_ios_preflight_no_config(self):
        with patch.dict("sys.modules", {"mobile_env_config": None}):
            result = check_ios_preflight()
            assert "supported" in result
