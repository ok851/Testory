# -*- coding: utf-8 -*-
"""R15 IncidentMemory / Runbook 轻量检索。"""

from __future__ import annotations


def test_seed_runbook_search_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.memory.incident_memory import search_runbooks, suggest_for_failure

    hits = search_runbooks("DESKTOP_NO_SESSION attach_window", limit=3)
    assert hits
    assert any("desktop" in (h.get("id") or "") or "桌面" in (h.get("title") or "") for h in hits)
    tips = suggest_for_failure(
        error_code="DESKTOP_SOFT_FAIL",
        error_message="窗口未找到",
        layer="desktop",
    )
    assert tips
    assert tips[0].get("score", 0) > 0


def test_record_and_search_incident(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.memory.incident_memory import record_incident, search_incidents

    rec = record_incident(
        error_code="CROSS_END_ASSERT_FAILED",
        error_message="order_id mismatch",
        layer="api",
        title="断言失败样例",
        body="核对变量透传",
        tags=["assert"],
    )
    assert rec["id"].startswith("inc-")
    hits = search_incidents("CROSS_END_ASSERT_FAILED order_id", limit=5)
    assert any(h.get("id") == rec["id"] for h in hits)


def test_remember_verifier_failure_skips_success(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.agent_teams.test_run_state import TestRunState
    from ai_modules.memory.incident_memory import remember_verifier_failure, search_incidents

    st = TestRunState.create(goal="ok")
    st.set_status("success")
    assert remember_verifier_failure(st) is None

    st2 = TestRunState.create(goal="bad")
    st2.set_status("failed")
    st2.execution = {"error": "boom", "error_code": "HITL_TIMEOUT"}
    st2.report = {"reason": "HITL 超时"}
    rec = remember_verifier_failure(st2)
    assert rec is not None
    hits = search_incidents("HITL_TIMEOUT", limit=5)
    assert any(h.get("id") == rec["id"] for h in hits)
