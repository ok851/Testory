# -*- coding: utf-8 -*-
"""remote 农场调度门禁：未就绪不得进 Desktop 阶段。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from ai_modules.execute.farm_dispatch_gate import check_farm_dispatch_gate
from ai_modules.execute.orchestrator import _execute_ui_stage
from ai_modules.plan.context_bus import CrossEndContext


def _mark_online(tmp_path: Path, node_id: str) -> None:
    store = tmp_path / "execution_farm" / "nodes.json"
    data = json.loads(store.read_text(encoding="utf-8"))
    for n in data.get("nodes") or []:
        if n.get("node_id") == node_id:
            n["last_ok"] = True
            n["last_probe"] = "2099-01-01T00:00:00Z"
    store.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_gate_off_skips(monkeypatch, tmp_path):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DESKTOP_FARM_DISPATCH_GATE", "0")
    g = check_farm_dispatch_gate()
    assert g["ok"] is True
    assert g.get("skipped") is True


def test_auto_no_nodes_requires_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DESKTOP_FARM_DISPATCH_GATE", "auto")
    monkeypatch.delenv("DESKTOP_AGENT_GATEWAY_URL", raising=False)
    monkeypatch.delenv("DESKTOP_AGENT_GATEWAY_SECRET", raising=False)
    monkeypatch.setenv("DESKTOP_FARM_GATEWAY", "0")
    monkeypatch.setenv("DESKTOP_EXECUTION_MODE", "remote")
    g = check_farm_dispatch_gate()
    assert g["ok"] is False
    assert g["error_code"] == "FARM_DISPATCH_NOT_READY"


def test_auto_no_nodes_with_gateway_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DESKTOP_FARM_DISPATCH_GATE", "auto")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_URL", "http://10.0.0.1:8766")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_SECRET", "sec")
    monkeypatch.setenv("DESKTOP_EXECUTION_MODE", "remote")
    g = check_farm_dispatch_gate()
    assert g["ok"] is True
    assert "gateway" in (g.get("detail") or "")


def test_auto_with_nodes_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DESKTOP_FARM_DISPATCH_GATE", "auto")
    monkeypatch.setenv("DESKTOP_EXECUTION_MODE", "inprocess")  # not remote → dispatch_ready false
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_SECRET", "sec")
    from ai_modules.enterprise.execution_farm import register_node

    register_node(name="n", base_url="http://127.0.0.1:8766")
    g = check_farm_dispatch_gate()
    assert g["ok"] is False
    assert g["error_code"] == "FARM_DISPATCH_NOT_READY"
    assert g.get("failed_checks")


def test_force_ready_path(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DESKTOP_FARM_DISPATCH_GATE", "1")
    monkeypatch.setenv("DESKTOP_EXECUTION_MODE", "remote")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_URL", "http://10.0.0.2:8766")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_SECRET", "sec")
    from ai_modules.enterprise.execution_farm import register_node

    node = register_node(name="ready", base_url="http://10.0.0.2:8766")
    _mark_online(tmp_path, node["node_id"])
    g = check_farm_dispatch_gate()
    assert g["ok"] is True


def test_preflight_remote_farm_gate_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DESKTOP_PREFLIGHT", raising=False)
    monkeypatch.setenv("DESKTOP_EXECUTION_MODE", "remote")
    monkeypatch.setenv("DESKTOP_FARM_DISPATCH_GATE", "force")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_URL", "http://127.0.0.1:8766")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_SECRET", "sec")

    from ai_modules.execute.desktop_preflight import check_desktop_preflight

    with patch("modules.desktop.desktop_env_config.desktop_execution_mode", return_value="remote"):
        with patch("modules.desktop.desktop_agent_client.desktop_agent_enabled", return_value=True):
            with patch(
                "modules.desktop.desktop_agent_client.desktop_agent_json",
                return_value=({"ok": True}, None),
            ):
                pre = check_desktop_preflight()
    assert pre["ok"] is False
    assert pre["error_code"] == "FARM_DISPATCH_NOT_READY"
    assert pre.get("farm_dispatch", {}).get("ok") is False


def test_orchestrator_propagates_farm_gate():
    def _bad(**_k):
        return {
            "ok": False,
            "mode": "remote",
            "detail": "farm_gate",
            "error_code": "FARM_DISPATCH_NOT_READY",
            "error": "not ready",
            "farm_dispatch": {"ok": False, "detail": "dispatch_not_ready"},
        }

    stage = {
        "id": "d1",
        "layer": "desktop",
        "steps": [{"action": "launch_app", "input_value": "notepad.exe"}],
    }
    with patch(
        "ai_modules.execute.desktop_preflight.check_desktop_preflight",
        _bad,
    ):
        result, _ = _execute_ui_stage(stage, CrossEndContext(plan_id="fg", scenario="t"))
    assert result["ok_assert"] is False
    assert result["error_code"] == "FARM_DISPATCH_NOT_READY"
    assert result["desktop_preflight"]["farm_dispatch"]["ok"] is False
