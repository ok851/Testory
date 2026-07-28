# -*- coding: utf-8 -*-
"""跨端 UI 阶段假绿守卫：无 page / 返回值校验 / context 默认失败。"""

from unittest.mock import MagicMock, patch

from ai_modules.execute.orchestrator import _execute_ui_stage
from ai_modules.execute.web_runner import execute_single_web_step
from ai_modules.plan.context_bus import CrossEndContext


def _ctx():
    return CrossEndContext(plan_id="t1", scenario="test")


def test_web_no_page_fails_not_green():
    stage = {
        "id": "w1",
        "layer": "web",
        "steps": [{"action": "click", "selector": "#a"}],
    }
    with patch("browser_manager.get_page", return_value=None):
        result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False
    assert "浏览器" in (result.get("error") or "") or "NO_BROWSER" in (
        result.get("error_code") or ""
    )


def test_web_empty_steps_fails():
    result, _ = _execute_ui_stage({"id": "w0", "layer": "web", "steps": []}, _ctx())
    assert result["ok_assert"] is False


def test_unknown_layer_fails():
    result, _ = _execute_ui_stage(
        {"id": "x", "layer": "tv", "steps": [{"action": "wait", "value": "0"}]},
        _ctx(),
    )
    assert result["ok_assert"] is False
    assert "layer" in (result.get("error") or "").lower() or "不支持" in (
        result.get("error") or ""
    )


def test_web_step_failure_propagates():
    page = MagicMock()
    stage = {
        "id": "w2",
        "layer": "web",
        "steps": [{"action": "click", "selector": "#missing"}],
    }
    with patch("browser_manager.get_page", return_value=page):
        with patch(
            "ai_modules.execute.web_runner.execute_single_web_step",
            return_value={"ok": False, "error": "timeout", "action": "click"},
        ):
            result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False
    assert "timeout" in (result.get("error") or "")


def test_web_all_steps_ok():
    page = MagicMock()
    stage = {
        "id": "w3",
        "layer": "web",
        "steps": [
            {"action": "wait", "value": "0"},
            {"action": "click", "selector": "#ok"},
        ],
    }
    with patch("browser_manager.get_page", return_value=page):
        with patch(
            "ai_modules.execute.web_runner.execute_single_web_step",
            side_effect=[
                {"ok": True, "skipped": False, "action": "wait"},
                {"ok": True, "skipped": False, "action": "click"},
            ],
        ):
            result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is True
    assert result["steps_executed"] == 2


def test_web_allow_skip_navigate_counts_ok():
    """仅当阶段显式 allow_skip 时，全跳过导航才可绿。"""
    page = MagicMock()
    stage = {
        "id": "w4",
        "layer": "web",
        "allow_skip": True,
        "steps": [{"action": "navigate", "url": "", "allow_skip": True}],
    }
    with patch("browser_manager.get_page", return_value=page):
        result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is True


def test_web_all_skipped_without_stage_allow_skip_fails():
    page = MagicMock()
    stage = {
        "id": "w4b",
        "layer": "web",
        "steps": [{"action": "navigate", "url": "", "allow_skip": True}],
    }
    with patch("browser_manager.get_page", return_value=page):
        result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False
    assert result.get("error_code") == "ALL_STEPS_SKIPPED"


def test_mobile_checks_return_values():
    stage = {
        "id": "m1",
        "layer": "mobile",
        "executor": "appium",
        "await_device_run": False,
        "steps": [{"action": "tap", "selector": "#x"}],
    }
    mock_ex = MagicMock()
    mock_ex.execute_steps.return_value = [
        {"status": "error", "error": "device offline", "action": "tap"}
    ]
    with patch("mobile_executor.get_mobile_executor", return_value=mock_ex):
        result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False
    assert "offline" in (result.get("error") or "") or "失败" in (
        result.get("error") or ""
    )


def test_mobile_partial_steps_fail():
    stage = {
        "id": "m2",
        "layer": "mobile",
        "executor": "appium",
        "await_device_run": False,
        "steps": [{"action": "tap"}, {"action": "tap"}],
    }
    mock_ex = MagicMock()
    mock_ex.execute_steps.return_value = [{"status": "success", "action": "tap"}]
    with patch("mobile_executor.get_mobile_executor", return_value=mock_ex):
        result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False


def test_desktop_checks_return_and_rejects_warning():
    stage = {
        "id": "d1",
        "layer": "desktop",
        "steps": [{"action": "wait", "input_value": "0.01"}],
    }
    with patch(
        "desktop_automation.sync_desktop_execute_step",
        return_value={"status": "warning", "warning": "soft miss"},
    ):
        with patch(
            "step_executor.validate_desktop_step_result",
            side_effect=lambda r, a: r,
        ):
            result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False
    assert "warning" in (result.get("error") or "").lower() or "soft" in (
        result.get("error") or ""
    )


def test_desktop_validate_raise_fails_stage():
    stage = {
        "id": "d2",
        "layer": "desktop",
        "steps": [{"action": "click", "selector": "btn"}],
    }
    with patch(
        "desktop_automation.sync_desktop_execute_step",
        return_value={"status": "error", "error": "not found"},
    ):
        with patch(
            "step_executor.validate_desktop_step_result",
            side_effect=RuntimeError("not found"),
        ):
            result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False
    assert "not found" in (result.get("error") or "")


def test_context_missing_ok_assert_defaults_fail():
    ctx = _ctx()
    ctx.record_stage_result("s1", {"error": "x"})
    assert ctx.get_stage_result("s1")["ok"] is False
    assert ctx.all_passed is False


def test_context_all_passed_requires_stages():
    ctx = _ctx()
    assert ctx.all_passed is False


def test_context_all_passed_requires_every_stage_ok():
    ctx = _ctx()
    ctx.record_stage_result("a", {"ok_assert": True})
    ctx.record_stage_result("b", {"ok_assert": False, "error": "boom"})
    assert ctx.all_passed is False


def test_web_runner_empty_click_selector_fails():
    page = MagicMock()
    out = execute_single_web_step({"action": "click"}, page)
    assert out["ok"] is False
    assert out.get("error_code") == "EMPTY_SELECTOR"
    assert "selector" in (out.get("error") or "")


def test_web_runner_empty_selector_ignores_allow_skip():
    """Y3：空 selector 即使 allow_skip 也不得跳过当绿。"""
    page = MagicMock()
    out = execute_single_web_step(
        {"action": "click", "selector": "  ", "allow_skip": True}, page
    )
    assert out["ok"] is False
    assert out.get("skipped") is False
    assert out.get("error_code") == "EMPTY_SELECTOR"


def test_web_runner_empty_navigate_fails():
    page = MagicMock()
    out = execute_single_web_step({"action": "navigate", "url": ""}, page)
    assert out["ok"] is False
    assert out.get("error_code") == "EMPTY_URL"


def test_web_runner_empty_assert_fails():
    page = MagicMock()
    out = execute_single_web_step({"action": "assert"}, page)
    assert out["ok"] is False
    assert out.get("error_code") == "EMPTY_ASSERT"


def test_web_stage_propagates_empty_selector_code_and_hint():
    page = MagicMock()
    stage = {
        "id": "w5",
        "layer": "web",
        "steps": [{"action": "input", "selector": "", "value": "x"}],
    }
    with patch("browser_manager.get_page", return_value=page):
        result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False
    assert result.get("error_code") == "EMPTY_SELECTOR"
    assert result.get("user_hint")
    assert "选择器" in result["user_hint"] or "selector" in result["user_hint"].lower()
