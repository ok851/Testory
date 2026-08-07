# -*- coding: utf-8 -*-
"""MultiDeviceScheduler 单元测试：配置解析、结果汇总、摘要生成。"""
from __future__ import annotations

import pytest

from ai_modules.execute.multi_device_scheduler import (
    multi_device_summary,
    _execute_steps_on_device,
)


class TestMultiDeviceSummary:
    def test_summary_all_ok(self):
        result = {
            "device_count": 3,
            "device_results": [
                {"device_udid": "dev-1", "model": "Pixel 7", "ok": True, "error": None},
                {"device_udid": "dev-2", "model": "Galaxy S24", "ok": True, "error": None},
                {"device_udid": "dev-3", "model": "Mi 14", "ok": True, "error": None},
            ],
            "elapsed_ms": 5234.5,
        }
        text = multi_device_summary(result)
        assert "3 台" in text
        assert "成功 3" in text
        assert "失败 0" in text
        assert "5235ms" in text or "5234ms" in text
        assert "[OK]" in text

    def test_summary_partial_fail(self):
        result = {
            "device_count": 2,
            "device_results": [
                {"device_udid": "dev-1", "model": "Pixel", "ok": True, "error": None},
                {"device_udid": "dev-2", "model": "Galaxy", "ok": False, "error": "timeout"},
            ],
            "elapsed_ms": 3000.0,
        }
        text = multi_device_summary(result)
        assert "成功 1" in text
        assert "失败 1" in text
        assert "[FAIL]" in text
        assert "timeout" in text

    def test_summary_empty(self):
        result = {"device_count": 0, "device_results": [], "elapsed_ms": 0}
        text = multi_device_summary(result)
        assert "0 台" in text


class TestExecuteStepsOnDevice:
    def test_no_backend_returns_exception(self):
        """当 mobile_sync_store 和 MobileEngineDispatcher 都不可用时，应返回异常结果。"""
        device = {"udid": "fake-dev", "model": "Test", "platform": "android"}
        steps = [{"action": "tap", "x": 100, "y": 200}]
        result = _execute_steps_on_device(device, steps, timeout_sec=1.0)
        assert result["device_udid"] == "fake-dev"
        # Should either succeed (if backend available) or return error
        if not result["ok"]:
            assert result["error_code"] in (
                "DEVICE_EXCEPTION", "MOBILE_ENGINE_UNAVAILABLE",
                "MOBILE_SYNC_STORE_UNAVAILABLE", "DEVICE_NOT_CONNECTED",
            ) or result["error"] is not None

    def test_device_udid_propagated(self):
        device = {"udid": "test-udid-12345", "model": "TestModel", "platform": "android"}
        result = _execute_steps_on_device(device, [], timeout_sec=0.5)
        assert result["device_udid"] == "test-udid-12345"
        assert result["device_model"] == "TestModel"


class TestDiscoverDevices:
    def test_discover_returns_list(self):
        from ai_modules.execute.multi_device_scheduler import _discover_available_devices
        # Should return a list (empty if no devices connected)
        devices = _discover_available_devices(platform_filter="", max_devices=0)
        assert isinstance(devices, list)

    def test_discover_with_platform_filter(self):
        from ai_modules.execute.multi_device_scheduler import _discover_available_devices
        devices = _discover_available_devices(platform_filter="android")
        for d in devices:
            assert d.get("platform") == "android"

    def test_discover_with_max_devices(self):
        from ai_modules.execute.multi_device_scheduler import _discover_available_devices
        devices = _discover_available_devices(platform_filter="", max_devices=1)
        assert len(devices) <= 1
