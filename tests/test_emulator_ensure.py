# -*- coding: utf-8 -*-

from unittest.mock import MagicMock

from mobile_emulator_manager import ensure_emulator_for_preset


def test_ensure_emulator_reuses_running_without_restart(monkeypatch):
    attach_meta = {"serial": "emulator-5554", "avd_name": "Testory_Pixel7", "reused": True}
    calls = {"attach": 0, "start": 0}

    def _attach(*args, **kwargs):
        calls["attach"] += 1
        return True, "已复用", attach_meta

    def _start(*args, **kwargs):
        calls["start"] += 1
        return False, "不应冷启动", {}

    monkeypatch.setattr("mobile_emulator_manager.try_attach_running_emulator", _attach)
    monkeypatch.setattr("mobile_emulator_manager.start_avd", _start)

    ok, msg, meta = ensure_emulator_for_preset("pixel_7", force_restart=False)
    assert ok is True
    assert meta.get("reused") is True
    assert calls["attach"] == 1
    assert calls["start"] == 0


def test_ensure_emulator_force_restart_skips_attach(monkeypatch):
    calls = {"attach": 0}

    monkeypatch.setattr(
        "mobile_emulator_manager.try_attach_running_emulator",
        lambda *a, **k: (calls.__setitem__("attach", calls["attach"] + 1) or (True, "reuse", {})),
    )
    monkeypatch.setattr("mobile_emulator_manager.list_running_emulators", lambda: [])
    monkeypatch.setattr("mobile_emulator_manager._ensure_emulator_slot_free", lambda *a, **k: None)
    monkeypatch.setattr(
        "mobile_emulator_manager.provision_avd_for_preset",
        lambda _p: (True, "Testory_Pixel7", "ok"),
    )
    monkeypatch.setattr(
        "mobile_emulator_manager.start_avd",
        lambda *a, **k: (True, "started", {"serial": "emulator-5554"}),
    )
    monkeypatch.setattr("mobile_emulator_manager.get_preset_by_id", lambda _p: {
        "id": "pixel_7",
        "label": "Pixel 7",
        "avd_name_hint": "Testory_Pixel7",
    })
    monkeypatch.setattr("mobile_emulator_manager.frame_preset_for_model", lambda _p: "pixel_7")

    ok, msg, meta = ensure_emulator_for_preset("pixel_7", force_restart=True)
    assert ok is True
    assert calls["attach"] == 0
