# -*- coding: utf-8 -*-
"""Phase B-3：HITL 事件入 Trace / 阶段结果。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from modules.ai.agent_hitl import (
    cancel_hitl_gate,
    get_hitl_events,
    hitl_outcome_from_events,
    open_hitl_gate,
    reset_hitl_state_for_tests,
    resume_hitl_gate,
    wait_hitl_gate,
)
from ai_modules.execute.cross_end_run_audit import record_cross_end_execution
from ai_modules.execute.orchestrator import execute_cross_end_plan
from ai_modules.execute.trace_pack import build_trace_document, export_trace_pack
from database import Database


@pytest.fixture(autouse=True)
def _clean_hitl():
    reset_hitl_state_for_tests()
    yield
    reset_hitl_state_for_tests()


@pytest.fixture
def db(tmp_path):
    Database._schema_initialized = False
    inst = Database(str(tmp_path / "b3.db"))
    yield inst
    Database._schema_initialized = False


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path / "uat"))
    return tmp_path / "uat"


def test_open_resume_emits_events():
    open_hitl_gate("g1", reason="验证码")
    assert resume_hitl_gate("g1") is True
    evs = get_hitl_events(gate_id="g1")
    kinds = [e["kind"] for e in evs]
    assert "opened" in kinds
    assert "resumed" in kinds
    assert hitl_outcome_from_events(evs) == "resumed"


def test_timeout_emits_wait_timed_out():
    open_hitl_gate("g-to", reason="login")
    assert wait_hitl_gate("g-to", timeout_s=0.25, poll_interval_s=0.05) is False
    evs = get_hitl_events(gate_id="g-to")
    kinds = [e["kind"] for e in evs]
    assert "wait_started" in kinds
    assert "wait_timed_out" in kinds
    assert hitl_outcome_from_events(evs) == "timed_out"


def test_cancel_emits_wait_cancelled():
    open_hitl_gate("g-c", reason="x")

    def _cancel():
        time.sleep(0.12)
        cancel_hitl_gate("g-c")

    t = threading.Thread(target=_cancel)
    t.start()
    assert wait_hitl_gate("g-c", timeout_s=2.0, poll_interval_s=0.05) is False
    t.join(timeout=2)
    evs = get_hitl_events(gate_id="g-c")
    assert hitl_outcome_from_events(evs) == "cancelled"
    assert any(e["kind"] == "cancelled" for e in evs)


def test_orchestrator_hitl_timeout_attaches_events(data_dir):
    plan = {
        "plan_id": "hitl-to",
        "scenario": "HITL timeout",
        "stages": [
            {
                "id": "stage-hitl",
                "layer": "hitl",
                "label": "验证码",
                "hitl": {"prompt": "请完成验证码", "timeout_s": 0.3},
                "poll_interval_s": 0.05,
            }
        ],
    }
    out = execute_cross_end_plan(plan, acquire_lock=False, record_history=False)
    assert out.get("success") is False
    sr = (out.get("stage_results") or [])[0]
    assert sr.get("ok_assert") is False
    assert sr.get("hitl_events")
    assert sr.get("hitl_outcome") == "timed_out"
    assert sr.get("error_code") in ("HITL_TIMEOUT", "HITL_TIMEOUT_OR_CANCEL")


def test_orchestrator_hitl_resume_in_trace(db, data_dir, tmp_path):
    plan = {
        "plan_id": "hitl-ok",
        "scenario": "HITL resume",
        "stages": [
            {
                "id": "stage-hitl",
                "layer": "hitl",
                "gate_id": "cross_end:hitl-ok:stage-hitl",
                "hitl": {"prompt": "扫码登录", "timeout_s": 2.0},
                "poll_interval_s": 0.05,
            }
        ],
    }

    def _resume():
        time.sleep(0.2)
        assert resume_hitl_gate("cross_end:hitl-ok:stage-hitl") is True

    t = threading.Thread(target=_resume)
    t.start()
    out = execute_cross_end_plan(
        plan, acquire_lock=False, project_id=1, record_history=False
    )
    t.join(timeout=3)
    assert out.get("success") is True
    sr = (out.get("stage_results") or [])[0]
    assert sr.get("hitl_outcome") == "resumed"
    assert any(e.get("kind") == "wait_resumed" for e in sr.get("hitl_events") or [])

    audit = record_cross_end_execution(out, plan=plan, db=db)
    exported = export_trace_pack(
        audit_id=audit["audit_id"],
        run_history_id=audit["run_history_id"],
        db=db,
        out_dir=tmp_path / "hitl-pack",
        make_zip=False,
    )
    assert exported["ok"] is True
    pack = Path(exported["pack_dir"])
    hitl_file = pack / "hitl_events.json"
    assert hitl_file.is_file()
    hitl_rows = json.loads(hitl_file.read_text(encoding="utf-8"))
    assert any(r.get("kind") == "opened" for r in hitl_rows)
    trace = json.loads((pack / "trace.json").read_text(encoding="utf-8"))
    assert any(e.get("source") == "hitl" for e in trace["events"])


def test_trace_includes_hitl_on_failure_pack(db, data_dir, tmp_path):
    result = {
        "success": False,
        "error_code": "HITL_TIMEOUT",
        "error": "timeout",
        "stage_results": [
            {
                "stage_id": "h1",
                "layer": "hitl",
                "ok_assert": False,
                "hitl_gate_id": "g-x",
                "hitl_outcome": "timed_out",
                "hitl_events": [
                    {"kind": "opened", "gate_id": "g-x", "at": "t1", "reason": "captcha"},
                    {"kind": "wait_timed_out", "gate_id": "g-x", "at": "t2", "reason": "captcha"},
                ],
                "error": "HITL timeout",
            }
        ],
    }
    audit = record_cross_end_execution(result, plan={"scenario": "hitl-fail"}, db=db)
    meta = json.loads(db.get_run_history_detail(audit["run_history_id"])["extracted_text"])
    assert meta.get("hitl")
    doc = build_trace_document(audit_id=audit["audit_id"], db=db)
    assert any(e.get("source") == "hitl" for e in doc["trace"]["events"])
    assert doc["manifest"]["status"] == "failed"


def test_events_isolated_by_gate():
    open_hitl_gate("ga", reason="a")
    open_hitl_gate("gb", reason="b")
    resume_hitl_gate("ga")
    a = get_hitl_events(gate_id="ga")
    b = get_hitl_events(gate_id="gb")
    assert all(e["gate_id"] == "ga" for e in a)
    assert all(e["gate_id"] == "gb" for e in b)
    assert hitl_outcome_from_events(a) == "resumed"
    assert hitl_outcome_from_events(b) == "unknown"  # only opened
