# -*- coding: utf-8 -*-
"""历史详情 enrichment：门禁摘要 / 证据 / CI 链接。"""

from __future__ import annotations

from ai_modules.execute.history_ops_summary import (
    enrich_run_history_record,
    merge_ci_meta_into_expected,
)
from ai_modules.execute.cross_end_run_audit import build_audit_record


def test_enrich_cross_end_history_shows_hitl_risk_and_audit():
    result = {
        "success": False,
        "error_code": "RISK_APPROVAL_REQUIRED",
        "gate_passed": False,
        "plan_id": "p1",
        "stage_results": [
            {
                "stage_id": "h1",
                "layer": "hitl",
                "ok_assert": True,
                "hitl_gate_id": "g1",
                "hitl_outcome": "resumed",
                "hitl_events": [{"kind": "wait_resumed"}],
            },
            {
                "stage_id": "l2",
                "ok_assert": False,
                "risk_level": "L2",
                "risk_decision": "require_approval",
                "risk_approval_id": "risk-abc",
                "risk_events": [{"kind": "risk_require_approval"}],
                "error_code": "RISK_APPROVAL_REQUIRED",
            },
        ],
    }
    audit = build_audit_record(result, plan={"plan_id": "p1", "scenario": "s"}, project_id=1)
    assert audit["audit_id"].startswith("cea-")
    exp = __import__("json").loads(audit["expected_text"])
    assert exp.get("audit_id") == audit["audit_id"]

    row = {
        "id": 42,
        "status": "failed",
        "test_type": "cross_end",
        "extracted_text": audit["extracted_text"],
        "expected_text": audit["expected_text"],
        "error": "L2",
        "case_name": "s",
    }
    enriched = enrich_run_history_record(row)
    assert enriched["ops_summary"]["hitl_count"] == 1
    assert enriched["ops_summary"]["risk_count"] == 1
    assert enriched["links"]["audit_id"] == audit["audit_id"]
    assert "audit_id=" in (enriched["links"]["trace_export_url"] or "")
    assert "HITL" in (enriched.get("output_preview") or "")


def test_merge_ci_meta_into_expected():
    raw = merge_ci_meta_into_expected(
        "",
        ci_run_id="cir-1",
        build_id="Jenkins-88",
        trigger_source="jenkins",
    )
    data = __import__("json").loads(raw)
    assert data["ci_run_id"] == "cir-1"
    assert data["build_id"] == "Jenkins-88"
    enriched = enrich_run_history_record({
        "id": 7,
        "status": "success",
        "test_type": "web",
        "extracted_text": "",
        "expected_text": raw,
    })
    assert enriched["links"]["build_id"] == "Jenkins-88"
    assert enriched["links"]["ci_run_url"].endswith("/cir-1")
    assert "build=" in (enriched.get("output_preview") or "")


def test_aggregate_ops_governance_counts_gates_without_greenwash():
    from ai_modules.execute.history_ops_summary import aggregate_ops_governance

    blocked = build_audit_record(
        {
            "success": False,
            "error_code": "RISK_APPROVAL_REQUIRED",
            "gate_passed": False,
            "plan_id": "p2",
            "stage_results": [
                {
                    "stage_id": "l2",
                    "ok_assert": False,
                    "risk_level": "L2",
                    "risk_decision": "require_approval",
                    "risk_approval_id": "r1",
                    "risk_events": [{"kind": "risk_require_approval"}],
                    "error_code": "RISK_APPROVAL_REQUIRED",
                }
            ],
        },
        plan={"plan_id": "p2"},
        project_id=1,
    )
    ok_ci = {
        "id": 2,
        "status": "success",
        "test_type": "web",
        "extracted_text": "",
        "expected_text": merge_ci_meta_into_expected("", ci_run_id="c1", build_id="B1"),
        "case_name": "ui",
    }
    rows = [
        {
            "id": 1,
            "status": "failed",
            "test_type": "cross_end",
            "extracted_text": blocked["extracted_text"],
            "expected_text": blocked["expected_text"],
            "case_name": "scenario",
            "created_at": "2026-07-24 12:00:00",
        },
        ok_ci,
    ]
    agg = aggregate_ops_governance(rows, recent_limit=5)
    assert agg["scanned_runs"] == 2
    assert agg["cross_end_runs"] == 1
    assert agg["with_risk"] == 1
    assert agg["gate_blocked"] == 1
    assert agg["with_ci"] >= 1
    assert agg["with_evidence"] >= 1
    assert agg["recent_gate_events"][0]["error_code"] == "RISK_APPROVAL_REQUIRED"
    assert any(x["code"] == "RISK_APPROVAL_REQUIRED" for x in agg["top_error_codes"])
