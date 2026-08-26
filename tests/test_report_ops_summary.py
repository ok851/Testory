# -*- coding: utf-8 -*-
"""报告治理看板：含无 case 的跨端历史。"""

from __future__ import annotations

import json
import os
import tempfile

from database import Database
from modules.integration.test_report import TestReportGenerator
from ai_modules.execute.cross_end_run_audit import build_audit_record, persist_to_database


def test_ops_governance_includes_orphan_cross_end():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    Database._schema_initialized = False
    try:
        db = Database(path)
        audit = build_audit_record(
            {
                "success": False,
                "error_code": "HITL_TIMEOUT",
                "gate_passed": False,
                "plan_id": "px",
                "stage_results": [
                    {
                        "stage_id": "h",
                        "layer": "hitl",
                        "ok_assert": False,
                        "hitl_gate_id": "g-1",
                        "hitl_outcome": "timeout",
                        "hitl_events": [{"kind": "timeout"}],
                        "error_code": "HITL_TIMEOUT",
                    }
                ],
            },
            plan={"plan_id": "px", "scenario": "orphan-demo"},
            project_id=None,
        )
        rid = persist_to_database(audit, db=db)
        assert rid

        gen = TestReportGenerator(db=db)
        summary = gen.get_ops_governance_summary(case_category="cross_end")
        assert summary["scanned_runs"] >= 1
        assert summary["cross_end_runs"] >= 1
        assert summary["with_hitl"] >= 1
        assert summary["gate_blocked"] >= 1
        assert summary["recent_gate_events"]
        assert summary["recent_gate_events"][0]["error_code"] == "HITL_TIMEOUT"
    finally:
        Database._schema_initialized = False
        try:
            os.remove(path)
        except OSError:
            pass


def test_ops_category_filter_cross_end_excludes_plain_web():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    Database._schema_initialized = False
    try:
        db = Database(path)
        pid = db.create_project("ops-rpt", "t")
        cid = db.create_test_case_v2(pid, "c1", case_type="ui", platform="web")
        db.create_run_history(
            cid,
            "success",
            1.0,
            "",
            "",
            json.dumps({"build_id": "x"}),
            test_type="web",
            project_id=pid,
        )
        audit = build_audit_record(
            {
                "success": False,
                "error_code": "RISK_APPROVAL_REQUIRED",
                "gate_passed": False,
                "stage_results": [
                    {
                        "stage_id": "r",
                        "ok_assert": False,
                        "risk_level": "L2",
                        "risk_decision": "require_approval",
                        "risk_approval_id": "a1",
                        "risk_events": [{}],
                        "error_code": "RISK_APPROVAL_REQUIRED",
                    }
                ],
            },
            plan={"scenario": "ce"},
            project_id=pid,
        )
        persist_to_database(audit, db=db)

        gen = TestReportGenerator(db=db)
        only_ce = gen.get_ops_governance_summary(project_id=pid, case_category="cross_end")
        assert only_ce["cross_end_runs"] >= 1
        assert only_ce["scanned_runs"] == only_ce["cross_end_runs"] + only_ce.get(
            "agent_teams_runs", 0
        )
    finally:
        Database._schema_initialized = False
        try:
            os.remove(path)
        except OSError:
            pass


def test_overview_includes_orphan_cross_end_in_totals_and_failures():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    Database._schema_initialized = False
    try:
        db = Database(path)
        pid = db.create_project("ov-ce", "")
        cid = db.create_test_case_v2(pid, "ui-ok", case_type="ui", platform="web")
        db.create_run_history(cid, "success", 1.0, "", "", "", test_type="web", project_id=pid)
        audit = build_audit_record(
            {
                "success": False,
                "error_code": "HITL_TIMEOUT",
                "gate_passed": False,
                "stage_results": [
                    {
                        "stage_id": "h",
                        "layer": "hitl",
                        "ok_assert": False,
                        "hitl_gate_id": "g",
                        "hitl_outcome": "timeout",
                        "hitl_events": [{}],
                        "error_code": "HITL_TIMEOUT",
                    }
                ],
            },
            plan={"scenario": "fail-ce"},
            project_id=pid,
        )
        persist_to_database(audit, db=db)

        gen = TestReportGenerator(db=db)
        ov = gen.get_statistics_overview(project_id=pid)
        assert ov["includes_orphan_runs"] is True
        assert ov["orphan_cross_end_runs"] >= 1
        assert ov["total_runs"] >= 2
        assert ov["failed_runs"] >= 1
        assert ov["passed_runs"] >= 1
        # 不得因排除跨端而虚高通过率
        assert ov["pass_rate"] < 100.0

        dist = gen.get_status_distribution(project_id=pid)
        statuses = {d["status"]: d["count"] for d in dist}
        assert sum(statuses.values()) == ov["total_runs"]

        proj = gen.get_project_statistics(project_id=pid)
        assert len(proj) == 1
        assert proj[0]["total_runs"] >= 2
        assert proj[0]["failed_runs"] >= 1
    finally:
        Database._schema_initialized = False
        try:
            os.remove(path)
        except OSError:
            pass
