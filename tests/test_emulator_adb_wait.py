# -*- coding: utf-8 -*-

from pathlib import Path

from mobile_emulator_manager import (
    _fatal_from_emulator_output,
    _wait_emulator_online,
)


def test_fatal_from_multiple_avd_log():
    text = (
        "INFO | ok\n"
        "FATAL | Running multiple emulators with the same AVD is an experimental feature."
    )
    msg = _fatal_from_emulator_output(text)
    assert msg and "后台进程" in msg


def test_wait_emulator_online_fails_fast_on_fatal_log(tmp_path, monkeypatch):
    log_path = tmp_path / "emu.log"
    log_path.write_text(
        "FATAL | Running multiple emulators with the same AVD is an experimental feature.\n",
        encoding="utf-8",
    )

    class _Proc:
        def poll(self):
            return 1

        stdout = None

    monkeypatch.setattr(
        "mobile_emulator_manager._serial_adb_state",
        lambda *a, **k: "",
    )
    ok, msg = _wait_emulator_online(
        "adb",
        "emulator-5554",
        _Proc(),
        {},
        timeout=5,
        log_path=Path(log_path),
    )
    assert ok is False
    assert "后台进程" in msg
