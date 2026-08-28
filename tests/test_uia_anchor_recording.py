# -*- coding: utf-8 -*-
"""阶段1：UIA 锚点补录 —— 录制器 to_case_steps 产出真实选择器。

验证：移动/桌面工具 result 携带 uia_anchor → 步骤含 selector_type/selector_value/
locator_candidates；多端联动时 per-step layer 按工具前缀归因；无锚点时行为与现状一致。
"""
import json

from modules.ai.ai_action_recorder import ActionRecorder, _layer_for_tool_name

_MOBILE_ANCHOR = {
    "layer": "android",
    "candidates": [
        {"type": "id", "value": "com.example:id/login_btn", "score": 0.95},
        {"type": "text", "value": "登录", "score": 0.85},
    ],
    "node": {"resource_id": "com.example:id/login_btn", "text": "登录", "bounds": [10, 10, 100, 50]},
    "tree_fingerprint": "abc123",
}

_DESKTOP_ANCHOR = {
    "layer": "desktop",
    "candidates": [
        {"type": "automation_id", "value": "ConfirmButton", "score": 0.95},
        {"type": "name", "value": "确定", "score": 0.85},
    ],
    "node": {"name": "确定", "control_type": "Button", "rect": [0, 0, 80, 30]},
    "tree_fingerprint": "",
}


def _capture(recorder, name, args, result):
    return recorder.capture_from_tool_event(name=name, args=args, result=result)


def test_mobile_tap_with_anchor_produces_selector():
    rec = ActionRecorder(platform="android")
    _capture(
        rec,
        "mobile_tap",
        {"text": "登录"},
        {"ok": True, "text": "登录", "uia_anchor": _MOBILE_ANCHOR},
    )
    steps = rec.to_case_steps()
    assert len(steps) == 1
    step = steps[0]
    assert step["automation_layer"] == "android"
    assert step["selector_type"] == "id"
    assert step["selector_value"] == "com.example:id/login_btn"
    assert isinstance(step["locator_candidates"], list)
    assert step["locator_candidates"][0]["type"] == "id"
    assert step["uia_anchor"] == _MOBILE_ANCHOR


def test_desktop_click_with_anchor_keeps_anchor_selector():
    rec = ActionRecorder(platform="desktop")
    _capture(
        rec,
        "windows_click_element",
        {"description": "确定"},
        {"ok": True, "matched": "确定", "uia_anchor": _DESKTOP_ANCHOR},
    )
    steps = rec.to_case_steps()
    assert len(steps) == 1
    step = steps[0]
    assert step["automation_layer"] == "desktop"
    # 有 UIA 锚点时不回退恒值 "window"
    assert step["selector_type"] == "automation_id"
    assert step["selector_value"] == "ConfirmButton"
    assert len(step["locator_candidates"]) == 2


def test_mixed_platform_per_record_layer():
    # 平台单值为 desktop，但记录含手机步骤 → per-step layer 按工具前缀归因
    rec = ActionRecorder(platform="desktop")
    _capture(
        rec,
        "mobile_tap",
        {"text": "登录"},
        {"ok": True, "uia_anchor": _MOBILE_ANCHOR},
    )
    _capture(
        rec,
        "windows_click_element",
        {"description": "确定"},
        {"ok": True, "uia_anchor": _DESKTOP_ANCHOR},
    )
    steps = rec.to_case_steps()
    assert [s["automation_layer"] for s in steps] == ["android", "desktop"]
    assert [s["selector_type"] for s in steps] == ["id", "automation_id"]


def test_no_anchor_keeps_legacy_behavior():
    # 无锚点时：移动端不产出选择器；桌面端仍回退 "window"（与现状一致）
    rec = ActionRecorder(platform="desktop")
    _capture(rec, "mobile_tap", {"text": "登录"}, {"ok": True, "text": "登录"})
    _capture(rec, "windows_click_element", {"description": "确定"}, {"ok": True, "matched": "确定"})
    steps = rec.to_case_steps()
    mob = [s for s in steps if s["automation_layer"] == "android"]
    dsk = [s for s in steps if s["automation_layer"] == "desktop"]
    assert mob and "selector_type" not in mob[0]
    assert "selector_value" not in mob[0]
    assert dsk and dsk[0]["selector_type"] == "window"


def test_layer_for_tool_name_prefixes():
    assert _layer_for_tool_name("mobile_tap") == "android"
    assert _layer_for_tool_name("windows_click_element") == "desktop"
    assert _layer_for_tool_name("desktop_type_text") == "desktop"
    assert _layer_for_tool_name("browser_click") == "web"
    assert _layer_for_tool_name("mobile_extract_otp") == "android"
    assert _layer_for_tool_name("anything_else") == ""


def test_verification_recorded_when_present():
    rec = ActionRecorder(platform="android")
    _capture(
        rec,
        "mobile_tap",
        {"text": "登录"},
        {
            "ok": True,
            "uia_anchor": _MOBILE_ANCHOR,
            "verification": {
                "found": True,
                "matched_via": "id",
                "node_state": {"text": "登录", "selected": True},
            },
        },
    )
    steps = rec.to_case_steps()
    assert steps and steps[0]["verification"]["found"] is True
    assert steps[0]["verification"]["matched_via"] == "id"
