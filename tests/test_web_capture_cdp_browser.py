# -*- coding: utf-8 -*-

from unittest.mock import patch

from web_capture.cdp_browser import (
    detect_browser_executable,
    pick_free_port,
    _is_blank_page_url,
    _is_idle_startup_page,
    close_blank_cdp_targets,
    close_idle_or_non_target_tabs,
)


def test_pick_free_port():
    p = pick_free_port()
    assert 1024 < p < 65536


@patch("web_capture.cdp_browser._registry_browser_path", return_value=r"C:\Edge\msedge.exe")
def test_detect_edge_from_registry(mock_reg):
    assert detect_browser_executable("edge") == r"C:\Edge\msedge.exe"
    mock_reg.assert_called()


def test_is_blank_page_url():
    assert _is_blank_page_url("about:blank")
    assert _is_blank_page_url("chrome://newtab/")
    assert not _is_blank_page_url("https://example.com/login")


def test_is_idle_startup_page_includes_edge_ntp():
    # Edge 新标签页看起来像正常网页，但不是业务导航
    assert _is_idle_startup_page("https://ntp.msn.com/edge/ntp?locale=zh-cn")
    assert _is_idle_startup_page("https://ntp.msn.cn/edge/ntp")
    assert _is_idle_startup_page("chrome://new-tab-page/")
    assert not _is_idle_startup_page("https://admin.hypaas.com/")


def test_close_blank_cdp_targets_no_port():
    assert close_blank_cdp_targets(0) == 0
    assert close_idle_or_non_target_tabs(0, target_url="https://example.com") == 0
