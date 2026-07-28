# -*- coding: utf-8 -*-
"""农场 Gateway 回退 + MCP live 探活诚实性。"""

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


def test_farm_gateway_opt_in_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DESKTOP_AGENT_GATEWAY_URL", raising=False)
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_SECRET", "s3cret")
    monkeypatch.setenv("DESKTOP_FARM_GATEWAY", "0")
    from ai_modules.enterprise.execution_farm import register_node
    from ai_modules.enterprise.gateway_resolve import resolve_desktop_gateway
    from desktop_agent_client import desktop_agent_config, desktop_agent_enabled

    node = register_node(name="gw", base_url="http://10.1.2.3:8766")
    _mark_online(tmp_path, node["node_id"])

    r0 = resolve_desktop_gateway()
    assert r0.get("source") == "none"
    assert r0.get("base_url") == ""
    assert desktop_agent_enabled() is False

    monkeypatch.setenv("DESKTOP_FARM_GATEWAY", "1")
    r1 = resolve_desktop_gateway()
    assert r1.get("source") == "farm"
    assert r1.get("base_url") == "http://10.1.2.3:8766"
    assert r1.get("farm_used") is True
    base, secret = desktop_agent_config()
    assert base == "http://10.1.2.3:8766"
    assert secret == "s3cret"
    assert desktop_agent_enabled() is True


def test_env_url_wins_over_farm(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_URL", "http://env-host:8766")
    monkeypatch.setenv("DESKTOP_FARM_GATEWAY", "1")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_SECRET", "x")
    from ai_modules.enterprise.execution_farm import register_node
    from ai_modules.enterprise.gateway_resolve import resolve_desktop_gateway

    node = register_node(name="gw2", base_url="http://farm-host:8766")
    _mark_online(tmp_path, node["node_id"])
    r = resolve_desktop_gateway()
    assert r.get("source") == "env"
    assert r.get("base_url") == "http://env-host:8766"
    assert r.get("farm_used") is False


def test_mcp_live_missing_url_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DESKTOP_AGENT_GATEWAY_URL", raising=False)
    monkeypatch.setenv("DESKTOP_FARM_GATEWAY", "0")
    from testory_mcp.gateway_live import mcp_live_demo, probe_gateway_health

    health = probe_gateway_health()
    assert health.get("ok") is False
    assert health.get("error_code") == "GATEWAY_URL_MISSING"
    demo = mcp_live_demo(try_step=True)
    assert demo.get("live_tool_success") is False
    assert demo["honesty"]["health_ok_means_case_pass"] is False
