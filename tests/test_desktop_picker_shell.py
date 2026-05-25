# -*- coding: utf-8 -*-
"""桌面 Shell 拾取：覆盖窗下仍应产出 uia_path。"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_build_shell_icon_pick_from_cache():
    from desktop_picker import _build_shell_icon_pick_from_cache

    hit = (10, 20, 50, 70, "控制面板")
    pick = _build_shell_icon_pick_from_cache(hit, 100, 200)
    assert pick["selector_type"] == "uia_path"
    assert pick["desktop_spec"].get("surface") == "desktop_shell"
    assert "hwnd" not in pick["desktop_spec"]
    nodes = pick.get("uia_path") or []
    assert any(n.get("name") == "控制面板" for n in nodes)
    cands = json.loads(pick.get("locator_candidates") or "[]")
    assert any(c.get("selector_type") == "uia_path" for c in cands)


@patch("desktop_picker._desktop_icon_hit_for_pick")
def test_pick_control_at_icon_hit_win32(mock_hit):
    from desktop_picker import _pick_control_at

    mock_hit.return_value = (10, 20, 50, 70, "回收站")
    pick = _pick_control_at(100, 200, set())
    assert pick is not None
    assert pick["selector_type"] == "uia_path"
    assert pick["desktop_spec"].get("surface") == "desktop_shell"


def test_finalize_shell_forces_uia_primary():
    from desktop_picker import _finalize_shell_desktop_pick
    from desktop_precise_locator import synthesize_desktop_icon_uia_path

    path = synthesize_desktop_icon_uia_path("记事本")
    pick = {
        "selector_type": "coordinate",
        "selector_value": "1,2",
        "name": "记事本",
        "desktop_spec": {"hwnd": 999, "window_title": "设置"},
    }
    out = _finalize_shell_desktop_pick(pick, path, 50, 60, icon_name="记事本")
    assert out["selector_type"] == "uia_path"
    assert out["desktop_spec"].get("surface") == "desktop_shell"
    assert "hwnd" not in out["desktop_spec"]
