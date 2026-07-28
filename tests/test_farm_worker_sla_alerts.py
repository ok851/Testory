# -*- coding: utf-8 -*-
"""农场 Worker drain + SLA 阈值告警诚实性。"""

from __future__ import annotations


def test_drain_empty_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.farm_worker import drain_queued_jobs

    r = drain_queued_jobs(limit=5)
    assert r.get("ok") is True
    assert r.get("drained") == 0
    assert r.get("case_pass_claimed") is False
    assert r.get("parallel_suite_pass_claimed") is False


def test_drain_processes_queued_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.farm_jobs import enqueue_job, get_job
    from ai_modules.enterprise.farm_worker import drain_queued_jobs

    a = enqueue_job(job_type="noop", auto_run=False)
    b = enqueue_job(job_type="noop", auto_run=False)
    assert a["job"]["status"] == "queued"
    assert b["job"]["status"] == "queued"
    r = drain_queued_jobs(limit=10)
    assert r.get("drained") == 2
    assert r.get("succeeded") == 2
    assert r.get("failed") == 0
    assert r.get("parallel_suite_pass_claimed") is False
    assert get_job(a["job"]["job_id"])["status"] == "succeeded"
    assert get_job(b["job"]["job_id"])["status"] == "succeeded"


def test_sla_alerts_never_met(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLA_ALERT_MIN_SAMPLES", "3")
    monkeypatch.setenv("SLA_ALERT_LATENCY_P95_MS", "10")
    monkeypatch.setenv("SLA_ALERT_FAIL_RATIO", "0.3")
    from ai_modules.enterprise.sla_alerts import evaluate_sla_alerts
    from ai_modules.enterprise.sla_evidence import record_metric

    record_metric(kind="t", ok=False, latency_ms=100)
    record_metric(kind="t", ok=False, latency_ms=200)
    record_metric(kind="t", ok=True, latency_ms=50)
    alerts = evaluate_sla_alerts()
    assert alerts.get("sla_claim") is False
    assert alerts.get("sla_met") is False
    assert alerts.get("has_warning") is True
    codes = {a.get("code") for a in alerts.get("alerts") or []}
    assert "LATENCY_P95_HIGH" in codes or "FAIL_RATIO_HIGH" in codes


def test_sla_alerts_insufficient_samples(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLA_ALERT_MIN_SAMPLES", "5")
    from ai_modules.enterprise.sla_alerts import evaluate_sla_alerts
    from ai_modules.enterprise.sla_evidence import record_metric

    record_metric(kind="t", ok=True, latency_ms=1)
    alerts = evaluate_sla_alerts()
    assert alerts.get("sla_met") is False
    assert any(a.get("code") == "INSUFFICIENT_SAMPLES" for a in alerts.get("alerts") or [])
