# -*- coding: utf-8 -*-
"""Phase A 加深：≥5 角色 + SDK bridge；客户 ERP 别名持久化。"""

from __future__ import annotations

from pathlib import Path

from ai_modules.agent_teams import load_team_spec, run_cross_end_qa_team
from ai_modules.agent_teams.sdk_bridge import (
    LOCAL_ROLES_MIN,
    adapt_local_run_to_sdk_events,
    assert_five_roles_in_spec,
)
from ai_modules.agent_teams.team_runner import run_with_injected_execute
from modules.desktop.desktop_env_config import (
    load_app_alias_specs,
    probe_app_alias,
    save_user_alias,
)


def test_spec_has_five_roles():
    spec = load_team_spec()
    missing = assert_five_roles_in_spec(spec)
    assert missing == [], missing
    ids = [r.get("id") for r in spec.get("roles") or []]
    for r in LOCAL_ROLES_MIN:
        assert r in ids


def test_five_agents_emit_on_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))

    def fake_execute(plan, **kwargs):
        return {
            "success": True,
            "gate_passed": True,
            "stage_results": [{"stage_id": "s1", "ok_assert": True}],
            "assertion_passed": 0,
            "assertion_failed": 0,
        }

    plan = {
        "plan_id": "p5",
        "stages": [
            {
                "id": "s1",
                "layer": "api",
                "request": {"method": "GET", "url": "https://example.com"},
            },
            {
                "id": "d1",
                "layer": "desktop",
                "steps": [{"action": "attach_window", "desktop_spec": {"window_title_re": ".*"}}],
            },
        ],
    }
    state = run_with_injected_execute(
        fake_execute,
        plan=plan,
        persist=False,
        record_history=False,
        allow_replan=False,
    )
    seen = state.agent_kinds_seen()
    for role in LOCAL_ROLES_MIN:
        assert role in seen, f"missing {role} in {seen}"
    assert state.status == "success"
    mapped = adapt_local_run_to_sdk_events(state)
    assert mapped["sdk_available"] is False or isinstance(mapped["sdk_available"], bool)
    assert len(mapped["events"]) >= 5


def test_user_alias_persist_and_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DESKTOP_APP_ALIASES", raising=False)
    # 用本文件当「客户 exe」探测存在性
    fake_exe = Path(__file__).resolve()
    entry = save_user_alias(
        "erp",
        path=str(fake_exe),
        args=["/order", "{order_id}"],
        window_title_re=".*{order_id}.*",
    )
    assert entry["alias"] == "erp"
    specs = load_app_alias_specs()
    assert "erp" in specs
    assert specs["erp"]["path"] == str(fake_exe)
    probe = probe_app_alias("erp", order_id="ORD-1")
    assert probe["ok"] is True
    assert probe["path_exists"] is True
    assert "ORD-1" in (probe.get("window_title_re") or "") or "{order_id}" not in (
        probe.get("window_title_re") or ""
    )


def test_probe_missing_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DESKTOP_APP_ALIASES", raising=False)
    probe = probe_app_alias("erp_not_configured_xyz")
    assert probe["ok"] is False
    assert probe["error_code"] == "DESKTOP_ALIAS_MISSING"
