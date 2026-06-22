# -*- coding: utf-8 -*-
"""移动端设备过滤、优先级与清理。"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_format_connect_error_unauthorized():
    from mobile_device_manager import format_connect_error

    msg = format_connect_error({"udid": "ABCD1234", "state": "unauthorized"})
    assert "尚未授权" in msg
    assert "ABCD1234" in msg


def test_format_connect_error_offline():
    from mobile_device_manager import format_connect_error

    msg = format_connect_error({"udid": "192.168.1.5:5555", "state": "offline"})
    assert "离线" in msg


def test_pick_default_real_device_skips_pq_prefix():
    from mobile_device_manager import pick_default_real_device

    devices = [
        {"udid": "PQFAKE001", "state": "device", "display_name": "Ghost"},
        {"udid": "REAL123456", "state": "device", "display_name": "MyPhone"},
    ]
    with patch("mobile_device_manager.list_real_usb_devices", return_value=devices):
        with patch("mobile_device_manager.get_device_info", return_value={"width": 1080, "height": 2400}):
            dev = pick_default_real_device()
    assert dev is not None
    assert dev.get("udid") == "REAL123456"


def test_score_device_priority_usb_over_pq():
    from mobile_device_manager import score_device_priority

    pq = score_device_priority({"udid": "PQ123", "state": "device"})
    usb = score_device_priority({"udid": "3B163L00CF800000", "state": "device"})
    assert usb > pq


def test_list_devices_for_ui_real_excludes_emulator():
    from mobile_device_manager import list_devices_for_ui

    with patch(
        "mobile_device_manager.list_usb_devices",
        return_value=[
            {"udid": "emulator-5554", "state": "device", "display_name": "Emu"},
            {"udid": "REAL001", "state": "device", "display_name": "Phone"},
        ],
    ):
        real = list_devices_for_ui("real")
    assert len(real) == 1
    assert real[0]["udid"] == "REAL001"


def test_should_prune_offline_and_pq():
    from mobile_device_manager import should_prune_device

    assert should_prune_device({"udid": "192.168.0.1:5555", "state": "offline"})
    assert should_prune_device({"udid": "PQGHOST", "state": "device"})
    assert not should_prune_device({"udid": "REAL001", "state": "device"})


def test_prune_stale_adb_devices_disconnects():
    from mobile_device_manager import prune_stale_adb_devices

    with patch(
        "mobile_device_manager.list_usb_devices",
        side_effect=[
            [{"udid": "PQGHOST", "state": "device"}, {"udid": "REAL001", "state": "device"}],
            [{"udid": "REAL001", "state": "device"}],
            [{"udid": "REAL001", "state": "device"}],
            [{"udid": "REAL001", "state": "device"}],
        ],
    ):
        with patch("mobile_device_manager.list_emulators", return_value=[]):
            with patch("mobile_device_manager.adb_disconnect_device", return_value=(True, "ok")) as disc:
                out = prune_stale_adb_devices()
    disc.assert_called_once_with("PQGHOST")
    assert len(out["pruned"]) == 1
    assert out["pruned"][0]["udid"] == "PQGHOST"
