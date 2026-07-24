# -*- coding: utf-8 -*-
"""跨端变量抽取契约与 Web/编排落地回归。"""

from unittest.mock import MagicMock, patch

from ai_modules.execute.orchestrator import _execute_ui_stage, execute_cross_end_plan
from ai_modules.plan.context_bus import CrossEndContext
from ai_modules.plan.var_extraction import (
    apply_value_policy,
    collect_extraction_rules,
    extract_web_variables,
    merge_step_extractions,
    redact_value,
    validate_required_extractions,
)
from ai_modules.plan.api_skill_adapter import execute_api_stage


def test_collect_rules_from_vars_and_steps():
    stage = {
        "vars_to_store": {"order_id": "#oid"},
        "steps": [
            {"action": "extract_text", "selector": ".x", "store_as": "title"},
        ],
    }
    rules = collect_extraction_rules(stage)
    assert "order_id" in rules
    assert rules["order_id"]["selector"] == "#oid"
    assert "title" in rules


def test_redact_sensitive_names():
    assert "***" in redact_value("secret-token-value") or "*" in redact_value("abcdef")
    v = apply_value_policy("password", "hunter2", {"redact": True})
    assert "hunter2" not in str(v)


def test_extract_web_variables_success_and_missing():
    page = MagicMock()
    page.url = "https://ex/order/9"
    loc = MagicMock()
    loc.inner_text.return_value = "ORD-9"
    page.locator.return_value = loc

    rules = collect_extraction_rules(
        {
            "vars_to_store": {
                "order_id": {"selector": "#oid", "source": "text"},
                "page_url": {"source": "url"},
                "missing_req": {"selector": "#nope"},
            }
        }
    )
    # force missing for missing_req
    def _locator(sel):
        if sel == "#nope":
            m = MagicMock()
            m.inner_text.side_effect = Exception("timeout")
            return m
        return loc

    page.locator.side_effect = _locator
    extracted, missing = extract_web_variables(page, rules)
    assert extracted.get("order_id") == "ORD-9"
    assert extracted.get("page_url") == "https://ex/order/9"
    assert "missing_req" in missing


def test_optional_var_not_in_missing():
    rules = collect_extraction_rules(
        {"vars_to_store": {"opt": {"selector": "#x", "optional": True}}}
    )
    page = MagicMock()
    loc = MagicMock()
    loc.inner_text.side_effect = Exception("gone")
    page.locator.return_value = loc
    extracted, missing = extract_web_variables(page, rules)
    assert missing == []
    assert "opt" not in extracted


def test_merge_step_extractions():
    out = merge_step_extractions(
        [{"ok": True, "extracted_text": "A1", "store_as": "code"}],
        [{"action": "extract_text", "store_as": "code"}],
    )
    assert out["code"] == "A1"


def test_ui_stage_extracts_and_stores_in_context():
    page = MagicMock()
    page.url = "https://app/t"
    loc = MagicMock()
    loc.inner_text.return_value = "TICKET-1"
    page.locator.return_value = loc

    stage = {
        "id": "w1",
        "layer": "web",
        "steps": [{"action": "wait", "value": "0"}],
        "vars_to_store": {"ticket_id": {"selector": "#tid"}},
    }
    ctx = CrossEndContext(plan_id="p", scenario="s")
    with patch("browser_manager.get_page", return_value=page):
        with patch(
            "ai_modules.execute.web_runner.execute_single_web_step",
            return_value={"ok": True, "skipped": False, "action": "wait"},
        ):
            result, extracted = _execute_ui_stage(stage, ctx)
    assert result["ok_assert"] is True
    assert extracted.get("ticket_id") == "TICKET-1"
    ctx.record_stage_result("w1", result, extracted)
    assert ctx.get_variable("ticket_id") == "TICKET-1"
    assert ctx.get_variable("w1.ticket_id") == "TICKET-1"


def test_ui_stage_missing_required_var_fails():
    page = MagicMock()
    loc = MagicMock()
    loc.inner_text.side_effect = Exception("not found")
    page.locator.return_value = loc
    stage = {
        "id": "w2",
        "layer": "web",
        "steps": [{"action": "wait", "value": "0"}],
        "vars_to_store": {"must": "#must"},
    }
    with patch("browser_manager.get_page", return_value=page):
        with patch(
            "ai_modules.execute.web_runner.execute_single_web_step",
            return_value={"ok": True, "skipped": False, "action": "wait"},
        ):
            result, extracted = _execute_ui_stage(stage, CrossEndContext())
    assert result["ok_assert"] is False
    assert result.get("error_code") == "VAR_EXTRACT_MISSING"
    assert "must" not in extracted or extracted.get("must") is None


def test_downstream_resolves_upstream_var():
    plan = {
        "plan_id": "link-1",
        "scenario": "api then use var",
        "stages": [
            {
                "id": "a1",
                "layer": "api",
                "sync_point": "created",
                "request": {"method": "GET", "url": "https://example.invalid/x"},
                "extract": {"order_id": {"json_path": "$.id", "type": "string"}},
            },
            {
                "id": "w1",
                "layer": "web",
                "depends_on": ["created"],
                "steps": [{"action": "wait", "value": "0"}],
            },
        ],
    }

    api_result = {
        "ok_assert": True,
        "status_code": 200,
        "response_json": {"id": "OID-77"},
        "response_text": "",
        "response_headers": {},
        "error": None,
    }

    def _fake_api(stage, context):
        from ai_modules.plan.api_skill_adapter import _extract_from_response

        extracted = _extract_from_response(
            api_result["response_json"], "", {}, stage.get("extract") or {}
        )
        return dict(api_result), extracted

    def _fake_ui(stage, context, **kwargs):
        # 下游应能读到上游变量
        assert context.get_variable("order_id") == "OID-77"
        return {"ok_assert": True, "error": None, "elapsed_ms": 1, "steps_executed": 1}, {}

    with patch(
        "ai_modules.execute.orchestrator._execute_api_stage",
        side_effect=_fake_api,
    ):
        with patch(
            "ai_modules.execute.orchestrator._execute_ui_stage",
            side_effect=_fake_ui,
        ):
            out = execute_cross_end_plan(plan, acquire_lock=False)

    assert out.get("success") is True
    assert out["variables"].get("order_id") == "OID-77"


def test_api_extract_missing_fails_gate():
    with patch(
        "ai_modules.plan.api_skill_adapter.execute_api_spec_sync",
        return_value={
            "ok_assert": True,
            "status_code": 200,
            "response_json": {"other": 1},
            "response_text": "",
            "response_headers": {},
        },
    ):
        result, extracted = execute_api_stage(
            {
                "request": {"method": "GET", "url": "https://example.invalid"},
                "extract": {"order_id": {"json_path": "$.id"}},
            }
        )
    assert result.get("ok_assert") is False
    assert result.get("error_code") == "VAR_EXTRACT_MISSING" or "抽取" in (
        result.get("error") or ""
    )


def test_validate_required():
    rules = {"a": {"optional": False}, "b": {"optional": True}}
    assert validate_required_extractions(rules, {"a": "1"}) == []
    assert validate_required_extractions(rules, {}) == ["a"]
