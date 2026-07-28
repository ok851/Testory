# -*- coding: utf-8 -*-
"""Jenkins 反向触发：未配置时诚实失败。"""

from __future__ import annotations


def test_jenkins_not_configured(monkeypatch):
    monkeypatch.delenv("JENKINS_URL", raising=False)
    monkeypatch.delenv("JENKINS_USER", raising=False)
    monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
    from ai_modules.enterprise.jenkins_trigger import jenkins_configured, trigger_jenkins_job

    assert jenkins_configured() is False
    r = trigger_jenkins_job(job_name="demo")
    assert r.get("ok") is False
    assert r.get("error_code") == "JENKINS_NOT_CONFIGURED"
    assert r.get("case_pass_claimed") is False
    assert r.get("jenkins_build_claimed_pass") is False


def test_jenkins_job_name_required(monkeypatch):
    monkeypatch.setenv("JENKINS_URL", "http://jenkins.example")
    monkeypatch.setenv("JENKINS_USER", "u")
    monkeypatch.setenv("JENKINS_API_TOKEN", "t")
    from ai_modules.enterprise.jenkins_trigger import trigger_jenkins_job

    r = trigger_jenkins_job(job_name="")
    assert r.get("error_code") == "JOB_NAME_REQUIRED"


def test_jenkins_trigger_mock_success(monkeypatch):
    monkeypatch.setenv("JENKINS_URL", "http://jenkins.example")
    monkeypatch.setenv("JENKINS_USER", "u")
    monkeypatch.setenv("JENKINS_API_TOKEN", "t")
    from ai_modules.enterprise import jenkins_trigger as jt

    def _req(method, url, **kwargs):
        if "crumbIssuer" in url:
            return 200, b'{"crumb":"c","crumbRequestField":"Jenkins-Crumb"}', {}
        return 201, b"", {"location": "http://jenkins.example/queue/item/9/"}

    monkeypatch.setattr(jt, "_request", _req)
    r = jt.trigger_jenkins_job(job_name="folder/my-job", parameters={"A": "1"})
    assert r.get("ok") is True
    assert r.get("queue_url")
    assert r.get("jenkins_build_claimed_pass") is False
    assert "/job/folder/job/my-job/buildWithParameters" in (r.get("trigger_url") or "")
