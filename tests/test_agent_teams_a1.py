# -*- coding: utf-8 -*-
"""Phase A-1：TestRunState + Planner/WebApiExecutor/Verifier 最小闭环。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_modules.agent_teams import load_team_spec, run_cross_end_qa_team
from ai_modules.agent_teams.roles import PlannerAgent, VerifierAgent, WebApiExecutorAgent
from ai_modules.agent_teams.team_runner import run_with_injected_execute
from ai_modules.agent_teams.test_run_state import TestRunState, load_run, save_run


def _mini_plan(**extra):
    plan = {
        "plan_id": "plan-a1-demo",
        "scenario": "A1 demo",
        "stages": [
            {
                "id": "stage-1",
                "layer": "api",
                "label": "ping",
                "skill": "testory-api-test",
                "request": {"method": "GET", "url": "https://example.com"},
                "assert": {"status_in": [200]},
            }
        ],
    }
    plan.update(extra)
    return plan


def test_team_spec_loads():
    spec = load_team_spec()
    assert spec.get("team_id") == "testory-cross-end-qa-team"
    roles = [r.get("id") for r in spec.get("roles") or []]
    assert "Planner" in roles
    assert "WebApiExecutor" in roles
    assert "Verifier" in roles


def test_test_run_state_persist_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    st = TestRunState.create(goal="persist-me", user_id="u1")
    st.emit("Planner", "dispatch", "go")
    st.emit("Planner", "complete", "done")
    path = save_run(st)
    assert path.is_file()
    loaded = load_run(st.run_id)
    assert loaded is not None
    assert loaded.goal == "persist-me"
    assert len(loaded.events) >= 2
    assert "Planner" in loaded.agent_kinds_seen()


def test_three_agents_emit_and_pass_when_execute_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))

    def fake_execute(plan, **kwargs):
        assert plan.get("plan_id") == "plan-a1-demo"
        return {
            "success": True,
            "gate_passed": True,
            "variables": {"order_id": "O-1"},
            "stage_results": [
                {"stage_id": "stage-1", "ok_assert": True, "elapsed_ms": 10},
            ],
            "assertion_passed": 1,
            "assertion_failed": 0,
            "assertion_details": [{"name": "eq", "passed": True}],
        }

    state = run_with_injected_execute(
        fake_execute,
        plan=_mini_plan(),
        description="unused when plan given",
        persist=True,
    )
    assert state.status == "success"
    assert state.vars.get("order_id") == "O-1"
    assert state.report and state.report.get("passed") is True
    agents = state.agent_kinds_seen()
    assert "Planner" in agents
    assert "WebApiExecutor" in agents
    assert "Verifier" in agents
    # 日志可见派单/完成
    kinds = {(e.get("agent"), e.get("kind")) for e in state.events}
    assert ("Planner", "dispatch") in kinds
    assert ("Planner", "complete") in kinds
    assert ("WebApiExecutor", "dispatch") in kinds
    assert ("WebApiExecutor", "complete") in kinds
    assert ("Verifier", "dispatch") in kinds
    assert ("Verifier", "complete") in kinds
    # 落盘
    again = load_run(state.run_id)
    assert again is not None
    assert again.status == "success"


def test_verifier_blocks_false_green_on_execute_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))

    def fake_execute(plan, **kwargs):
        return {
            "success": False,
            "error": "跨端断言失败 1 条",
            "error_code": "CROSS_END_ASSERT_FAILED",
            "gate_passed": False,
            "stage_results": [
                {"stage_id": "stage-1", "ok_assert": True},
            ],
            "assertion_passed": 0,
            "assertion_failed": 1,
            "assertion_details": [{"name": "eq", "passed": False}],
        }

    state = run_with_injected_execute(fake_execute, plan=_mini_plan())
    assert state.status == "failed"
    assert state.report.get("passed") is False
    assert "Verifier" in state.agent_kinds_seen()


def test_planner_fail_skips_executor_still_has_verifier_report(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))

    class BadPlanner(PlannerAgent):
        def run(self, state, *, description="", plan=None):
            state.set_status("planning")
            state.emit(self.role, "dispatch", "plan")
            state.errors.append("Planner: boom")
            state.emit(self.role, "fail", "boom")
            state.set_status("failed")
            return state

    called = {"n": 0}

    def fake_execute(plan, **kwargs):
        called["n"] += 1
        return {"success": True, "stage_results": []}

    state = run_cross_end_qa_team(
        description="x",
        planner=BadPlanner(),
        executor=WebApiExecutorAgent(execute_fn=fake_execute),
        verifier=VerifierAgent(),
    )
    assert called["n"] == 0
    assert state.status == "failed"
    assert state.report is not None
    assert state.report.get("passed") is False
    assert "Planner" in state.agent_kinds_seen()
    assert "Verifier" in state.agent_kinds_seen()


def test_report_json_shape_on_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))

    def fake_execute(plan, **kwargs):
        return {
            "success": True,
            "stage_results": [{"stage_id": "stage-1", "ok_assert": True}],
            "assertion_passed": 0,
            "assertion_failed": 0,
            "assertion_details": [],
        }

    state = run_with_injected_execute(fake_execute, plan=_mini_plan())
    report = state.report
    assert report["passed"] is True
    assert report["evidence_level"] in ("strong", "weak", "missing")
    assert "evidence_counts" in report
    assert report["run_id"] == state.run_id
    # 可序列化为 report.json
    raw = json.dumps(report, ensure_ascii=False)
    assert "passed" in raw
