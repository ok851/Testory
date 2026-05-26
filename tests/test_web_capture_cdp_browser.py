# -*- coding: utf-8 -*-

from unittest.mock import patch

from web_capture.cdp_browser import detect_browser_executable, pick_free_port


def test_pick_free_port():
    p = pick_free_port()
    assert 1024 < p < 65536


@patch("web_capture.cdp_browser._registry_browser_path", return_value=r"C:\Edge\msedge.exe")
def test_detect_edge_from_registry(mock_reg):
    assert detect_browser_executable("edge") == r"C:\Edge\msedge.exe"
    mock_reg.assert_called()
