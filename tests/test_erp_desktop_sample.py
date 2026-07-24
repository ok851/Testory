# -*- coding: utf-8 -*-
"""ERP 桌面样例计划与变量种子。"""

from __future__ import annotations

from unittest.mock import patch

from ai_modules.execute.erp_desktop_sample import build_erp_desktop_sample_plan
from ai_modules.execute.orchestrator import execute_cross_end_plan
from ai_modules.plan.context_bus import CrossEndContext
from ai_modules.plan.cross_end_assertion import run_cross_end_assertions


def test_erp_plan_structure():
    plan = build_erp_desktop_sample_plan(order_id="ORD-X-1", project_id=3)
    assert plan["project_id"] == 3
    assert plan["variables"]["api_order_id"] == "ORD-X-1"
    assert plan["meta"]["template"] == "erp_desktop_sample"
    assert plan["stages"][0]["layer"] == "desktop"
    assert any(s.get("store_as") == "erp_order_id" for s in plan["stages"][0]["steps"])
    assert plan["assertions"]
    assert "args" in (plan["stages"][0]["steps"][0].get("desktop_spec") or {})


def test_erp_alias_plan_missing_is_honest(monkeypatch):
    monkeypatch.delenv("DESKTOP_APP_ALIASES", raising=False)
    plan = build_erp_desktop_sample_plan(
        order_id="ORD-A",
        launch_mode="alias",
        alias="erp",
    )
    assert plan["meta"]["launch_mode"] == "alias"
    assert plan["meta"].get("alias_error")
    assert "DESKTOP_ALIAS_MISSING" in plan["meta"]["alias_error"]
    assert plan["stages"][0]["steps"][0]["input_value"] == "@erp"


def test_erp_alias_plan_resolves_object(monkeypatch):
    import json
    import sys

    monkeypatch.setenv(
        "DESKTOP_APP_ALIASES",
        json.dumps(
            {
                "erp": {
                    "path": sys.executable,
                    "args": ["fake.py", "--order-id", "{order_id}"],
                    "window_title_re": "(?i)^{order_id}$",
                }
            }
        ),
    )
    plan = build_erp_desktop_sample_plan(
        order_id="ORD-ALIAS-1",
        launch_mode="alias",
        alias="erp",
    )
    assert not plan["meta"].get("alias_error")
    launch = plan["stages"][0]["steps"][0]
    assert launch["input_value"] == "@erp"
    assert launch["desktop_spec"]["path"] == sys.executable
    assert launch["desktop_spec"]["args"][-1] == "ORD-ALIAS-1"
    assert "ORD-ALIAS-1" in plan["meta"]["window_title_re"]


def test_plan_variables_seeded_into_context_for_assertions():
    ctx = CrossEndContext(plan_id="e1", scenario="erp")
    # 模拟编排种子
    for k, v in {"api_order_id": "ORD-A", "erp_order_id": "ORD-A"}.items():
        ctx.set_variable(k, v)
    passed, failed, details = run_cross_end_assertions(
        ctx,
        [
            {
                "field": "order_id",
                "api": "api_order_id",
                "desktop": "erp_order_id",
                "type": "string",
            }
        ],
    )
    assert failed == 0
    assert passed == 1

    ctx2 = CrossEndContext(plan_id="e2", scenario="erp")
    ctx2.set_variable("api_order_id", "ORD-A")
    ctx2.set_variable("erp_order_id", "ORD-B")
    passed2, failed2, _ = run_cross_end_assertions(
        ctx2,
        [{"field": "order_id", "api": "api_order_id", "desktop": "erp_order_id"}],
    )
    assert failed2 == 1
    assert passed2 == 0


def test_execute_seeds_plan_variables(monkeypatch):
    """编排启动时写入 plan.variables，断言可引用。"""
    calls = {"desktop": 0}

    def _ui(stage, context, plan=None):
        calls["desktop"] += 1
        # 桌面阶段假装抽到与种子一致的订单号
        oid = context.get_variable("api_order_id")
        context.set_variable("erp_order_id", oid)
        return (
            {
                "stage_id": stage.get("id"),
                "ok_assert": True,
                "layer": "desktop",
                "extracted": {"erp_order_id": oid},
                "steps_executed": 1,
            },
            {"erp_order_id": oid},
        )

    plan = {
        "plan_id": "seed-erp",
        "scenario": "t",
        "variables": {"api_order_id": "ORD-SEED-1"},
        "stages": [{"id": "d1", "layer": "desktop", "steps": [{"action": "wait", "input_value": "0.1"}]}],
        "assertions": [
            {"field": "order_id", "api": "api_order_id", "desktop": "erp_order_id"}
        ],
    }
    with patch("ai_modules.execute.orchestrator._execute_ui_stage", _ui):
        with patch("ai_modules.execute.orchestrator.check_desktop_preflight", create=True):
            out = execute_cross_end_plan(plan, acquire_lock=False, record_history=False)
    assert calls["desktop"] == 1
    assert out.get("success") is True
    assert out.get("assertion_failed", 0) == 0
