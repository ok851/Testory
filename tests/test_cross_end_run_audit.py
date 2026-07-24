# -*- coding: utf-8 -*-
"""Phase B-1：跨端 / AgentTeams 诚实写入统一 run_history。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_modules.execute.cross_end_run_audit import (
    build_history_error,
    normalize_cross_end_history_status,
    persist_to_database,
    record_cross_end_execution,
    redact_vars_for_history,
    stage_results_to_step_rows,
)
from database import Database


@pytest.fixture
def db(tmp_path):
    Database._schema_initialized = False
    inst = Database(str(tmp_path / "b1.db"))
    yield inst
    Database._schema_initialized = False


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path / "uat"))
    return tmp_path / "uat"


def test_normalize_status_only_success_is_green():
    assert normalize_cross_end_history_status({"success": True}) == "success"
    assert normalize_cross_end_history_status({"success": True, "gate_passed": False}) == "failed"
    assert normalize_cross_end_history_status({"success": False}) == "failed"
    assert normalize_cross_end_history_status(None) == "failed"
    assert normalize_cross_end_history_status({}) == "failed"


def test_redact_sensitive_vars():
    out = redact_vars_for_history({
        "order_id": "O1",
        "access_token": "secret-value",
        "password": "p",
    })
    assert out["order_id"] == "O1"
    assert out["access_token"] == "***"
    assert out["password"] == "***"


def test_failed_error_non_empty():
    err = build_history_error({
        "success": False,
        "error_code": "CROSS_END_ASSERT_FAILED",
        "error": "断言失败",
    })
    assert "CROSS_END_ASSERT_FAILED" in err
    assert "断言失败" in err


def test_stage_rows_map_skip_and_fail():
    rows = stage_results_to_step_rows([
        {"stage_id": "a", "ok_assert": True, "layer": "api", "elapsed_ms": 1000},
        {"stage_id": "b", "ok_assert": False, "error": "boom", "layer": "web"},
        {"stage_id": "c", "ok_assert": False, "skipped_failure": True, "layer": "web"},
    ])
    assert rows[0]["status"] == "success"
    assert rows[1]["status"] == "failed" and rows[1]["error"] == "boom"
    assert rows[2]["status"] == "skipped"


def test_record_success_and_steps(db, data_dir):
    result = {
        "success": True,
        "gate_passed": True,
        "plan_id": "p1",
        "variables": {"order_id": "O1", "token": "abc"},
        "stage_results": [
            {"stage_id": "s1", "ok_assert": True, "layer": "api", "elapsed_ms": 50},
            {
                "stage_id": "s2",
                "ok_assert": True,
                "layer": "web",
                "elapsed_ms": 80,
                "screenshot_path": "/tmp/x.png",
            },
        ],
        "assertion_passed": 1,
        "assertion_failed": 0,
    }
    audit = record_cross_end_execution(
        result,
        plan={"plan_id": "p1", "scenario": "下单一致"},
        project_id=7,
        db=db,
    )
    assert audit["ok"] is True
    assert audit["status"] == "success"
    assert result["run_history_id"] == audit["run_history_id"]
    assert result["history_status"] == "success"
    hid = audit["run_history_id"]
    detail = db.get_run_history_detail(hid)
    assert detail["status"] == "success"
    assert detail["test_type"] == "cross_end"
    assert detail["case_id"] is None
    assert detail["flow_name"] == "下单一致"
    assert detail["project_id"] == 7
    assert not detail["error"]
    steps = db.get_step_results(hid)
    assert len(steps) == 2
    assert steps[0]["status"] == "success"
    assert Path(result["audit_path"]).is_file()
    meta = json.loads(detail["extracted_text"])
    assert meta["variables"]["order_id"] == "O1"
    assert meta["variables"]["token"] == "***"


def test_record_failure_never_success(db, data_dir):
    result = {
        "success": False,
        "gate_passed": False,
        "error": "无浏览器",
        "error_code": "NO_BROWSER_PAGE",
        "stage_results": [
            {"stage_id": "s1", "ok_assert": False, "error": "无 page", "layer": "web"},
        ],
    }
    audit = record_cross_end_execution(result, plan={"scenario": "fail-demo"}, db=db)
    assert audit["status"] == "failed"
    detail = db.get_run_history_detail(audit["run_history_id"])
    assert detail["status"] == "failed"
    assert detail["error"]
    assert "NO_BROWSER_PAGE" in detail["error"] or "无浏览器" in detail["error"]


def test_orphan_prune_keeps_cross_end(db, data_dir):
    result = {
        "success": False,
        "error": "busy",
        "error_code": "EXECUTION_LOCK_BUSY",
        "stage_results": [],
    }
    audit = record_cross_end_execution(result, plan={"scenario": "lock"}, db=db)
    hid = audit["run_history_id"]
    bad = db.create_run_history(None, "failed", 0.1, "orphan", "", "", test_type="web")
    n = db.prune_orphan_run_history()
    assert n >= 1
    assert db.get_run_history_detail(hid) is not None
    assert db.get_run_history_detail(bad) is None


def test_project_filter_includes_cross_end(db, data_dir):
    record_cross_end_execution(
        {"success": True, "gate_passed": True, "stage_results": []},
        plan={"scenario": "proj-a"},
        project_id=42,
        db=db,
    )
    rows = db.get_all_run_history(page=1, page_size=20, project_id=42)
    assert any(r.get("test_type") == "cross_end" and r.get("project_id") == 42 for r in rows)
    assert db.get_run_history_count(project_id=42) >= 1
    assert db.get_run_history_count(project_id=99) == 0


def test_record_lock_busy_failed(db, data_dir):
    result = {
        "success": False,
        "error_code": "EXECUTION_LOCK_BUSY",
        "error": "busy",
        "lock": "busy",
        "stage_results": [],
    }
    audit = record_cross_end_execution(result, db=db)
    assert audit["status"] == "failed"
    assert db.get_run_history_detail(audit["run_history_id"])["status"] == "failed"


def test_execute_cross_end_plan_wires_audit(db, data_dir, monkeypatch):
    from ai_modules.execute import cross_end_run_audit as audit_mod
    from ai_modules.execute import orchestrator as orch

    monkeypatch.setattr(
        orch,
        "_execute_cross_end_plan_impl",
        lambda plan, **kw: {
            "success": True,
            "gate_passed": True,
            "plan_id": plan.get("plan_id"),
            "variables": {"x": 1},
            "stage_results": [{"stage_id": "s1", "ok_assert": True, "elapsed_ms": 10}],
        },
    )
    monkeypatch.setattr(
        audit_mod,
        "persist_to_database",
        lambda record, db=None: persist_to_database(record, db=db),
    )

    # 强制走我们的 db：包装 record_cross_end_execution 调用链中的 persist
    real_record = audit_mod.record_cross_end_execution

    def record_with_db(result, **kwargs):
        kwargs["db"] = db
        return real_record(result, **kwargs)

    monkeypatch.setattr(audit_mod, "record_cross_end_execution", record_with_db)
    # orchestrator imports inside _audit — patch module attribute used at call time
    monkeypatch.setattr(
        "ai_modules.execute.cross_end_run_audit.record_cross_end_execution",
        record_with_db,
    )

    out = orch.execute_cross_end_plan(
        {"plan_id": "px", "scenario": "wired", "stages": [{"id": "s1"}]},
        acquire_lock=False,
        project_id=3,
        user_id="u1",
    )
    assert out.get("success") is True
    assert out.get("run_history_id")
    detail = db.get_run_history_detail(out["run_history_id"])
    assert detail["test_type"] == "cross_end"
    assert detail["status"] == "success"
    assert detail["project_id"] == 3


def test_agent_teams_records_agent_type(db, data_dir, monkeypatch):
    from ai_modules.agent_teams.team_runner import run_with_injected_execute
    from ai_modules.execute import cross_end_run_audit as audit_mod

    real_record = audit_mod.record_cross_end_execution

    def record_with_db(result, **kwargs):
        kwargs["db"] = db
        return real_record(result, **kwargs)

    monkeypatch.setattr(audit_mod, "record_cross_end_execution", record_with_db)
    monkeypatch.setattr(
        "ai_modules.execute.cross_end_run_audit.record_cross_end_execution",
        record_with_db,
    )

    def fake_execute(plan, **kwargs):
        # Agent 路径应关闭跨端重复记账
        assert kwargs.get("record_history") is False
        return {
            "success": True,
            "gate_passed": True,
            "variables": {"a": 1},
            "stage_results": [{"stage_id": "s1", "ok_assert": True}],
            "assertion_passed": 0,
            "assertion_failed": 0,
        }

    state = run_with_injected_execute(
        fake_execute,
        plan={
            "plan_id": "at1",
            "scenario": "agent hist",
            "stages": [{"id": "s1", "layer": "api"}],
        },
        project_id=5,
        record_history=True,
    )
    assert state.status == "success"
    rows = db.get_all_run_history(page=1, page_size=50, project_id=5)
    agent_rows = [r for r in rows if r.get("test_type") == "agent_teams"]
    assert len(agent_rows) == 1
    assert agent_rows[0]["status"] == "success"
    # 不应因 Executor 再写一条 cross_end
    cross_rows = [r for r in rows if r.get("test_type") == "cross_end"]
    assert cross_rows == []


def test_success_false_cannot_force_history_success(db, data_dir):
    """即便调用方误标，record 层也应 failed。"""
    result = {"success": False, "stage_results": []}
    audit = record_cross_end_execution(result, db=db)
    assert audit["status"] == "failed"
    assert result["history_status"] == "failed"
