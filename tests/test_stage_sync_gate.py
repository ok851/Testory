# -*- coding: utf-8 -*-
"""Y1: vars_to_read / wait_for / data_sync 等同步门禁真实接线。"""

from unittest.mock import patch

from ai_modules.execute.orchestrator import execute_cross_end_plan
from ai_modules.plan.context_bus import CrossEndContext
from ai_modules.plan.sync_manager import (
    SyncPointManager,
    collect_stage_sync_specs,
)


def test_collect_vars_to_read_as_data_sync():
    specs = collect_stage_sync_specs({"vars_to_read": ["token", "stage-1.order_id"]})
    assert len(specs) == 1
    assert specs[0]["type"] == "data_sync"
    assert specs[0]["keys"] == ["token", "stage-1.order_id"]


def test_collect_wait_for_list_and_shorthand():
    specs = collect_stage_sync_specs({
        "wait_for": [
            {"type": "time_sync", "seconds": 0.01},
            {"type": "data_sync", "keys": ["a"]},
        ],
        "time_sync": 0.02,
    })
    types = [s["type"] for s in specs]
    assert "time_sync" in types
    assert "data_sync" in types


def test_wait_for_data_sync_polls_then_ok():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    mgr = SyncPointManager(ctx)
    ok, missing, waited = mgr.wait_for_data_sync(["token"], max_wait_s=0.05, interval_s=0.02)
    assert ok is False
    assert missing == ["token"]

    ctx.set_variable("token", "abc")
    ok2, missing2, _ = mgr.wait_for_data_sync(["token"], max_wait_s=0.05, interval_s=0.02)
    assert ok2 is True
    assert missing2 == []


def test_wait_for_data_sync_empty_keys_fails():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    mgr = SyncPointManager(ctx)
    ok, missing, _ = mgr.wait_for_data_sync([], max_wait_s=0.01)
    assert ok is False
    assert missing


def test_empty_string_var_is_missing():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ctx.set_variable("token", "  ")
    mgr = SyncPointManager(ctx)
    ok, missing, _ = mgr.wait_for_data_sync(["token"], max_wait_s=0.0)
    assert ok is False
    assert "token" in missing


def test_run_pre_stage_syncs_time_and_data():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ctx.set_variable("order_id", "O-1")
    mgr = SyncPointManager(ctx)
    gate = mgr.run_pre_stage_syncs({
        "vars_to_read": ["order_id"],
        "wait_for": {"type": "time_sync", "seconds": 0.01},
    })
    assert gate["ok"] is True
    assert len(gate["syncs"]) == 2


def test_run_pre_stage_syncs_missing_vars_fails():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    mgr = SyncPointManager(ctx)
    gate = mgr.run_pre_stage_syncs({
        "vars_to_read": ["missing_token"],
        "data_sync_timeout_s": 0.05,
    })
    assert gate["ok"] is False
    assert gate.get("error_code") == "SYNC_DATA_TIMEOUT"
    assert "missing_token" in (gate.get("error") or "")


def test_state_sync_variable_condition():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    mgr = SyncPointManager(ctx)
    gate = mgr.run_pre_stage_syncs({
        "wait_for": {
            "type": "state_sync",
            "variable": "ui_ready",
            "equals": True,
            "timeout_s": 0.05,
            "interval_s": 0.02,
        }
    })
    assert gate["ok"] is False
    assert gate.get("error_code") == "SYNC_UI_VAR_TIMEOUT"

    ctx.set_variable("ui_ready", True)
    gate2 = mgr.run_pre_stage_syncs({
        "state_sync": {"variable": "ui_ready", "equals": True, "timeout_s": 0.05}
    })
    assert gate2["ok"] is True


def test_state_sync_no_page_selector_fails_honestly():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    mgr = SyncPointManager(ctx)
    with patch("modules.web.browser_manager.get_page", return_value=None):
        detail = mgr._run_ui_state_sync(
            {"selector": "#ready", "timeout_s": 0.01},
            {"type": "state_sync", "ok": False},
        )
    assert detail["ok"] is False
    assert detail.get("error_code") == "SYNC_UI_NO_PAGE"


def test_api_state_sync_success_and_timeout():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    mgr = SyncPointManager(ctx)
    calls = {"n": 0}

    def _fake_http(spec, resolve_text=None):
        calls["n"] += 1
        status = "pending" if calls["n"] < 2 else "ready"
        return {"response_json": {"status": status}, "ok_assert": True}

    with patch("modules.integration.api_http_helper.execute_api_spec_sync", side_effect=_fake_http):
        gate = mgr.run_pre_stage_syncs({
            "wait_for": {
                "type": "api_state_sync",
                "request": {"method": "GET", "url": "http://example.test/status"},
                "json_path": "$.status",
                "equals": "ready",
                "timeout_s": 1.0,
                "interval_s": 0.05,
            }
        })
    assert gate["ok"] is True
    assert calls["n"] >= 2

    calls["n"] = 0

    def _always_pending(spec, resolve_text=None):
        return {"response_json": {"status": "pending"}, "ok_assert": True}

    with patch("modules.integration.api_http_helper.execute_api_spec_sync", side_effect=_always_pending):
        gate2 = mgr.run_pre_stage_syncs({
            "api_state_sync": {
                "request": {"method": "GET", "url": "http://example.test/status"},
                "json_path": "$.status",
                "equals": "ready",
                "timeout_s": 0.12,
                "interval_s": 0.05,
            }
        })
    assert gate2["ok"] is False
    assert gate2.get("error_code") == "SYNC_API_TIMEOUT"


def test_unknown_sync_type_fails():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    mgr = SyncPointManager(ctx)
    gate = mgr.run_pre_stage_syncs({"wait_for": {"type": "magic_sync"}})
    assert gate["ok"] is False
    assert gate.get("error_code") == "SYNC_UNKNOWN_TYPE"


def test_acquire_rejects_skipped_dependency():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ctx.record_stage_result(
        "a",
        {"ok_assert": False, "skipped_failure": True, "error": "x"},
    )
    mgr = SyncPointManager(ctx)
    mgr.set_plan_stages([
        {"id": "a", "sync_point": "created"},
        {"id": "b", "depends_on": ["created"], "sync_point": "done"},
    ])
    assert mgr.acquire("b", ["created"]) is False


def test_orchestrator_vars_to_read_blocks_success():
    plan = {
        "plan_id": "y1-missing",
        "scenario": "y1",
        "stages": [
            {
                "id": "stage-1",
                "layer": "api",
                "sync_point": "done",
                "vars_to_read": ["never_set"],
                "data_sync_timeout_s": 0.05,
                "request": {"method": "GET", "url": "http://example.test/ok"},
            }
        ],
    }
    with patch(
        "ai_modules.plan.api_skill_adapter.execute_api_stage",
        return_value=({"ok_assert": True, "status_code": 200}, {}),
    ):
        out = execute_cross_end_plan(plan, acquire_lock=False)
    assert out.get("success") is False
    assert out.get("error_code") == "SYNC_DATA_TIMEOUT" or (
        out.get("stage_results")
        and out["stage_results"][0].get("error_code") == "SYNC_DATA_TIMEOUT"
    )
    # API 不应被调用（同步门禁挡在前面）
    assert out["stage_results"][0].get("ok_assert") is False


def test_orchestrator_vars_to_read_passes_when_present():
    plan = {
        "plan_id": "y1-ok",
        "scenario": "y1",
        "stages": [
            {
                "id": "stage-1",
                "layer": "api",
                "sync_point": "prep",
                "request": {"method": "GET", "url": "http://example.test/prep"},
            },
            {
                "id": "stage-2",
                "layer": "api",
                "depends_on": ["prep"],
                "vars_to_read": ["token"],
                "sync_point": "done",
                "request": {"method": "GET", "url": "http://example.test/use"},
            },
        ],
    }

    def _exec(stage, context):
        if stage.get("id") == "stage-1":
            return {"ok_assert": True, "status_code": 200}, {"token": "T1"}
        return {"ok_assert": True, "status_code": 200}, {}

    with patch("ai_modules.execute.orchestrator._execute_api_stage", side_effect=_exec):
        out = execute_cross_end_plan(plan, acquire_lock=False)
    assert out.get("success") is True
    assert out.get("gate_passed") is True


def test_orchestrator_time_sync_runs():
    plan = {
        "plan_id": "y1-time",
        "scenario": "y1",
        "stages": [
            {
                "id": "stage-1",
                "layer": "api",
                "sync_point": "done",
                "wait_for": {"type": "time_sync", "seconds": 0.02},
                "request": {"method": "GET", "url": "http://example.test/ok"},
            }
        ],
    }
    with patch(
        "ai_modules.execute.orchestrator._execute_api_stage",
        return_value=({"ok_assert": True, "status_code": 200}, {}),
    ):
        out = execute_cross_end_plan(plan, acquire_lock=False)
    assert out.get("success") is True
