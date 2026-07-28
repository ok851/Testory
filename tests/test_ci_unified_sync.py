# -*- coding: utf-8 -*-
"""统一门禁：Testory ↔ Jenkins 同步。"""

from __future__ import annotations


def test_both_must_pass_waits_for_both_sides(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise import ci_unified_sync as sync

    rec = sync.create_sync(policy="both_must_pass", testory_run_id="run-1", jenkins_job="demo")
    assert rec["status"] == "running"
    assert rec["unified_gate_passed"] is False

    rec = sync.apply_testory_result(rec["sync_id"], run_id="run-1", status="success", gate_passed=True)
    assert rec["status"] == "running"
    assert rec["unified_gate_passed"] is False

    rec = sync.apply_jenkins_result(rec["sync_id"], result="SUCCESS", build_url="http://j/job/1/")
    assert rec["status"] == "success"
    assert rec["unified_gate_passed"] is True


def test_both_must_pass_fails_if_jenkins_red(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise import ci_unified_sync as sync

    rec = sync.create_sync(policy="both_must_pass")
    sync.apply_testory_result(rec["sync_id"], run_id="r1", status="success", gate_passed=True)
    rec = sync.apply_jenkins_result(rec["sync_id"], result="FAILURE")
    assert rec["status"] == "failed"
    assert rec["unified_gate_passed"] is False


def test_both_must_pass_fails_if_testory_red(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise import ci_unified_sync as sync

    rec = sync.create_sync(policy="both_must_pass")
    sync.apply_testory_result(rec["sync_id"], run_id="r1", status="failed", gate_passed=False)
    rec = sync.apply_jenkins_result(rec["sync_id"], result="SUCCESS")
    assert rec["unified_gate_passed"] is False
    assert rec["status"] == "failed"


def test_jenkins_only_side(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise import ci_unified_sync as sync

    rec = sync.create_sync(policy="both_must_pass", jenkins_job="only-j")
    rec = sync.apply_jenkins_result(rec["sync_id"], result="SUCCESS")
    assert rec["unified_gate_passed"] is True


def test_start_unified_sync_triggers_jenkins(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JENKINS_URL", "http://jenkins.example")
    monkeypatch.setenv("JENKINS_USER", "u")
    monkeypatch.setenv("JENKINS_API_TOKEN", "t")
    from ai_modules.enterprise import jenkins_trigger as jt
    from ai_modules.enterprise import ci_unified_sync as sync

    def _req(method, url, **kwargs):
        if "crumbIssuer" in url:
            return 200, b'{"crumb":"c","crumbRequestField":"Jenkins-Crumb"}', {}
        if "/queue/" in url:
            return 200, b'{"executable":{"url":"http://jenkins.example/job/demo/7/","number":7}}', {}
        if "/job/demo/7" in url or "api/json?tree" in url:
            return 200, b'{"number":7,"result":"SUCCESS","building":false,"url":"http://jenkins.example/job/demo/7/"}', {}
        return 201, b"", {"location": "http://jenkins.example/queue/item/1/"}

    monkeypatch.setattr(jt, "_request", _req)
    # 避免后台线程长时间跑
    monkeypatch.setattr(sync, "_ensure_poller", lambda _sid: None)

    out = sync.start_unified_sync(jenkins_job="demo", auto_poll=False)
    assert out.get("ok") is True
    s = out["sync"]
    assert s["jenkins"].get("queue_url")
    # refresh 应解析到 SUCCESS
    refreshed = sync.refresh_sync(s["sync_id"])
    assert refreshed["jenkins"].get("result") == "SUCCESS"
    assert refreshed["unified_gate_passed"] is True


def test_resolve_jenkins_build_status(monkeypatch):
    monkeypatch.setenv("JENKINS_URL", "http://jenkins.example")
    monkeypatch.setenv("JENKINS_USER", "u")
    monkeypatch.setenv("JENKINS_API_TOKEN", "t")
    from ai_modules.enterprise import jenkins_trigger as jt

    calls = []

    def _req(method, url, **kwargs):
        calls.append(url)
        if "/queue/" in url:
            return 200, b'{"executable":{"url":"http://jenkins.example/job/x/3/","number":3}}', {}
        return 200, b'{"number":3,"result":"FAILURE","building":false,"url":"http://jenkins.example/job/x/3/"}', {}

    monkeypatch.setattr(jt, "_request", _req)
    st = jt.resolve_jenkins_build_status(queue_url="http://jenkins.example/queue/item/2/")
    assert st.get("terminal") is True
    assert st.get("result") == "FAILURE"
