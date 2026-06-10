# -*- coding: utf-8 -*-

from unittest.mock import MagicMock, patch

from mobile_emulator_manager import (
    _wait_boot_completed,
    _wait_serial_gone,
    start_avd,
)


def test_wait_boot_completed_accepts_dev_bootcomplete(monkeypatch):
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        prop = cmd[-1] if cmd else ""
        if prop == "dev.bootcomplete" and calls["n"] >= 2:
            return MagicMock(stdout="1", returncode=0)
        return MagicMock(stdout="0", returncode=0)

    monkeypatch.setattr("mobile_emulator_manager._resolve_adb", lambda: "adb")
    monkeypatch.setattr("mobile_emulator_manager._emulator_env", lambda: {})
    monkeypatch.setattr("mobile_emulator_manager._serial_adb_state", lambda *a, **k: "device")
    monkeypatch.setattr("mobile_emulator_manager.subprocess.run", _run)
    ok, msg = _wait_boot_completed("emulator-5554", timeout=5)
    assert ok is True
    assert "完成" in msg


def test_wait_serial_gone_when_not_device(monkeypatch):
    monkeypatch.setattr(
        "mobile_emulator_manager._serial_adb_state",
        lambda *a, **k: "",
    )
    assert _wait_serial_gone("adb", "emulator-5554", {}, timeout=3) is True


def test_start_avd_recovers_from_stale_running_emulator(monkeypatch):
    boot_calls = {"n": 0}

    def _boot(serial, timeout=120, progress_cb=None):
        boot_calls["n"] += 1
        if boot_calls["n"] == 1:
            return False, "boot timeout"
        return True, "启动完成"

    progress: list[tuple[int, str]] = []

    def _progress(pct, label):
        progress.append((pct, label))

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.pid = 4242
    fake_proc.stdout = None

    monkeypatch.setattr("mobile_emulator_manager.emulator_available", lambda: (True, "emulator.exe"))
    monkeypatch.setattr("mobile_emulator_manager._disk_space_preflight", lambda _n: None)
    monkeypatch.setattr("mobile_emulator_manager._accel_check_message", lambda *a: None)
    monkeypatch.setattr("mobile_emulator_manager._resolve_adb", lambda: "adb")
    monkeypatch.setattr("mobile_emulator_manager._emulator_env", lambda: {})
    monkeypatch.setattr("mobile_emulator_manager._cleanup_stale_emulators", lambda *a, **k: None)
    monkeypatch.setattr("mobile_emulator_manager._ensure_emulator_slot_free", lambda *a, **k: None)
    monkeypatch.setattr("mobile_emulator_manager._ensure_adb_server", lambda *a, **k: None)
    monkeypatch.setattr("mobile_emulator_manager._serial_adb_state", lambda *a, **k: "device")
    monkeypatch.setattr("mobile_emulator_manager._wait_serial_ready", lambda *a, **k: True)
    monkeypatch.setattr("mobile_emulator_manager._wait_boot_completed", _boot)
    monkeypatch.setattr("mobile_emulator_manager._force_stop_serial", lambda *a, **k: None)
    monkeypatch.setattr("mobile_emulator_manager._wait_serial_gone", lambda *a, **k: True)
    monkeypatch.setattr("mobile_emulator_manager._reset_adb_server", lambda *a, **k: None)
    monkeypatch.setattr(
        "mobile_emulator_manager._wait_emulator_online",
        lambda *a, **k: (True, ""),
    )
    monkeypatch.setattr("mobile_emulator_manager.subprocess.Popen", lambda *a, **k: fake_proc)

    ok, msg, meta = start_avd(
        "Testory_Pixel7",
        no_window=True,
        progress_cb=_progress,
    )
    assert ok is True
    assert meta.get("serial") == "emulator-5554"
    assert boot_calls["n"] == 2
    assert any("清理" in label for _, label in progress)
