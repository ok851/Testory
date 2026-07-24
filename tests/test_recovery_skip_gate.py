# -*- coding: utf-8 -*-
"""RECOVERY_SKIP 诚实化：披露、默认挡成功、显式策略可放行。"""

from unittest.mock import patch

from ai_modules.execute.orchestrator import execute_cross_end_plan
from ai_modules.plan.context_bus import CrossEndContext
from ai_modules.plan.recovery_engine import (
    RECOVERY_ABORT,
    RECOVERY_RETRY,
    RECOVERY_SKIP,
    RecoveryEngine,
)


def test_decide_continue_is_skip_and_logged():
    eng = RecoveryEngine()
    assert eng.decide("s1", "boom", "continue") == RECOVERY_SKIP
    assert eng.get_recovery_log()[-1]["action"] == RECOVERY_SKIP
    assert eng.skipped_stage_ids() == ["s1"]


def test_decide_retry_then_abort():
    eng = RecoveryEngine(max_retries=1)
    assert eng.decide("s1", "e1", "retry") == RECOVERY_RETRY
    assert eng.decide("s1", "e2", "retry") == RECOVERY_ABORT
    assert eng.get_recovery_log()[-1].get("retry_exhausted") is True


def test_context_retry_clears_old_error():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ctx.record_stage_result("a", {"ok_assert": False, "error": "fail1"})
    assert ctx.all_passed is False
    ctx.record_stage_result("a", {"ok_assert": True})
    assert ctx.all_passed is True
    assert ctx._errors == []


def test_evaluate_pass_ignore_skipped():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ctx.record_stage_result(
        "a",
        {"ok_assert": False, "error": "x", "skipped_failure": True, "recovery_action": "skip"},
    )
    ctx.record_stage_result("b", {"ok_assert": True})
    assert ctx.evaluate_pass(ignore_skipped_failures=False) is False
    assert ctx.evaluate_pass(ignore_skipped_failures=True) is True


def test_orchestrator_skip_blocks_success_by_default():
    plan = {
        "plan_id": "skip-block",
        "scenario": "skip",
        "stages": [
            {
                "id": "bad",
                "layer": "web",
                "on_failure": "continue",
                "steps": [{"action": "click", "selector": "#x"}],
                "sync_point": "bad_done",
            },
            {
                "id": "ok",
                "layer": "web",
                "steps": [{"action": "wait", "value": "0"}],
                "sync_point": "ok_done",
            },
        ],
    }

    def _ui(stage, context, **kwargs):
        sid = stage.get("id")
        if sid == "bad":
            return {"ok_assert": False, "error": "selector missing", "elapsed_ms": 1}, {}
        return {"ok_assert": True, "error": None, "elapsed_ms": 1, "steps_executed": 1}, {}

    with patch("ai_modules.execute.orchestrator._execute_ui_stage", side_effect=_ui):
        out = execute_cross_end_plan(plan, acquire_lock=False)

    assert out.get("success") is False
    assert "bad" in (out.get("skipped_failure_stages") or [])
    assert out.get("error_code") == "RECOVERY_SKIP_BLOCKS_SUCCESS"
    assert any(
        (r or {}).get("stage_id") == "bad" and (r or {}).get("skipped_failure")
        for r in out.get("stage_results") or []
    )
    assert any((r or {}).get("action") == RECOVERY_SKIP for r in out.get("recovery_log") or [])


def test_orchestrator_allow_skipped_failures_can_pass():
    plan = {
        "plan_id": "skip-allow",
        "scenario": "skip ok",
        "allow_skipped_failures": True,
        "stages": [
            {
                "id": "bad",
                "layer": "web",
                "on_failure": "continue",
                "steps": [{"action": "click", "selector": "#x"}],
                "sync_point": "bad_done",
            },
            {
                "id": "ok",
                "layer": "web",
                "steps": [{"action": "wait", "value": "0"}],
                "sync_point": "ok_done",
            },
        ],
    }

    def _ui(stage, context, **kwargs):
        if stage.get("id") == "bad":
            return {"ok_assert": False, "error": "soft", "elapsed_ms": 1}, {}
        return {"ok_assert": True, "error": None, "elapsed_ms": 1, "steps_executed": 1}, {}

    with patch("ai_modules.execute.orchestrator._execute_ui_stage", side_effect=_ui):
        out = execute_cross_end_plan(plan, acquire_lock=False)

    assert out.get("success") is True
    assert out.get("allow_skipped_failures") is True
    assert "bad" in (out.get("skipped_failure_stages") or [])


def test_orchestrator_abort_still_stops():
    plan = {
        "plan_id": "abort-1",
        "scenario": "abort",
        "stages": [
            {
                "id": "bad",
                "layer": "web",
                "on_failure": "abort",
                "steps": [{"action": "click", "selector": "#x"}],
                "sync_point": "bad_done",
            },
            {
                "id": "ok",
                "layer": "web",
                "depends_on": ["bad_done"],
                "steps": [{"action": "wait", "value": "0"}],
                "sync_point": "ok_done",
            },
        ],
    }

    calls = {"n": 0}

    def _ui(stage, context, **kwargs):
        calls["n"] += 1
        return {"ok_assert": False, "error": "hard fail", "elapsed_ms": 1}, {}

    with patch("ai_modules.execute.orchestrator._execute_ui_stage", side_effect=_ui):
        out = execute_cross_end_plan(plan, acquire_lock=False)

    assert out.get("success") is False
    assert calls["n"] == 1
    assert out.get("error") == "hard fail"
