# -*- coding: utf-8 -*-
"""企业运营：跨端异步执行 + HITL/Risk 门禁列表。"""

from __future__ import annotations

import time

import pytest

from agent_hitl import list_hitl_gates, open_hitl_gate, reset_hitl_state_for_tests, resume_hitl_gate
from ai_modules.execute.cross_end_async import (
    create_run,
    get_run,
    list_ops_gates,
    reset_cross_end_async_for_tests,
    start_run_thread,
)
from ai_modules.security.risk_guard import (
    approve_risk,
    evaluate_stage_risk,
    reset_risk_guard_for_tests,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_hitl_state_for_tests()
    reset_risk_guard_for_tests()
    reset_cross_end_async_for_tests()
    yield
    reset_hitl_state_for_tests()
    reset_risk_guard_for_tests()
    reset_cross_end_async_for_tests()


def test_list_hitl_waiting():
    open_hitl_gate("ops-g1", reason="验证码", user_id="u1")
    rows = list_hitl_gates(status="waiting")
    assert any(r.get("gate_id") == "ops-g1" for r in rows)
    resume_hitl_gate("ops-g1")
    assert not any(
        r.get("gate_id") == "ops-g1" and r.get("status") == "waiting"
        for r in list_hitl_gates(status="waiting")
    )


def test_list_ops_gates_combines_hitl_and_risk():
    open_hitl_gate("ops-g2", reason="登录", user_id="u2")
    d = evaluate_stage_risk({"id": "s-l2", "risk_level": "L2", "label": "清数"})
    assert d.approval_id
    blob = list_ops_gates(user_id="u2")
    assert blob["hitl_count"] >= 1
    assert blob["risk_count"] >= 1
    assert any(x.get("approval_id") == d.approval_id for x in blob["risk_pending"])


def test_async_cross_end_run_completes(monkeypatch):
    def _fake_execute(plan, **kwargs):
        return {
            "success": True,
            "gate_passed": True,
            "plan_id": plan.get("plan_id"),
            "stage_results": [
                {"stage_id": "s1", "ok_assert": True, "layer": "api"},
            ],
        }

    monkeypatch.setattr(
        "ai_modules.execute.orchestrator.execute_cross_end_plan",
        _fake_execute,
    )
    plan = {"plan_id": "async-1", "stages": [{"id": "s1", "layer": "api"}]}
    rec = create_run(plan, user_id="u", project_id=1)
    start_run_thread(rec["run_id"], plan, user_id="u", project_id=1)
    deadline = time.time() + 3
    final = None
    while time.time() < deadline:
        final = get_run(rec["run_id"])
        if final and final.get("status") in ("success", "failed"):
            break
        time.sleep(0.05)
    assert final and final["status"] == "success"
    assert final["result"]["success"] is True


def test_approve_token_allows_l2_retry():
    stage = {"id": "wipe", "risk_level": "L2", "label": "清数据"}
    d1 = evaluate_stage_risk(stage, plan={"plan_id": "p"})
    assert d1.ok is False
    ok, token = approve_risk(d1.approval_id, approver="lead")
    assert ok and token
    stage2 = dict(stage)
    stage2["approval_token"] = token
    d2 = evaluate_stage_risk(stage2, plan={"plan_id": "p", "approvals": {"wipe": token}})
    assert d2.ok is True
