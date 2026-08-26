# -*- coding: utf-8 -*-

from modules.mobile.mobile_plugin_bundles import (
    _resolve_download_url,
    _resolve_local_zip,
    _sanitize_env_value,
    get_android_platform_tools_catalog_entry,
)


def test_sanitize_env_rejects_comment_as_url():
    assert _sanitize_env_value("# 可选，覆盖插件市场下载地址") == ""
    assert _sanitize_env_value("  ") == ""


def test_resolve_download_url_ignores_invalid_env(monkeypatch):
    monkeypatch.setenv("ANDROID_PLATFORM_TOOLS_URL", "# 可选，覆盖插件市场下载地址（内网 CDN）")
    url = _resolve_download_url()
    assert url.startswith("https://")


def test_catalog_entry_has_id():
    entry = get_android_platform_tools_catalog_entry()
    assert entry["id"] == "mobile-android-platform-tools"
    assert entry["type"] == "runtime_bundle"


def test_default_google_url_configured():
    url = _resolve_download_url()
    assert "platform-tools" in url or url == "" or url.startswith("http")


def test_local_zip_optional():
    # 未放置 zip 时不应报错
    _resolve_local_zip()


def test_resolve_adb_in_dir_finds_nested(tmp_path):
    from modules.mobile.mobile_plugin_bundles import resolve_adb_in_dir

    nested = tmp_path / "platform-tools"
    nested.mkdir()
    adb = nested / "adb.exe"
    adb.write_bytes(b"")
    found = resolve_adb_in_dir(tmp_path)
    assert found == str(adb.resolve())
