# -*- coding: utf-8 -*-
"""mobile_runner iOS 执行链测试（mock idb）。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ai_modules.execute.mobile_runner import run_mobile_case_steps


class TestRunMobileCaseStepsRouting:
    def test_android_default(self):
        with patch("ai_modules.execute.mobile_runner._run_android_steps", return_value=[{"ok": True}]) as mock:
            result = run_mobile_case_steps([{"action": "tap"}])
            mock.assert_called_once()
            assert result[0]["ok"] is True

    def test_ios_routing(self):
        with patch("ai_modules.execute.mobile_runner._run_ios_steps", return_value=[{"ok": True}]) as mock:
            result = run_mobile_case_steps([{"action": "tap"}], platform="ios", udid="ABC")
            mock.assert_called_once()

    def test_iphone_routing(self):
        with patch("ai_modules.execute.mobile_runner._run_ios_steps", return_value=[{"ok": True}]) as mock:
            result = run_mobile_case_steps([{"action": "tap"}], platform="iphone")
            mock.assert_called_once()


def _make_mock_mgr():
    mgr = MagicMock()
    mgr.list_devices.return_value = [MagicMock(udid="DEV-ABC")]
    mgr.check_device_readiness.return_value = {"all_passed": True}
    mgr.launch_app.return_value = (True, "ok")
    mgr.tap.return_value = True
    mgr.long_press.return_value = True
    mgr.swipe.return_value = True
    mgr.input_text.return_value = True
    mgr.clear_text.return_value = True
    mgr.press_button.return_value = True
    mgr.capture_screenshot.return_value = True
    mgr.get_accessibility_tree.return_value = {"label": "Login", "children": []}
    return mgr


class TestIOSStepExecution:
    def _run(self, steps, udid="DEV-ABC", mgr=None):
        """Helper: run iOS steps with mocked IOSDeviceManager."""
        if mgr is None:
            mgr = _make_mock_mgr()
        with patch("mobile_engine.device.ios_device.IOSDeviceManager", return_value=mgr):
            return run_mobile_case_steps(steps, platform="ios", udid=udid)

    def test_launch_app(self):
        results = self._run([{"action": "launch", "bundle_id": "com.example.app"}])
        assert results[0]["ok"] is True

    def test_tap(self):
        results = self._run([{"action": "tap", "x": 100, "y": 200}])
        assert results[0]["ok"] is True

    def test_input_text(self):
        results = self._run([{"action": "input_text", "input_value": "hello"}])
        assert results[0]["ok"] is True

    def test_screenshot(self):
        results = self._run([{"action": "screenshot", "output_path": "/tmp/shot.png"}])
        assert results[0]["ok"] is True
        assert results[0]["screenshot_path"] == "/tmp/shot.png"

    def test_assert_found(self):
        results = self._run([{"action": "assert", "input_value": "Login"}])
        assert results[0]["ok"] is True

    def test_assert_not_found(self):
        mgr = _make_mock_mgr()
        mgr.get_accessibility_tree.return_value = {"label": "other", "children": []}
        results = self._run([{"action": "assert", "input_value": "Nonexistent"}], mgr=mgr)
        assert results[0]["ok"] is False
        assert results[0]["error_code"] == "IOS_ASSERT_FAILED"

    def test_unsupported_action(self):
        results = self._run([{"action": "drag_and_drop"}])
        assert results[0]["ok"] is False
        assert "不支持" in results[0]["error"]

    def test_no_device(self):
        mgr = _make_mock_mgr()
        mgr.list_devices.return_value = []
        with patch("mobile_engine.device.ios_device.IOSDeviceManager", return_value=mgr):
            results = run_mobile_case_steps([{"action": "tap"}], platform="ios")
        assert results[0]["ok"] is False
        assert results[0]["error_code"] == "IOS_NO_DEVICE"

    def test_preflight_failed(self):
        mgr = _make_mock_mgr()
        mgr.check_device_readiness.return_value = {"all_passed": False, "errors": ["idb 不可用"]}
        results = self._run([{"action": "tap"}], mgr=mgr)
        assert results[0]["ok"] is False
        assert results[0]["error_code"] == "IOS_PREFLIGHT_FAILED"

    def test_step_failure_stops_execution(self):
        mgr = _make_mock_mgr()
        mgr.tap.return_value = False
        steps = [
            {"action": "tap", "x": 100, "y": 200},
            {"action": "input_text", "input_value": "hello"},
        ]
        results = self._run(steps, mgr=mgr)
        assert len(results) == 1
        assert results[0]["ok"] is False

    def test_continue_on_failure(self):
        mgr = _make_mock_mgr()
        mgr.tap.return_value = False
        steps = [
            {"action": "tap", "x": 100, "y": 200, "continue_on_failure": True},
            {"action": "input_text", "input_value": "hello"},
        ]
        results = self._run(steps, mgr=mgr)
        assert len(results) == 2
        assert results[0]["ok"] is False
        assert results[1]["ok"] is True

    def test_multi_step_sequence(self):
        steps = [
            {"action": "launch", "bundle_id": "com.example"},
            {"action": "tap", "x": 100, "y": 200},
            {"action": "input_text", "input_value": "user@test.com"},
            {"action": "screenshot", "output_path": "/tmp/result.png"},
        ]
        results = self._run(steps)
        assert len(results) == 4
        assert all(r["ok"] for r in results)

    def test_swipe(self):
        results = self._run([{"action": "swipe", "x1": 100, "y1": 200, "x2": 300, "y2": 400}])
        assert results[0]["ok"] is True

    def test_press_button(self):
        results = self._run([{"action": "press_button", "button": "HOME"}])
        assert results[0]["ok"] is True

    def test_clear_text(self):
        results = self._run([{"action": "clear_text"}])
        assert results[0]["ok"] is True

    def test_wait(self):
        results = self._run([{"action": "wait", "seconds": 0.01}])
        assert results[0]["ok"] is True
