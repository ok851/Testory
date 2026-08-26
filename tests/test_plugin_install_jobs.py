# -*- coding: utf-8 -*-

from modules.core.plugin_install_jobs import should_install_in_background


def test_background_plugin_ids():
    assert should_install_in_background("mobile-android-platform-tools")
    assert not should_install_in_background("web-capture-chrome")
