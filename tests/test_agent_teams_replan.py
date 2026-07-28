# -*- coding: utf-8 -*-
"""R16：Verifier → Planner 失败重规划。"""

from __future__ import annotations

from ai_modules.agent_teams.replan import build_replan_feedback, propose_replan
from ai_modules.agent_teams.team_runner import run_with_injected_execute


def _plan_with_desktop_attach():
    return {
        "plan_id": "plan-replan",
        "scenario": "desktop attach",
        "stages": [
            {
                "id": "desk-1",
                "layer": "desktop",
                "steps": [
                    {
                        "action": "attach_window",
                        "desktop_spec": {"window_title_re": "^ExactERP$"},
                    }
                ],
            }
        ],
    }


def test_propose_replan_broadens_desktop_title():
    plan = _plan_with_desktop_attach()
    fb = build_replan_feedback(
        execution={"error": "no window", "error_code": "DESKTOP_SOFT_FAIL"},
        stage_results=[
            {"stage_id": "desk-1", "ok_assert": False, "error_code": "DESKTOP_SOFT_FAIL"},
        ],
    )
    new_plan, meta = propose_replan(plan, fb, suggestions=[{"title": "tip", "body": "放宽标题"}])
    assert new_plan is not None
    assert "broaden_desktop_attach" in meta["strategies"]
    tre = new_plan["stages"][0]["steps"][0]["desktop_spec"]["window_title_re"]
    assert "ExactERP" in tre and tre.startswith(".*")
    assert new_plan.get("replan_generation") == 1
    assert new_plan.get("meta", {}).get("replan_tips")


def test_replan_loop_retries_then_can_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_TEAMS_MAX_REPLAN", "1")
    calls = {"n": 0}

    def fake_execute(plan, **kwargs):
        calls["n"] += 1
        # 第一次失败；重规划后标题已放宽 → 成功
        tre = ""
        try:
            tre = plan["stages"][0]["steps"][0]["desktop_spec"]["window_title_re"]
        except Exception:
            pass
        if calls["n"] == 1 or (tre.startswith("^") and tre.endswith("$")):
            return {
                "success": False,
                "gate_passed": False,
                "error": "window missing",
                "error_code": "DESKTOP_SOFT_FAIL",
                "stage_results": [
                    {"stage_id": "desk-1", "ok_assert": False, "error_code": "DESKTOP_SOFT_FAIL"},
                ],
                "assertion_passed": 0,
                "assertion_failed": 0,
            }
        return {
            "success": True,
            "gate_passed": True,
            "stage_results": [{"stage_id": "desk-1", "ok_assert": True}],
            "assertion_passed": 0,
            "assertion_failed": 0,
            "assertion_details": [],
        }

    state = run_with_injected_execute(
        fake_execute,
        plan=_plan_with_desktop_attach(),
        persist=True,
        record_history=False,
        allow_replan=True,
        max_replan=1,
    )
    assert calls["n"] == 2
    assert state.replan_count == 1
    assert state.status == "success"
    assert "Planner" in state.agent_kinds_seen()
    assert "Verifier" in state.agent_kinds_seen()


def test_replan_exhausted_stays_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))

    def always_fail(plan, **kwargs):
        return {
            "success": False,
            "error": "still bad",
            "error_code": "DESKTOP_SOFT_FAIL",
            "stage_results": [
                {"stage_id": "desk-1", "ok_assert": False, "error_code": "DESKTOP_SOFT_FAIL"},
            ],
            "assertion_failed": 0,
            "assertion_passed": 0,
        }

    state = run_with_injected_execute(
        always_fail,
        plan=_plan_with_desktop_attach(),
        persist=False,
        record_history=False,
        allow_replan=True,
        max_replan=1,
    )
    assert state.status == "failed"
    assert state.replan_count == 1
    # 第二次仍失败，不得假绿
    assert (state.report or {}).get("passed") is False


def test_replan_disabled_no_second_execute(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    calls = {"n": 0}

    def once(plan, **kwargs):
        calls["n"] += 1
        return {
            "success": False,
            "error": "x",
            "error_code": "DESKTOP_SOFT_FAIL",
            "stage_results": [{"stage_id": "desk-1", "ok_assert": False}],
            "assertion_failed": 0,
            "assertion_passed": 0,
        }

    state = run_with_injected_execute(
        once,
        plan=_plan_with_desktop_attach(),
        persist=False,
        record_history=False,
        allow_replan=False,
    )
    assert calls["n"] == 1
    assert state.replan_count == 0
    assert state.status == "failed"
