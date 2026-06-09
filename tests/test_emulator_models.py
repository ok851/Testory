# -*- coding: utf-8 -*-

from unittest.mock import patch

from mobile_emulator_manager import (
    EMULATOR_AVD_PRESETS,
    avd_exists,
    frame_preset_for_model,
    get_preset_by_id,
    list_emulator_models,
    provision_avd_for_preset,
)


def test_frame_preset_mapping():
    assert frame_preset_for_model("pixel_7") == "pixel_7"
    assert frame_preset_for_model("samsung_s23") == "samsung_s23"
    assert frame_preset_for_model("tablet_10") == "ipad_mini"
    assert frame_preset_for_model("unknown") == "generic_19_9"


def test_get_preset_by_id():
    p = get_preset_by_id("pixel_7")
    assert p is not None
    assert p.get("avd_name_hint") == "Testory_Pixel7"
    assert get_preset_by_id("not_a_phone") is None


def test_list_emulator_models_fields(monkeypatch):
    monkeypatch.setattr("mobile_emulator_manager.list_avds", lambda: [])
    monkeypatch.setattr("mobile_emulator_manager.list_running_emulators", lambda: [])
    models = list_emulator_models()
    assert len(models) == len(EMULATOR_AVD_PRESETS)
    first = models[0]
    assert first["id"] == "pixel_7"
    assert "avd_exists" in first
    assert "frame_preset_id" in first
    assert first["frame_preset_id"] == "pixel_7"


def test_avd_exists(monkeypatch):
    monkeypatch.setattr(
        "mobile_emulator_manager.list_avds",
        lambda: [{"name": "Testory_Pixel7", "label": "Pixel 7"}],
    )
    assert avd_exists("Testory_Pixel7") is True
    assert avd_exists("Missing") is False


def test_provision_avd_unknown_preset():
    ok, name, msg = provision_avd_for_preset("invalid")
    assert ok is False
    assert not name
    assert "未知" in msg


def test_provision_avd_already_exists(monkeypatch):
    monkeypatch.setattr("mobile_emulator_manager.avd_exists", lambda _n: True)
    ok, name, msg = provision_avd_for_preset("pixel_7")
    assert ok is True
    assert name == "Testory_Pixel7"
    assert "已存在" in msg


def test_provision_avd_creates_when_missing(monkeypatch):
    monkeypatch.setattr("mobile_emulator_manager.avd_exists", lambda _n: False)

    def _fake_create(preset):
        assert preset.get("id") == "pixel_7"
        return "Testory_Pixel7"

    monkeypatch.setattr(
        "mobile_emulator_sdk_bundles.create_avd_for_preset",
        _fake_create,
    )
    ok, name, msg = provision_avd_for_preset("pixel_7")
    assert ok is True
    assert name == "Testory_Pixel7"
    assert "创建" in msg


def test_emulator_diagnostics_includes_checks(monkeypatch):
    monkeypatch.setattr("mobile_emulator_manager.emulator_status", lambda: {
        "emulator_available": True,
        "sdk_ready": True,
        "avd_ready": False,
        "setup_hint": "",
        "hypervisor_ok": True,
        "android_sdk_home": "/sdk",
    })
    monkeypatch.setattr("mobile_env_config.mobile_enabled", lambda: True)
    with patch("mobile_scrcpy_bridge.bridge_health", return_value={"ok": True, "message": "ok"}):
        with patch("mobile_emulator_sdk_bundles._resolve_java_exe", return_value="java"):
            from mobile_emulator_manager import emulator_diagnostics

            diag = emulator_diagnostics()
    assert diag.get("blocking_reason")
    checks = diag.get("checks") or []
    ids = {c["id"] for c in checks}
    assert "sdk" in ids
    assert "avd" in ids
    assert any(c["id"] == "avd" and not c["ok"] for c in checks)
