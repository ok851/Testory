# -*- coding: utf-8 -*-
"""Y6: 跨端断言解析 UI/上下文源，失败挡总成功。"""

from unittest.mock import MagicMock, patch

from ai_modules.execute.orchestrator import execute_cross_end_plan
from ai_modules.plan.context_bus import CrossEndContext
from ai_modules.plan.cross_end_assertion import (
    assert_cross_end_consistency,
    resolve_assertion_sources,
    run_cross_end_assertions,
)


def test_resolve_context_vars():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ctx.set_variable("api_balance", "100.00")
    ctx.set_variable("stage-2.balance", "100.00")
    resolved, errs = resolve_assertion_sources(
        ctx,
        {
            "api": "api_balance",
            "web": "{{stage-2.balance}}",
        },
    )
    assert errs == []
    assert resolved["api"] == "100.00"
    assert resolved["web"] == "100.00"


def test_resolve_missing_var_fails():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    resolved, errs = resolve_assertion_sources(ctx, {"api": "missing_x", "web": "also_missing"})
    assert errs
    assert resolved == {}


def test_resolve_ui_selector():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    page = MagicMock()
    loc = MagicMock()
    loc.inner_text.return_value = "Alice"
    page.locator.return_value = loc
    with patch("modules.web.browser_manager.get_page", return_value=page):
        resolved, errs = resolve_assertion_sources(
            ctx,
            {
                "api": {"var": "name", "literal": "unused"},  # will use var path - need set
                "web": {"selector": "h1.user"},
            },
        )
    # api missing var
    assert any("name" in e or "变量" in e for e in errs)

    ctx.set_variable("name", "Alice")
    with patch("modules.web.browser_manager.get_page", return_value=page):
        resolved, errs = resolve_assertion_sources(
            ctx,
            {
                "api": "name",
                "web": {"selector": "#user-name", "source": "text"},
            },
        )
    assert errs == []
    assert resolved["api"] == "Alice"
    assert resolved["web"] == "Alice"


def test_ui_source_no_page_fails():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ctx.set_variable("api", "x")
    with patch("modules.web.browser_manager.get_page", return_value=None):
        _, errs = resolve_assertion_sources(
            ctx, {"api": "api", "web": {"selector": "h1"}}
        )
    assert any("浏览器" in e or "get_page" in e for e in errs)


def test_single_source_no_expected_fails():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ok, detail = assert_cross_end_consistency(
        ctx, "bal", {"api": 10.0}, declared_source_count=1
    )
    assert ok is False
    assert "不完整" in detail


def test_two_sources_one_missing_fails_not_skip():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ok, detail = assert_cross_end_consistency(
        ctx,
        "bal",
        {"api": 10.0},
        declared_source_count=2,
    )
    assert ok is False
    assert "不得跳过" in detail or "仅" in detail


def test_numeric_consistency_pass_and_fail():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ok, _ = assert_cross_end_consistency(
        ctx, "bal", {"api": 10.0, "web": 10.005}, tolerance=0.01, declared_source_count=2
    )
    assert ok is True
    ok2, _ = assert_cross_end_consistency(
        ctx, "bal", {"api": 10.0, "web": 11.0}, tolerance=0.01, declared_source_count=2
    )
    assert ok2 is False


def test_expected_compare():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ok, _ = assert_cross_end_consistency(
        ctx, "user", {"web": "Bob"}, expected="Bob", declared_source_count=1
    )
    assert ok is True
    ok2, _ = assert_cross_end_consistency(
        ctx, "user", {"web": "Bob"}, expected="Alice", declared_source_count=1
    )
    assert ok2 is False


def test_run_assertions_blocks_and_details():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ctx.set_variable("a", "1")
    ctx.set_variable("b", "2")
    passed, failed, details = run_cross_end_assertions(
        ctx,
        [{"field": "x", "sources": {"api": "a", "web": "b"}}],
    )
    assert passed == 0 and failed == 1
    assert details[0]["ok"] is False
    assert ctx.evaluate_pass() is False  # no stages → False anyway
    assert ctx.fail_count >= 1


def test_orchestrator_assertions_from_plan_block_success():
    plan = {
        "plan_id": "y6",
        "stages": [
            {
                "id": "stage-1",
                "layer": "api",
                "sync_point": "done",
                "request": {"method": "GET", "url": "http://example.test/ok"},
            }
        ],
        "assertions": [
            {
                "field": "balance",
                "sources": {"api": "bal_api", "web": "bal_web"},
            }
        ],
    }

    def _exec(stage, context):
        context.set_variable("bal_api", "100")
        context.set_variable("bal_web", "200")
        return {"ok_assert": True, "status_code": 200}, {"bal_api": "100"}

    with patch("ai_modules.execute.orchestrator._execute_api_stage", side_effect=_exec):
        out = execute_cross_end_plan(plan, acquire_lock=False)
    assert out.get("success") is False
    assert out.get("error_code") == "CROSS_END_ASSERT_FAILED"
    assert out.get("assertion_failed") == 1
    assert out.get("user_hint")


def test_orchestrator_assertions_pass_when_consistent():
    plan = {
        "plan_id": "y6ok",
        "stages": [
            {
                "id": "stage-1",
                "layer": "api",
                "sync_point": "done",
                "request": {"method": "GET", "url": "http://example.test/ok"},
            }
        ],
        "cross_end_assertions": [
            {
                "field": "balance",
                "left": "bal_api",
                "right": "bal_web",
                "tolerance": 0.01,
            }
        ],
    }

    def _exec(stage, context):
        context.set_variable("bal_api", 100.0)
        context.set_variable("bal_web", 100.005)
        return {"ok_assert": True, "status_code": 200}, {}

    with patch("ai_modules.execute.orchestrator._execute_api_stage", side_effect=_exec):
        out = execute_cross_end_plan(plan, acquire_lock=False)
    assert out.get("success") is True
    assert out.get("assertion_passed") == 1
    assert out.get("assertion_failed") == 0


def test_left_right_and_api_web_shorthand():
    ctx = CrossEndContext(plan_id="p", scenario="s")
    ctx.set_variable("x", "same")
    ctx.set_variable("y", "same")
    p, f, d = run_cross_end_assertions(
        ctx, [{"field": "n", "api": "x", "web": "y"}]
    )
    assert p == 1 and f == 0
