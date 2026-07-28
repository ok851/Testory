# -*- coding: utf-8 -*-
"""Phase B：Skill 沉淀 / 执行农场 / 配置注册中心。"""

from __future__ import annotations

from pathlib import Path


def test_promote_requires_success(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.skills.promote_from_run import promote_plan_to_skill_draft

    plan = {"scenario": "demo", "stages": [{"id": "s1", "layer": "api", "steps": [{"action": "http_get"}]}]}
    path, meta = promote_plan_to_skill_draft(plan, success=False, force=False)
    assert path is None
    assert meta.get("error_code") == "PROMOTE_REQUIRES_SUCCESS"

    path2, meta2 = promote_plan_to_skill_draft(plan, success=True)
    assert meta2.get("ok") is True
    assert path2 is not None
    assert Path(path2).is_file()


def test_promote_agent_run_failed_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.agent_teams.test_run_state import TestRunState
    from ai_modules.skills.promote_from_run import promote_agent_run

    st = TestRunState.create(goal="fail-flow")
    st.plan = {
        "scenario": "fail-flow",
        "stages": [{"id": "a", "layer": "web", "steps": [{"action": "navigate"}]}],
    }
    st.set_status("failed")
    _path, meta = promote_agent_run(st)
    assert meta.get("ok") is False
    assert meta.get("error_code") == "PROMOTE_REQUIRES_SUCCESS"


def test_execution_farm_register_and_probe_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.execution_farm import farm_summary, probe_node, register_node

    node = register_node(name="local", base_url="http://127.0.0.1:9")
    assert node.get("node_id")
    summary = farm_summary()
    assert summary["node_count"] == 1
    assert "disclaimer" in summary
    miss = probe_node("no-such-node")
    assert miss.get("error_code") == "NODE_NOT_FOUND"
    unreachable = probe_node(node["node_id"], timeout_s=0.5)
    assert unreachable.get("ok") is False
    assert unreachable.get("error_code") == "NODE_UNREACHABLE"
    assert "并行" in (unreachable.get("disclaimer") or "") or "可达" in (unreachable.get("disclaimer") or "")


def test_config_registry_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.config_registry import get_spec, registry_info, seed_from_builtin_team_spec

    path = seed_from_builtin_team_spec()
    assert path.is_file()
    info = registry_info()
    assert info.get("specs")
    assert "Nacos" in (info.get("nacos_note") or "")
    sid = info["specs"][0]
    spec = get_spec(sid)
    assert isinstance(spec, dict)
    assert spec.get("agents") or spec.get("roles") or spec.get("team_id")
