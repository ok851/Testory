# -*- coding: utf-8 -*-
"""desktop_picker 捕获模式与超时策略。"""

from unittest.mock import patch

import desktop_picker as dp


def test_picker_timeout_standard_vs_deep():
    with patch.object(dp, "_session_snapshot", return_value={"capture_mode": "standard"}):
        assert dp._picker_pick_timeout_sec() == dp._PICKER_PICK_TIMEOUT_STANDARD_SEC
    with patch.object(dp, "_session_snapshot", return_value={"capture_mode": "deep"}):
        assert dp._picker_pick_timeout_sec() == dp._PICKER_PICK_TIMEOUT_DEEP_SEC


def test_standard_pick_skips_uia(monkeypatch):
    """标准模式应走 Win32/坐标回退，不调用 UIA from_point。"""
    spec = {
        "hwnd": 42,
        "process": "notepad.exe",
        "window_title": "无标题",
        "class_name": "Notepad",
    }
    monkeypatch.setattr(dp, "_desktop_icon_hit_for_pick", lambda x, y: None)
    monkeypatch.setattr(dp, "_top_level_hwnd_at", lambda x, y, ex: 42)
    monkeypatch.setattr(dp, "_desktop_spec_at_point", lambda x, y, ex: dict(spec))
    uia_called = {"n": 0}

    def _fake_uia(*_a, **_k):
        uia_called["n"] += 1
        return None

    monkeypatch.setattr(dp, "_uia_wrapper_from_point_timed", _fake_uia)
    monkeypatch.setattr(
        dp,
        "_try_win32_control_info",
        lambda x, y, h: {"class_name": "Edit", "text": "hello", "label": "hello"},
    )
    monkeypatch.setattr(dp, "_attach_precise_capture_metadata", lambda pick, x, y, **kw: pick)

    pick = dp._pick_control_at(
        10, 20, set(), capture_mode=dp.CAPTURE_MODE_STANDARD
    )
    assert uia_called["n"] == 0
    assert pick is not None
    assert pick.get("selector_type") in ("client_coord", "coordinate")


def test_deep_pick_calls_uia(monkeypatch):
    spec = {
        "hwnd": 42,
        "process": "notepad.exe",
        "window_title": "无标题",
        "class_name": "Notepad",
    }
    monkeypatch.setattr(dp, "_desktop_icon_hit_for_pick", lambda x, y: None)
    monkeypatch.setattr(dp, "_top_level_hwnd_at", lambda x, y, ex: 42)
    monkeypatch.setattr(dp, "_desktop_spec_at_point", lambda x, y, ex: dict(spec))
    uia_called = {"n": 0}

    def _fake_uia(*_a, **_k):
        uia_called["n"] += 1
        return None

    monkeypatch.setattr(dp, "_uia_wrapper_from_point_timed", _fake_uia)
    monkeypatch.setattr(dp, "_try_win32_control_info", lambda x, y, h: None)
    monkeypatch.setattr(dp, "_attach_precise_capture_metadata", lambda pick, x, y, **kw: pick)

    dp._pick_control_at(10, 20, set(), capture_mode=dp.CAPTURE_MODE_DEEP)
    assert uia_called["n"] == 1
