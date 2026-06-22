# -*- coding: utf-8 -*-

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_capture.plugin_market import _all_catalog_items, install_plugin


def test_mobile_plugin_in_full_catalog():
    ids = {p["id"] for p in _all_catalog_items()}
    assert "mobile-android-platform-tools" in ids
    assert "mobile-testory-assistant" in ids
    assert "mobile-scrcpy" in ids


def test_install_unknown_id_error_message():
    r = install_plugin("not-a-real-plugin-id")
    assert r["success"] is False
    assert "未知插件" in r.get("error", "")
