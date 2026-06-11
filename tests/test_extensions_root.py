# -*- coding: utf-8 -*-

from pathlib import Path


def test_software_extensions_root_prefers_uat_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTORY_EXTENSIONS_ROOT", raising=False)
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from web_capture.plugin_market import software_extensions_root

    root = software_extensions_root()
    assert root == tmp_path / "extensions"
    assert root.is_dir()


def test_software_extensions_root_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom_ext"
    monkeypatch.setenv("TESTORY_EXTENSIONS_ROOT", str(custom))
    from web_capture.plugin_market import software_extensions_root

    assert software_extensions_root() == custom
    assert custom.is_dir()


def test_legacy_extensions_migrated_once(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTORY_EXTENSIONS_ROOT", raising=False)
    monkeypatch.delenv("UAT_DATA_DIR", raising=False)
    local = tmp_path / "LocalAppData"
    legacy = local / "NewUITestPlatform" / "extensions"
    legacy.mkdir(parents=True)
    (legacy / "android").mkdir()
    (legacy / "android" / "marker.txt").write_text("legacy", encoding="utf-8")

    target = local / "Testory" / "extensions"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr("sys.frozen", False, raising=False)

    from web_capture import plugin_market

    plugin_market._maybe_migrate_legacy_extensions(target)
    assert (target / "android" / "marker.txt").read_text(encoding="utf-8") == "legacy"
    marker = target.parent / ".extensions_migrated"
    assert marker.is_file()

    (legacy / "android" / "marker.txt").write_text("changed", encoding="utf-8")
    plugin_market._maybe_migrate_legacy_extensions(target)
    assert (target / "android" / "marker.txt").read_text(encoding="utf-8") == "legacy"


def test_android_tools_install_dir_uses_extensions_root(monkeypatch, tmp_path):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TESTORY_EXTENSIONS_ROOT", raising=False)
    from mobile_plugin_bundles import android_tools_install_dir

    assert android_tools_install_dir() == tmp_path / "extensions" / "android" / "platform-tools"
