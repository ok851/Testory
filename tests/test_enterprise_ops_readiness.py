# -*- coding: utf-8 -*-
"""企业运营：农场调度就绪 + ops readiness（非 SLA）。"""

from __future__ import annotations

import json
from pathlib import Path


def _mark_online(tmp_path: Path, node_id: str) -> None:
    store = tmp_path / "execution_farm" / "nodes.json"
    data = json.loads(store.read_text(encoding="utf-8"))
    for n in data.get("nodes") or []:
        if n.get("node_id") == node_id:
            n["last_ok"] = True
            n["last_probe"] = "2099-01-01T00:00:00Z"
    store.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_dispatch_not_ready_without_online(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DESKTOP_EXECUTION_MODE", "inprocess")
    monkeypatch.delenv("DESKTOP_AGENT_GATEWAY_URL", raising=False)
    monkeypatch.delenv("DESKTOP_AGENT_GATEWAY_SECRET", raising=False)
    from ai_modules.enterprise.execution_farm import dispatch_hint, dispatch_readiness, register_node

    register_node(name="n1", base_url="http://127.0.0.1:18766")
    ready = dispatch_readiness()
    assert ready.get("dispatch_ready") is False
    assert "disclaimer" in ready
    hint = dispatch_hint()
    assert hint.get("ok") is False
    assert hint.get("error_code") == "NO_ONLINE_NODE"


def test_dispatch_hint_env_suggestions(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DESKTOP_EXECUTION_MODE", "remote")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_SECRET", "secret-for-test")
    from ai_modules.enterprise.execution_farm import dispatch_hint, dispatch_readiness, register_node

    node = register_node(name="desk", base_url="http://10.0.0.9:8766", capabilities=["desktop"])
    _mark_online(tmp_path, node["node_id"])
    hint = dispatch_hint(capability="desktop")
    assert hint.get("ok") is True
    assert hint["env_suggestions"]["DESKTOP_AGENT_GATEWAY_URL"] == "http://10.0.0.9:8766"
    assert "不会自动改" in (hint.get("disclaimer") or "")
    ready = dispatch_readiness()
    assert ready.get("preferred_node")
    # gateway URL may be empty but preferred exists → gateway_url check ok
    assert any(c["id"] == "online_node" and c["ok"] for c in ready["checks"])


def test_ops_readiness_never_claims_sla(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.readiness import enterprise_ops_readiness

    payload = enterprise_ops_readiness()
    assert payload.get("ok") is True
    assert payload.get("sla_claim") is False
    assert "合同" in (payload.get("disclaimer") or "") or "SLA" in (payload.get("disclaimer") or "")
    assert payload.get("checks")


def test_sdk_bridge_adapt_events():
    from ai_modules.agent_teams.sdk_bridge import adapt_local_run_to_sdk_events
    from ai_modules.agent_teams.test_run_state import TestRunState

    st = TestRunState.create(goal="sdk")
    st.emit(agent="Planner", kind="plan", message="planned")
    st.emit(agent="Verifier", kind="verify", message="done")
    out = adapt_local_run_to_sdk_events(st)
    assert out.get("control_plane") == "local"
    roles = {e.get("sdk_role") for e in out.get("events") or []}
    assert "planner" in roles
    assert "verifier" in roles
