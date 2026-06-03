# -*- coding: utf-8 -*-

from pathlib import Path

from mobile_emulator_sdk_bundles import (
    _missing_sdk_packages,
    _system_image_ready,
    android_sdk_install_dir,
)


def test_system_image_not_ready_with_installer_only(tmp_path):
    img = tmp_path / "system-images" / "android-34" / "google_apis" / "x86_64"
    img.mkdir(parents=True)
    (img / ".installer").mkdir()
    assert _system_image_ready(tmp_path) is False


def test_system_image_ready_with_package_xml(tmp_path):
    img = tmp_path / "system-images" / "android-34" / "google_apis" / "x86_64"
    img.mkdir(parents=True)
    (img / "package.xml").write_text("<sdk/>", encoding="utf-8")
    assert _system_image_ready(tmp_path) is True


def test_collect_urls_prefers_mirror_first():
    from mobile_emulator_sdk_bundles import _collect_download_urls

    urls = _collect_download_urls()
    if len(urls) >= 2:
        assert "mirrors.aliyun.com" in urls[0] or "mirrors.cloud.tencent.com" in urls[0]


def test_missing_packages_detects_platform_tools(tmp_path):
    (tmp_path / "emulator").mkdir()
    (tmp_path / "emulator" / "emulator.exe").write_bytes(b"")
    missing = _missing_sdk_packages(tmp_path)
    assert "platform-tools" in missing
