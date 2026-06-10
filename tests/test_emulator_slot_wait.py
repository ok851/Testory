# -*- coding: utf-8 -*-

from mobile_emulator_manager import ensure_emulator_for_preset


def test_ensure_emulator_waits_when_slot_busy(monkeypatch):
    attach_meta = {"serial": "emulator-5554", "avd_name": "Testory_Pixel7", "reused": True}
    calls = {"wait": 0, "start": 0}

    monkeypatch.setattr(
        "mobile_emulator_manager.try_attach_running_emulator",
        lambda *a, **k: (False, "not ready", {}),
    )
    monkeypatch.setattr("mobile_emulator_manager._emulator_slot_busy", lambda *a, **k: True)
    monkeypatch.setattr(
        "mobile_emulator_manager._wait_existing_emulator_boot",
        lambda *a, **k: (calls.__setitem__("wait", calls["wait"] + 1) or (True, "ok", attach_meta)),
    )
    monkeypatch.setattr(
        "mobile_emulator_manager.start_avd",
        lambda *a, **k: (calls.__setitem__("start", calls["start"] + 1) or (True, "started", {})),
    )
    monkeypatch.setattr("mobile_emulator_manager.get_preset_by_id", lambda _p: {
        "id": "pixel_7",
        "label": "Pixel 7",
        "avd_name_hint": "Testory_Pixel7",
    })
    monkeypatch.setattr("mobile_emulator_manager.frame_preset_for_model", lambda _p: "pixel_7")

    ok, msg, meta = ensure_emulator_for_preset("pixel_7", force_restart=False)
    assert ok is True
    assert calls["wait"] == 1
    assert calls["start"] == 0
    assert meta.get("reused") is True
