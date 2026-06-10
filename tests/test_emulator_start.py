# -*- coding: utf-8 -*-

from mobile_emulator_manager import (
    _disk_space_preflight,
    _parse_emulator_fatal,
    _gpu_modes_for_start,
)


def test_parse_emulator_disk_fatal():
    out = "FATAL | Not enough space to create userdata partition."
    msg = _parse_emulator_fatal(out)
    assert "磁盘空间" in msg


def test_gpu_modes_dedup():
    modes = _gpu_modes_for_start("host", no_window=True)
    assert modes[0] == "swiftshader_indirect"
    assert "host" in modes


def test_gpu_modes_windowed_keeps_host_first():
    modes = _gpu_modes_for_start("host", no_window=False)
    assert modes[0] == "host"


def test_resolve_emulator_gpu_headless_windows(monkeypatch):
    from mobile_env_config import resolve_emulator_gpu

    monkeypatch.delenv("MOBILE_EMULATOR_GPU", raising=False)
    monkeypatch.setattr("os.name", "nt", raising=False)
    assert resolve_emulator_gpu("host", no_window=True) == "swiftshader_indirect"
    assert resolve_emulator_gpu("", no_window=True) == "swiftshader_indirect"
    monkeypatch.setenv("MOBILE_EMULATOR_GPU", "angle_indirect")
    assert resolve_emulator_gpu("host", no_window=True) == "angle_indirect"


def test_accel_check_message_when_hypervisor_missing(monkeypatch):
    monkeypatch.setattr("mobile_emulator_manager._hypervisor_service_running", lambda: False)
    monkeypatch.setattr(
        "mobile_emulator_manager.subprocess.run",
        lambda *a, **k: type("P", (), {"returncode": 6, "stdout": "not installed", "stderr": ""})(),
    )
    from mobile_emulator_manager import _accel_check_message

    msg = _accel_check_message("emulator.exe", {})
    assert msg and "Hypervisor" in msg


def test_disk_preflight_returns_message_when_low(monkeypatch, tmp_path):
    avd_dir = tmp_path / "Testory_Pixel7.avd"
    avd_dir.mkdir()
    (avd_dir / "config.ini").write_text(
        "disk.dataPartition.size=8000M\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANDROID_AVD_HOME", str(tmp_path))

    class TinyUsage:
        free = 100 * 1024 * 1024

    monkeypatch.setattr("mobile_emulator_manager.shutil.disk_usage", lambda _p: TinyUsage())
    err = _disk_space_preflight("Testory_Pixel7")
    assert err and "磁盘空间不足" in err
