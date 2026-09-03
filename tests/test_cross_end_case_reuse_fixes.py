# -*- coding: utf-8 -*-
"""生产级回归：AI 多端用例可复用 + 层推断 + 桌面点击收录。"""
from __future__ import annotations

import json

from modules.ai.ai_action_recorder import ActionRecorder, is_case_worthy_for_platform
from modules.ai.ai_step_normalization import normalize_ai_step
from modules.desktop.desktop_automation import normalize_automation_layer
from modules.execution.step_executor import ensure_mixed_run_environment


_DESKTOP_ANCHOR = {
    "layer": "desktop",
    "candidates": [
        {"type": "automation_id", "value": "ConfirmButton", "score": 0.95},
        {"type": "name", "value": "确定", "score": 0.85},
    ],
    "node": {"name": "确定", "control_type": "Button"},
}


def test_windows_click_element_is_case_worthy_and_maps_to_click():
    assert ActionRecorder._normalize_action_type("windows_click_element", {}) == "click"
    assert is_case_worthy_for_platform("click", "desktop")
    assert is_case_worthy_for_platform("click_element", "desktop")
    assert is_case_worthy_for_platform("click", "auto")


def test_desktop_click_json_string_result_keeps_uia_anchor():
    """工具结果常为 JSON 字符串；必须解析出 uia_anchor 才能落库选择器。"""
    rec = ActionRecorder(platform="auto")
    payload = {
        "success": True,
        "ok": True,
        "verified": True,
        "matched": "确定",
        "uia_anchor": _DESKTOP_ANCHOR,
    }
    rec.capture_from_tool_event(
        name="windows_click_element",
        args={"description": "确定"},
        result=json.dumps(payload, ensure_ascii=False),
        status="completed",
    )
    steps = rec.to_case_steps()
    assert len(steps) == 1
    step = steps[0]
    assert step["action"] == "click"
    assert step["automation_layer"] == "desktop"
    assert step["selector_value"]
    assert step["selector_type"] in ("automation_id", "name")


def test_web_navigate_and_text_click_produce_reusable_fields():
    rec = ActionRecorder(platform="auto")
    rec.capture_from_tool_event(
        name="browser_navigate",
        args={"url": "https://example.com/login"},
        result={"ok": True, "url": "https://example.com/login"},
        status="completed",
    )
    rec.capture_from_tool_event(
        name="browser_click",
        args={"ref": "@e5", "text": "登录"},
        result={"ok": True, "matched": "登录"},
        status="completed",
    )
    rec.capture_from_tool_event(
        name="browser_type",
        args={"ref": "@e3", "text": "admin"},
        result={"ok": True, "matched": "账号"},
        status="completed",
    )
    steps = rec.to_case_steps()
    assert len(steps) >= 3
    nav = steps[0]
    assert nav["action"] == "navigate"
    assert "example.com" in (nav.get("input_value") or "")
    click = next(s for s in steps if s["action"] == "click")
    assert click.get("selector_value")
    assert click["selector_value"] != "@e5"
    typed = next(s for s in steps if s["action"] == "input")
    assert typed.get("input_value") == "admin"
    assert typed.get("selector_value")


def test_mixed_platform_plan_keeps_all_layers():
    rec = ActionRecorder(platform="auto")
    rec.capture_from_tool_event(
        name="browser_navigate",
        args={"url": "https://example.com"},
        result={"ok": True},
        status="completed",
    )
    rec.capture_from_tool_event(
        name="windows_click_element",
        args={"description": "确定"},
        result={"ok": True, "matched": "确定", "uia_anchor": _DESKTOP_ANCHOR},
        status="completed",
    )
    rec.capture_from_tool_event(
        name="mobile_tap",
        args={"text": "登录"},
        result={
            "ok": True,
            "uia_anchor": {
                "candidates": [{"type": "text", "value": "登录"}],
                "node": {"text": "登录"},
            },
        },
        status="completed",
    )
    plan, _warnings = rec.build_normalized_plan(instruction="多端登录")
    layers = {s.get("automation_layer") for s in plan["steps"]}
    assert "web" in layers
    assert "desktop" in layers
    assert "android" in layers
    for s in plan["steps"]:
        assert s.get("action")
        if s["action"] in ("click", "tap", "input", "input_text"):
            assert s.get("selector_value") or s.get("locate_prompt")


def test_normalize_layer_does_not_misclassify_web_click_as_android():
    assert normalize_automation_layer({"action": "click"}) == "web"
    assert normalize_automation_layer({"action": "input"}) == "web"
    assert normalize_automation_layer({"action": "fill"}) == "web"
    assert normalize_automation_layer({"action": "wait"}) == "web"
    assert normalize_automation_layer({"action": "assert"}) == "web"
    assert normalize_automation_layer({"action": "click", "automation_layer": "desktop"}) == "desktop"
    assert normalize_automation_layer({"action": "tap"}) == "android"
    assert normalize_automation_layer({"action": "launch_app"}) == "desktop"


def test_ensure_mixed_run_allows_web_android_when_runtime_ok(monkeypatch):
    steps = [
        {"action": "navigate", "automation_layer": "web", "input_value": "https://x"},
        {"action": "tap", "automation_layer": "android", "selector_value": "登录"},
    ]
    # 避免真实环境依赖打断断言：模拟运行时可用
    monkeypatch.setattr(
        "modules.execution.step_executor.case_steps_include_desktop",
        lambda _s: False,
    )
    monkeypatch.setattr(
        "modules.execution.step_executor.case_steps_include_android",
        lambda _s: True,
    )
    monkeypatch.setattr(
        "modules.execution.step_executor.case_steps_include_web",
        lambda _s: True,
    )

    class _Cfg:
        @staticmethod
        def mobile_runtime_unavailable_reason():
            return None

    import sys

    sys.modules["modules.mobile.mobile_env_config"] = type(sys)("modules.mobile.mobile_env_config")
    sys.modules["modules.mobile.mobile_env_config"].mobile_runtime_unavailable_reason = (
        lambda: None
    )
    msg = ensure_mixed_run_environment(steps)
    assert msg is None or "不支持" not in str(msg)


def test_normalize_ai_step_promotes_target_and_aliases():
    out = normalize_ai_step(
        {
            "action": "type",
            "target": "用户名",
            "input_value": "admin",
            "automation_layer": "web",
        }
    )
    assert out["action"] == "input"
    assert out["selector_value"] == "用户名"
    assert out["selector_type"] == "text"
    assert out["input_value"] == "admin"

    out2 = normalize_ai_step(
        {
            "action": "navigate",
            "target": "https://example.com/a",
            "automation_layer": "web",
        }
    )
    assert out2["action"] == "navigate"
    assert out2["input_value"].startswith("https://")
