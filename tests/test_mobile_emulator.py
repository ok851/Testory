# -*- coding: utf-8 -*-
from unittest.mock import patch

from mobile_emulator_manager import EMULATOR_AVD_PRESETS, is_emulator_serial, parse_avd_create_command


def test_is_emulator_serial():
    assert is_emulator_serial("emulator-5554")
    assert not is_emulator_serial("192.168.1.5:5555")


def test_avd_presets():
    assert len(EMULATOR_AVD_PRESETS) >= 3
    cmd = parse_avd_create_command("pixel_7")
    assert cmd and "avdmanager create avd" in cmd


@patch("mobile_emulator_manager.emulator_exe", return_value=None)
def test_emulator_unavailable(_mock):
    from mobile_emulator_manager import emulator_available

    ok, msg = emulator_available()
    assert ok is False
    assert "ANDROID" in msg or "Emulator" in msg
