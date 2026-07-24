# -*- coding: utf-8 -*-
"""Phase R10：RiskGuard L0/L1/L2 与编排门禁。"""

from __future__ import annotations

import pytest

from ai_modules.security.risk_guard import (
    approve_risk,
    classify_stage,
    deny_risk,
    evaluate_stage_risk,
    get_risk_events,
    request_approval,
    reset_risk_guard_for_tests,
)
from ai_modules.execute.orchestrator import execute_cross_end_plan


@pytest.fixture(autouse=True)
def _clean():
    reset_risk_guard_for_tests()
    yield
    reset_risk_guard_for_tests()


def test_classify_explicit_and_readonly_api():
    assert classify_stage({"risk_level": "L2", "layer": "api"}) == "L2"
    assert classify_stage({
        "layer": "api",
        "request": {"method": "GET", "url": "https://x/orders/1"},
    }) == "L0"
    assert classify_stage({
        "layer": "web",
        "actions": [{"type": "click", "selector": "#ok"}],
    }) == "L1"
    assert classify_stage({
        "id": "wipe-db",
        "layer": "api",
        "label": "clear_data for demo",
        "request": {"method": "POST", "url": "/admin/clear_data"},
    }) == "L2"
    # cleanup  alone 不自动 L2
    assert classify_stage({"cleanup": True, "layer": "api", "id": "c1"}) == "L1"


def test_l2_requires_approval_then_token_allows():
    stage = {"id": "stage-wipe", "risk_level": "L2", "label": "清数据"}
    d1 = evaluate_stage_risk(stage, plan={"plan_id": "p1"})
    assert d1.ok is False
    assert d1.decision == "require_approval"
    assert d1.error_code == "RISK_APPROVAL_REQUIRED"
    assert d1.approval_id

    ok, token = approve_risk(d1.approval_id, approver="qa-lead")
    assert ok and token
    stage2 = dict(stage)
    stage2["approval_token"] = token
    d2 = evaluate_stage_risk(stage2, plan={"plan_id": "p1"})
    assert d2.ok is True
    assert d2.decision == "allow"
    kinds = [e["kind"] for e in get_risk_events(stage_id="stage-wipe")]
    assert "approval_requested" in kinds
    assert "approval_granted" in kinds
    assert "risk_allowed" in kinds


def test_denied_approval_blocks():
    rec = request_approval(stage_id="s-deny", level="L2", reason="x")
    assert deny_risk(rec["approval_id"], reason="no")
    stage = {
        "id": "s-deny",
        "risk_level": "L2",
        "approval_token": "not-a-real-token",
    }
    d = evaluate_stage_risk(stage)
    assert d.ok is False
    assert d.error_code == "RISK_TOKEN_INVALID"


def test_orchestrator_blocks_l2_without_token():
    plan = {
        "plan_id": "risk-block",
        "stages": [
            {
                "id": "stage-api",
                "layer": "api",
                "request": {"method": "GET", "url": "https://demo.local/ok"},
                "assert": {"status_in": [200]},
            },
            {
                "id": "stage-l2",
                "layer": "api",
                "risk_level": "L2",
                "risk_action": "clear_data",
                "label": "清测试数据",
                "request": {"method": "POST", "url": "https://demo.local/admin/clear"},
            },
        ],
    }
    # mock API adapter via patching execute path: first stage would call real HTTP —
    # use only L2 stage to avoid network
    plan2 = {
        "plan_id": "risk-only-l2",
        "stages": [plan["stages"][1]],
    }
    out = execute_cross_end_plan(plan2, acquire_lock=False, record_history=False)
    assert out.get("success") is False
    assert out.get("error_code") == "RISK_APPROVAL_REQUIRED" or any(
        (sr or {}).get("error_code") == "RISK_APPROVAL_REQUIRED"
        for sr in out.get("stage_results") or []
    )
    sr = (out.get("stage_results") or [])[0]
    assert sr.get("ok_assert") is False
    assert sr.get("risk_level") == "L2"
    assert sr.get("risk_approval_id")


def test_orchestrator_allows_l2_with_plan_approvals():
    stage = {
        "id": "stage-l2-ok",
        "layer": "api",
        "risk_level": "L2",
        "label": "经审批的清理",
        "request": {"method": "POST", "url": "https://demo.local/admin/clear"},
    }
    # 预建审批
    rec = request_approval(stage_id="stage-l2-ok", level="L2", reason="demo")
    ok, token = approve_risk(rec["approval_id"], approver="lead")
    assert ok
    plan = {
        "plan_id": "risk-ok",
        "approvals": {"stage-l2-ok": token},
        "stages": [stage],
    }
    # API 会真实请求失败——但 RiskGuard 应先通过；网络失败仍非假绿
    out = execute_cross_end_plan(plan, acquire_lock=False, record_history=False)
    sr = (out.get("stage_results") or [])[0]
    assert sr.get("risk_decision") == "allow"
    assert sr.get("risk_level") == "L2"
    # 无真实服务时 API 失败是预期；不得因缺审批而挡
    assert sr.get("error_code") != "RISK_APPROVAL_REQUIRED"
