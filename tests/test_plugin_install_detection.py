# -*- coding: utf-8 -*-

from pathlib import Path

from web_capture.plugin_market import is_plugin_installed, prune_stale_plugin_records


def test_emulator_sdk_not_installed_when_only_empty_dir(tmp_path, monkeypatch):
    sdk = tmp_path / "android" / "sdk"
    sdk.mkdir(parents=True)
    state_file = tmp_path / "installed_plugins.json"
    state_file.write_text(
        '{"plugins":{"mobile-android-emulator-sdk":{"type":"runtime_bundle",'
        f'"install_dir":"{sdk.as_posix().replace(chr(92), "/")}"'
        "}}}",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mobile_emulator_sdk_bundles.android_sdk_install_dir",
        lambda: sdk,
    )
    monkeypatch.setattr("web_capture.plugin_market._state_path", lambda: state_file)
    assert is_plugin_installed("mobile-android-emulator-sdk") is False


def test_emulator_sdk_installed_when_emulator_present(tmp_path, monkeypatch):
    sdk = tmp_path / "android" / "sdk"
    exe = sdk / "emulator" / "emulator.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(
        "mobile_emulator_sdk_bundles.android_sdk_install_dir",
        lambda: sdk,
    )
    assert is_plugin_installed("mobile-android-emulator-sdk") is True


def test_prune_removes_stale_mobile_record(tmp_path, monkeypatch):
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    state_file = tmp_path / "installed_plugins.json"
    state_file.write_text(
        '{"plugins":{"mobile-android-emulator-sdk":{"type":"runtime_bundle","install_dir":"'
        + str(sdk).replace("\\", "\\\\")
        + '"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mobile_emulator_sdk_bundles.android_sdk_install_dir",
        lambda: sdk,
    )
    monkeypatch.setattr("web_capture.plugin_market._state_path", lambda: state_file)
    n = prune_stale_plugin_records()
    assert n >= 1
    data = state_file.read_text(encoding="utf-8")
    assert "mobile-android-emulator-sdk" not in data


def test_emulator_sdk_repair_needed_without_avd(tmp_path, monkeypatch):
    sdk = tmp_path / "sdk"
    emu = sdk / "emulator" / "emulator.exe"
    emu.parent.mkdir(parents=True)
    emu.write_bytes(b"")
    img = sdk / "system-images" / "android-34" / "google_apis" / "x86_64"
    img.mkdir(parents=True)
    (img / "package.xml").write_text("<sdk>", encoding="utf-8")
    monkeypatch.setattr(
        "mobile_emulator_sdk_bundles.android_sdk_install_dir",
        lambda: sdk,
    )
    monkeypatch.setattr("mobile_emulator_sdk_bundles._avd_exists", lambda _n: False)
    from mobile_emulator_sdk_bundles import emulator_sdk_setup_status

    st = emulator_sdk_setup_status()
    assert st["sdk_ready"] is True
    assert st["setup_complete"] is False

    from web_capture.plugin_market import enrich_plugin_status

    out = enrich_plugin_status(
        {"id": "mobile-android-emulator-sdk", "type": "runtime_bundle", "category": "mobile"}
    )
    assert out["repair_needed"] is True
    assert out["status_label"] == "待创建虚拟手机"


def test_platform_tools_requires_adb_not_empty_dir(tmp_path, monkeypatch):
    pt = tmp_path / "platform-tools"
    pt.mkdir()
    monkeypatch.setattr(
        "mobile_plugin_bundles.android_tools_install_dir",
        lambda: pt,
    )
    assert is_plugin_installed("mobile-android-platform-tools") is False
