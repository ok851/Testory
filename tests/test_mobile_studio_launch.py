# -*- coding: utf-8 -*-

from mobile_studio_launch import launch_emulator_studio


def test_launch_emulator_studio_provisions_and_starts(monkeypatch):
    calls = {"provision": 0, "ensure": 0, "wait": 0}

    monkeypatch.setattr(
        "mobile_emulator_manager.emulator_status",
        lambda: {"emulator_available": True},
    )
    monkeypatch.setattr(
        "mobile_emulator_manager.provision_avd_for_preset",
        lambda _p: (calls.__setitem__("provision", calls["provision"] + 1) or (True, "Testory_Pixel7", "ok")),
    )
    monkeypatch.setattr(
        "mobile_emulator_manager.ensure_emulator_for_preset",
        lambda *a, **k: (
            calls.__setitem__("ensure", calls["ensure"] + 1)
            or (True, "started", {"serial": "emulator-5554", "reused": True})
        ),
    )
    monkeypatch.setattr(
        "mobile_emulator_manager.wait_emulator_mirror_ready",
        lambda *a, **k: (calls.__setitem__("wait", calls["wait"] + 1) or (True, "ok")),
    )
    monkeypatch.setattr(
        "mobile_emulator_manager.get_preset_by_id",
        lambda _p: {"id": "pixel_7", "label": "Pixel 7", "avd_name_hint": "Testory_Pixel7"},
    )
    monkeypatch.setattr(
        "mobile_emulator_manager.frame_preset_for_model",
        lambda _p: "pixel_7",
    )
    monkeypatch.setattr(
        "mobile_emulator_manager._serial_for_port",
        lambda _p: "emulator-5554",
    )

    ok, msg, meta = launch_emulator_studio("pixel_7")
    assert ok is True
    assert meta["serial"] == "emulator-5554"
    assert calls["provision"] == 1
    assert calls["ensure"] == 1
    assert calls["wait"] == 1
