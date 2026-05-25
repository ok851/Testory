# -*- coding: utf-8 -*-
"""desktop_precise_locator 单元测试。"""

import json

from desktop_precise_locator import (
    enrich_desktop_spec_for_precise_run,
    format_uia_path_for_display,
    is_misbound_overlay_spec,
    synthesize_desktop_icon_uia_path,
    uia_path_from_locator_candidates,
)


def test_misbound_settings_spec():
    spec = {
        "process": "ApplicationFrameHost.exe",
        "class_name": "ApplicationFrameWindow",
        "target_name": "FolderView",
        "hwnd": 12345,
    }
    assert is_misbound_overlay_spec(spec) is True


def test_enrich_coordinate_notepad_case():
    spec = {
        "process": "ApplicationFrameHost.exe",
        "hwnd": 999,
        "target_name": "FolderView",
    }
    out = enrich_desktop_spec_for_precise_run(
        spec,
        selector_type="coordinate",
        case_name="打开记事本",
    )
    assert out.get("surface") == "desktop_shell"
    assert "hwnd" not in out
    path = out.get("uia_path")
    assert isinstance(path, list)
    assert any(
        n.get("control_type") == "ListItem" and n.get("name") == "记事本"
        for n in path
    )


def test_uia_path_from_candidates():
    raw = [
        {"selector_type": "coordinate", "selector_value": "1,2", "score": 70},
        {
            "selector_type": "uia_path",
            "selector_value": json.dumps(synthesize_desktop_icon_uia_path("控制面板")),
            "score": 98,
        },
    ]
    got = uia_path_from_locator_candidates(raw)
    nodes = json.loads(got)
    assert nodes[-1]["name"] == "控制面板"


def test_enrich_from_candidates_only():
    spec = {
        "process": "ApplicationFrameHost.exe",
        "hwnd": 111,
        "window_title": "设置",
    }
    raw = [
        {
            "selector_type": "uia_path",
            "selector_value": json.dumps(synthesize_desktop_icon_uia_path("控制面板")),
            "score": 98,
        },
    ]
    out = enrich_desktop_spec_for_precise_run(
        spec,
        raw,
        selector_type="coordinate",
        selector_value="10,20",
    )
    assert out.get("surface") == "desktop_shell"
    assert "hwnd" not in out
    assert isinstance(out.get("uia_path"), list)


def test_relative_coord_to_client_xy():
    import ctypes
    from unittest.mock import patch

    from desktop_precise_locator import relative_coord_to_client_xy

    spec = {"hwnd": 100}

    def _fake_get_client_rect(_hwnd, pref):
        rect = ctypes.cast(pref, ctypes.POINTER(ctypes.wintypes.RECT)).contents
        rect.left = 0
        rect.top = 0
        rect.right = 200
        rect.bottom = 100
        return 1

    with patch.object(
        ctypes.windll.user32, "GetClientRect", _fake_get_client_rect
    ):
        cx, cy = relative_coord_to_client_xy(
            spec, '{"x_pct": 0.5, "y_pct": 0.25}'
        )
    assert cx == 100
    assert cy == 25


def test_synthesize_path_workerw_regex():
    nodes = synthesize_desktop_icon_uia_path("测试")
    assert nodes[0].get("class_name") == "Progman|WorkerW"
    lines = format_uia_path_for_display(nodes)
    assert any("regex:cls=" in line for line in lines)
    assert any("ListItem" in line and "测试" in line for line in lines)
