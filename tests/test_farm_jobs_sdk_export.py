# -*- coding: utf-8 -*-
"""农场任务队列 + SDK bridge 导出。"""

from __future__ import annotations

from pathlib import Path


def test_enqueue_unsupported_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.farm_jobs import enqueue_job

    r = enqueue_job(job_type="parallel_suite", auto_run=False)
    assert r.get("ok") is False
    assert r.get("error_code") == "JOB_TYPE_UNSUPPORTED"
    assert r.get("case_pass_claimed") is not True


def test_noop_job_succeeds_without_case_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.farm_jobs import enqueue_job, get_job

    r = enqueue_job(job_type="noop", auto_run=True)
    assert r.get("ok") is True
    assert r.get("case_pass_claimed") is False
    job = r.get("job") or get_job(r["job"]["job_id"])
    assert job["status"] == "succeeded"


def test_probe_job_fails_without_node(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.farm_jobs import enqueue_job

    r = enqueue_job(job_type="probe", auto_run=True)
    assert r.get("ok") is False
    assert (r.get("job") or {}).get("status") == "failed"
    assert r.get("case_pass_claimed") is False


def test_live_health_job_fails_without_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DESKTOP_AGENT_GATEWAY_URL", raising=False)
    monkeypatch.setenv("DESKTOP_FARM_GATEWAY", "0")
    from ai_modules.enterprise.farm_jobs import enqueue_job

    r = enqueue_job(job_type="live_health", auto_run=True)
    assert r.get("ok") is False
    assert (r.get("job") or {}).get("status") == "failed"


def test_sdk_export_bundle(tmp_path):
    from ai_modules.agent_teams.sdk_bridge import export_sdk_events_bundle
    from ai_modules.agent_teams.test_run_state import TestRunState

    st = TestRunState.create(goal="export")
    st.emit(agent="Planner", kind="dispatch", message="p")
    st.emit(agent="Verifier", kind="complete", message="v")
    st.set_status("failed")
    out = export_sdk_events_bundle(st, out_dir=tmp_path / "sdk")
    assert out.get("ok") is True
    assert (tmp_path / "sdk" / "sdk_events.json").is_file()
    assert out["payload"].get("case_pass_claimed") is False
    assert len(out["payload"]["events"]) >= 2
