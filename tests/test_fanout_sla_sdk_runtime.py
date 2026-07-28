# -*- coding: utf-8 -*-
"""fan-out / SLA 证据 / SDK runtime 诚实性。"""

from __future__ import annotations


def test_fanout_no_nodes(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.farm_batch import run_probe_fanout

    r = run_probe_fanout()
    assert r.get("ok") is False
    assert r.get("error_code") == "NO_NODES"
    assert r.get("parallel_suite_pass_claimed") is False
    assert r.get("case_pass_claimed") is False


def test_fanout_two_nodes_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.execution_farm import register_node
    from ai_modules.enterprise.farm_batch import run_probe_fanout
    from ai_modules.enterprise.sla_evidence import summarize_sla_evidence

    register_node(name="a", base_url="http://127.0.0.1:9")
    register_node(name="b", base_url="http://127.0.0.1:10")
    r = run_probe_fanout(auto_run=True)
    assert r.get("ok") is True  # 批次编排完成
    assert r.get("node_count") == 2
    assert r.get("all_nodes_reachable") is False
    assert r.get("parallel_suite_pass_claimed") is False
    assert r.get("failed_count") >= 1
    summary = summarize_sla_evidence()
    assert summary.get("sla_claim") is False
    assert summary.get("sample_count") >= 1


def test_sla_evidence_never_claims(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.sla_evidence import record_metric, summarize_sla_evidence

    record_metric(kind="t", ok=True, latency_ms=12.5)
    record_metric(kind="t", ok=False, latency_ms=40)
    s = summarize_sla_evidence()
    assert s["sla_claim"] is False
    assert s["sample_count"] == 2
    assert s["latency_ms_p50"] is not None
    assert "合同" in (s.get("disclaimer") or "") or "SLA" in (s.get("disclaimer") or "")


def test_sdk_runtime_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.agent_teams.sdk_runtime import try_official_sdk_runtime
    from ai_modules.agent_teams.test_run_state import TestRunState

    st = TestRunState.create(goal="sdk-rt")
    st.emit(agent="Planner", kind="note", message="x")
    r = try_official_sdk_runtime(st, fallback_export_dir=tmp_path / "fb")
    assert r.get("ok") is False
    assert r.get("error_code") == "SDK_NOT_INSTALLED"
    assert r.get("multi_agent_sdk_runtime_claimed") is False
    assert r.get("case_pass_claimed") is False
    assert r.get("used_local_bridge") is True
    assert (tmp_path / "fb" / "sdk_events.json").is_file()
