# -*- coding: utf-8 -*-
"""X7: Hermes 显式接入跨端编排（opt-in，不可用不得静默回退）。"""

from unittest.mock import patch

from ai_modules.execute.orchestrator import (
    _execute_ui_stage,
    _stage_requests_hermes,
    execute_cross_end_plan,
)
from ai_modules.plan.context_bus import CrossEndContext


def _ctx():
    return CrossEndContext(plan_id="h1", scenario="hermes")


def test_stage_requests_hermes_opt_in_only():
    assert _stage_requests_hermes({"layer": "web"}) is False
    assert _stage_requests_hermes({"executor": "hermes"}) is True
    assert _stage_requests_hermes({"use_hermes": True}) is True
    assert _stage_requests_hermes({"use_hermes": False, "executor": "hermes"}) is False
    assert _stage_requests_hermes({"layer": "web"}, {"default_ui_executor": "hermes"}) is True
    assert _stage_requests_hermes(
        {"use_hermes": False}, {"default_ui_executor": "hermes"}
    ) is False


def test_hermes_unavailable_fails_without_classic_fallback():
    stage = {
        "id": "w1",
        "layer": "web",
        "executor": "hermes",
        "description": "打开登录页并点击登录",
    }
    with patch(
        "ai_modules.execute.hermes_stage_executor.hermes_execute_available",
        return_value=False,
    ):
        with patch(
            "ai_modules.execute.hermes_stage_executor.hermes_execute_stage"
        ) as mock_run:
            result, _ = _execute_ui_stage(stage, _ctx())
            mock_run.assert_not_called()
    assert result["ok_assert"] is False
    assert result.get("error_code") == "HERMES_UNAVAILABLE"
    assert result.get("executor") == "hermes"
    assert result.get("user_hint")


def test_hermes_success_path():
    stage = {
        "id": "w2",
        "layer": "web",
        "use_hermes": True,
        "description": "验证首页标题",
    }
    with patch(
        "ai_modules.execute.hermes_stage_executor.hermes_execute_available",
        return_value=True,
    ):
        with patch(
            "ai_modules.execute.hermes_stage_executor.hermes_execute_stage",
            return_value=({"ok_assert": True, "summary": "[RESULT] ok"}, {}),
        ):
            result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is True
    assert result.get("executor") == "hermes"


def test_hermes_default_fail_without_result_marker():
    stage = {
        "id": "w3",
        "layer": "web",
        "executor": "hermes",
        "description": "随便看看",
    }
    with patch(
        "ai_modules.execute.hermes_stage_executor.hermes_execute_available",
        return_value=True,
    ):
        with patch(
            "ai_modules.execute.hermes_stage_executor.hermes_execute_stage",
            return_value=(
                {
                    "ok_assert": False,
                    "error": "Hermes 回复未包含 [RESULT] ok，默认失败（防假绿）",
                    "executor": "hermes",
                },
                {},
            ),
        ):
            result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False


def test_classic_path_untouched_by_default():
    stage = {
        "id": "w4",
        "layer": "web",
        "steps": [{"action": "click", "selector": "#a"}],
    }
    with patch("browser_manager.get_page", return_value=object()):
        with patch(
            "ai_modules.execute.web_runner.execute_single_web_step",
            return_value={"ok": True, "skipped": False, "action": "click"},
        ):
            with patch(
                "ai_modules.execute.hermes_stage_executor.hermes_execute_stage"
            ) as mock_h:
                result, _ = _execute_ui_stage(stage, _ctx())
                mock_h.assert_not_called()
    assert result["ok_assert"] is True
    assert result.get("executor") == "classic"


def test_orchestrator_plan_default_ui_executor_hermes():
    plan = {
        "plan_id": "p-hermes",
        "default_ui_executor": "hermes",
        "stages": [
            {
                "id": "stage-1",
                "layer": "web",
                "sync_point": "done",
                "description": "点一下登录",
            }
        ],
    }
    with patch(
        "ai_modules.execute.hermes_stage_executor.hermes_execute_available",
        return_value=False,
    ):
        out = execute_cross_end_plan(plan, acquire_lock=False)
    assert out.get("success") is False
    assert out["stage_results"][0].get("error_code") == "HERMES_UNAVAILABLE"
