# -*- coding: utf-8 -*-

from pathlib import Path
from unittest.mock import patch

from mobile_emulator_sdk_bundles import (
    _manifest,
    _sanitize_env_value,
    _sdk_has_emulator,
    get_android_emulator_sdk_catalog_entry,
    resolve_adb_in_sdk,
)


def test_sanitize_env():
    assert _sanitize_env_value("# comment") == ""


def test_manifest_has_plugin_id():
    m = _manifest()
    assert m.get("id") == "mobile-android-emulator-sdk"
    assert m.get("default_avd", {}).get("name") == "Testory_Pixel7"


def test_catalog_entry():
    entry = get_android_emulator_sdk_catalog_entry()
    assert entry["id"] == "mobile-android-emulator-sdk"
    assert entry["type"] == "runtime_bundle"
    assert "Android 模拟器" in (entry.get("features") or [])


def test_sdk_has_emulator(tmp_path):
    exe = tmp_path / "emulator" / "emulator.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    with patch("mobile_emulator_sdk_bundles._platform_key", return_value="windows"):
        assert _sdk_has_emulator(tmp_path)


def test_resolve_adb_in_sdk(tmp_path):
    adb = tmp_path / "platform-tools" / "adb.exe"
    adb.parent.mkdir(parents=True)
    adb.write_bytes(b"")
    with patch("mobile_emulator_sdk_bundles._platform_key", return_value="windows"):
        assert resolve_adb_in_sdk(tmp_path) == str(adb.resolve())


def test_install_requires_java(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "mobile_emulator_sdk_bundles.android_sdk_install_dir",
        lambda: tmp_path,
    )
    with patch("mobile_emulator_sdk_bundles._resolve_java_exe", return_value=None):
        from mobile_emulator_sdk_bundles import install_android_emulator_sdk

        r = install_android_emulator_sdk()
        assert r["success"] is False
        assert "Java" in r.get("error", "") or "java" in r.get("error", "").lower()


def test_install_already_ready(monkeypatch, tmp_path):
    emu = tmp_path / "emulator" / "emulator.exe"
    emu.parent.mkdir(parents=True)
    emu.write_bytes(b"")
    adb = tmp_path / "platform-tools" / "adb.exe"
    adb.parent.mkdir(parents=True)
    adb.write_bytes(b"")
    monkeypatch.setattr(
        "mobile_emulator_sdk_bundles.android_sdk_install_dir",
        lambda: tmp_path,
    )
    with patch("mobile_emulator_sdk_bundles._resolve_java_exe", return_value="java"):
        with patch(
            "mobile_emulator_sdk_bundles.ensure_emulator_sdk_ready",
            return_value={"success": True, "message": "ok"},
        ):
            from mobile_emulator_sdk_bundles import install_android_emulator_sdk

            r = install_android_emulator_sdk()
            assert r["success"] is True


def test_plugin_in_catalog():
    from web_capture.plugin_market import _all_catalog_items

    ids = {p["id"] for p in _all_catalog_items()}
    assert "mobile-android-emulator-sdk" in ids


def test_avdmanager_cmd_has_no_sdk_root_flag(tmp_path):
    from mobile_emulator_sdk_bundles import _avdmanager_cmd

    mgr = tmp_path / "avdmanager.bat"
    mgr.write_bytes(b"")
    cmd = _avdmanager_cmd(mgr, "-n", "Testory_Pixel7", "--force")
    assert cmd == [str(mgr), "create", "avd", "-n", "Testory_Pixel7", "--force"]


def test_create_default_avd_uses_subcommand_sdk_root(tmp_path, monkeypatch):
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    mgr = sdk / "cmdline-tools" / "latest" / "bin" / "avdmanager.bat"
    mgr.parent.mkdir(parents=True)
    mgr.write_bytes(b"")
    captured = {}

    def fake_run(cmd, sdk_root, **kwargs):
        captured["cmd"] = cmd
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mobile_emulator_sdk_bundles._avd_exists", lambda _n: False)
    monkeypatch.setattr("mobile_emulator_sdk_bundles._run_subprocess", fake_run)
    from mobile_emulator_sdk_bundles import _create_default_avd

    name = _create_default_avd(sdk, {"name": "Testory_Pixel7"})
    assert name == "Testory_Pixel7"
    assert captured["cmd"][1:3] == ["create", "avd"]
    assert "--sdk_root" not in " ".join(captured["cmd"])
