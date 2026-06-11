# -*- coding: utf-8 -*-

from pathlib import Path

from web_capture.plugin_market import is_plugin_installed, prune_stale_plugin_records


def test_prune_removes_stale_mobile_record(tmp_path, monkeypatch):
    pt = tmp_path / "platform-tools"
    pt.mkdir()
    state_file = tmp_path / "installed_plugins.json"
    state_file.write_text(
        '{"plugins":{"mobile-android-platform-tools":{"type":"runtime_bundle","install_dir":"'
        + str(pt).replace("\\", "\\\\")
        + '"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mobile_plugin_bundles.android_tools_install_dir",
        lambda: pt,
    )
    monkeypatch.setattr("web_capture.plugin_market._state_path", lambda: state_file)
    n = prune_stale_plugin_records()
    assert n >= 1
    data = state_file.read_text(encoding="utf-8")
    assert "mobile-android-platform-tools" not in data


def test_platform_tools_requires_adb_not_empty_dir(tmp_path, monkeypatch):
    pt = tmp_path / "platform-tools"
    pt.mkdir()
    monkeypatch.setattr(
        "mobile_plugin_bundles.android_tools_install_dir",
        lambda: pt,
    )
    assert is_plugin_installed("mobile-android-platform-tools") is False
