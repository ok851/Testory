# -*- coding: utf-8 -*-
"""Phase B-2：Trace / 证据包导出。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ai_modules.execute.cross_end_run_audit import record_cross_end_execution
from ai_modules.execute.trace_pack import (
    build_trace_document,
    export_trace_pack,
    write_trace_pack_dir,
    zip_trace_pack,
)
from ai_modules.agent_teams.team_runner import run_with_injected_execute
from database import Database


@pytest.fixture
def db(tmp_path):
    Database._schema_initialized = False
    inst = Database(str(tmp_path / "b2.db"))
    yield inst
    Database._schema_initialized = False


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path / "uat"))
    return tmp_path / "uat"


def test_export_requires_source():
    out = export_trace_pack()
    assert out["ok"] is False
    assert out["error_code"] == "TRACE_SOURCE_REQUIRED"


def test_pack_from_audit_success(db, data_dir, tmp_path):
    result = {
        "success": True,
        "gate_passed": True,
        "plan_id": "p-trace",
        "variables": {"order_id": "O1", "token": "sec"},
        "stage_results": [
            {"stage_id": "s1", "ok_assert": True, "layer": "api", "elapsed_ms": 10},
            {
                "stage_id": "s2",
                "ok_assert": True,
                "layer": "web",
                "screenshot_path": str(tmp_path / "no-such.png"),
            },
        ],
    }
    audit = record_cross_end_execution(
        result, plan={"scenario": "trace-ok"}, project_id=1, db=db
    )
    exported = export_trace_pack(
        audit_id=audit["audit_id"],
        run_history_id=audit["run_history_id"],
        db=db,
        out_dir=tmp_path / "pack1",
        make_zip=True,
    )
    assert exported["ok"] is True
    assert exported["status"] == "success"
    pack = Path(exported["pack_dir"])
    assert (pack / "manifest.json").is_file()
    assert (pack / "trace.json").is_file()
    assert (pack / "report.json").is_file()
    assert (pack / "SUMMARY.md").is_file()
    shots = json.loads((pack / "screenshots" / "index.json").read_text(encoding="utf-8"))
    assert any(s.get("status") == "missing" for s in shots)
    # zip 可读
    zpath = Path(exported["zip_path"])
    assert zpath.is_file()
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "trace.json" in names
    # 脱敏
    variables = json.loads((pack / "variables.json").read_text(encoding="utf-8"))
    assert variables.get("token") == "***"


def test_pack_failed_status_not_green(db, data_dir, tmp_path):
    result = {
        "success": False,
        "error": "断言失败",
        "error_code": "CROSS_END_ASSERT_FAILED",
        "stage_results": [
            {"stage_id": "s1", "ok_assert": False, "error": "mismatch", "layer": "web"},
        ],
    }
    audit = record_cross_end_execution(result, plan={"scenario": "trace-fail"}, db=db)
    doc = build_trace_document(audit_id=audit["audit_id"], run_history_id=audit["run_history_id"], db=db)
    assert doc["manifest"]["status"] == "failed"
    assert doc["report"]["passed"] is False


def test_empty_success_without_evidence_demoted(tmp_path, data_dir, db):
    # 伪造空 audit：success 但无阶段
    from ai_modules.execute import cross_end_run_audit as audit_mod

    fake = {
        "audit_id": "cea-emptygreen",
        "status": "success",
        "error": "",
        "flow_name": "empty",
        "test_type": "cross_end",
        "stage_results": [],
        "meta": {"variables": {}},
        "screenshots": "",
    }
    path = audit_mod._audit_dir() / "cea-emptygreen.json"
    path.write_text(json.dumps(fake), encoding="utf-8")
    doc = build_trace_document(audit_id="cea-emptygreen", db=db)
    assert doc["manifest"]["status"] == "failed"
    assert "缺少" in doc["manifest"]["reason"] or "证据" in doc["manifest"]["reason"]


def test_agent_run_trace_pack(db, data_dir, tmp_path, monkeypatch):
    from ai_modules.execute import cross_end_run_audit as audit_mod

    real = audit_mod.record_cross_end_execution

    def with_db(result, **kwargs):
        kwargs["db"] = db
        return real(result, **kwargs)

    monkeypatch.setattr(audit_mod, "record_cross_end_execution", with_db)
    monkeypatch.setattr(
        "ai_modules.execute.cross_end_run_audit.record_cross_end_execution",
        with_db,
    )

    def fake_execute(plan, **kwargs):
        return {
            "success": True,
            "gate_passed": True,
            "variables": {"a": 1},
            "stage_results": [{"stage_id": "s1", "ok_assert": True, "elapsed_ms": 5}],
        }

    state = run_with_injected_execute(
        fake_execute,
        plan={"plan_id": "t1", "scenario": "agent-trace", "stages": [{"id": "s1"}]},
        record_history=True,
        project_id=9,
    )
    exported = export_trace_pack(
        agent_run_id=state.run_id,
        db=db,
        out_dir=tmp_path / "agent-pack",
        make_zip=True,
    )
    assert exported["ok"] is True
    trace = json.loads(Path(exported["pack_dir"], "trace.json").read_text(encoding="utf-8"))
    agents = {e.get("agent") for e in trace["events"] if e.get("source") == "agent_teams"}
    assert "Planner" in agents
    assert "Verifier" in agents


def test_present_screenshot_copied(db, data_dir, tmp_path):
    shot = tmp_path / "real.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    result = {
        "success": True,
        "gate_passed": True,
        "stage_results": [
            {"stage_id": "s1", "ok_assert": True, "screenshot_path": str(shot)},
        ],
    }
    audit = record_cross_end_execution(result, plan={"scenario": "shot"}, db=db)
    exported = export_trace_pack(
        audit_id=audit["audit_id"],
        out_dir=tmp_path / "pack-shot",
        make_zip=False,
        db=db,
    )
    packed = Path(exported["pack_dir"]) / "screenshots" / "real.png"
    assert packed.is_file()
    idx = json.loads((Path(exported["pack_dir"]) / "screenshots" / "index.json").read_text(encoding="utf-8"))
    assert idx[0]["status"] == "present"
    assert idx[0].get("packed_as")


def test_zip_roundtrip_helpers(tmp_path, data_dir):
    doc = {
        "manifest": {"pack_id": "p-zip", "status": "failed", "reason": "x", "completeness": "complete", "refs": {}, "counts": {}},
        "trace": {"schema": "testory.json_trace/v1", "pack_id": "p-zip", "events": []},
        "report": {"passed": False},
        "stage_results": [],
        "variables": {},
        "screenshots": [],
        "history": {},
    }
    d = write_trace_pack_dir(doc, out_dir=tmp_path / "p-zip")
    z = zip_trace_pack(d)
    assert z.is_file() and z.stat().st_size > 0
