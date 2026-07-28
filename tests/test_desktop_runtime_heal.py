"""Desktop 有限运行时自愈：策略提案与失败不假绿。"""
from __future__ import annotations

import json

from ai_modules.optimize.desktop_runtime_heal import (
    desktop_runtime_heal_enabled,
    propose_healed_desktop_step,
    run_desktop_step_with_optional_heal,
)
from ai_modules.optimize.self_heal import heal_capability_matrix, summarize_heal_claim


def test_propose_broaden_exact_title_re():
    step = {
        "action": "attach_window",
        "automation_layer": "desktop",
        "desktop_spec": {"window_title_re": "(?i)^ORD-DEMO-404$"},
    }
    healed, meta = propose_healed_desktop_step(step)
    assert healed is not None
    assert "broaden_window_title_re" in meta["strategies"]
    tre = healed["desktop_spec"]["window_title_re"]
    assert tre.startswith(".*") and tre.endswith(".*")
    assert "ORD-DEMO-404" in tre


def test_propose_no_strategy_for_visual_only_click():
    healed, meta = propose_healed_desktop_step(
        {"action": "click", "desktop_spec": {"template_path": "x.png"}}
    )
    assert healed is None
    assert meta.get("reason") == "no_uia_selector"


def test_propose_uia_drop_automation_id():
    step = {
        "action": "click",
        "selector_value": json.dumps(
            {
                "element_snapshot": {
                    "selector": {
                        "anchor_props": "Button",
                        "key_candidates": [
                            {"property": "automation_id", "value": "btnSave_old", "match": "equals"},
                            {"property": "uia-name", "value": "保存", "match": "equals"},
                        ],
                        "parent_chain": [{"control_type": "Pane", "name": "Main"}],
                    }
                }
            },
            ensure_ascii=False,
        ),
    }
    healed, meta = propose_healed_desktop_step(step)
    assert healed is not None
    assert "drop_automation_id_prefer_name" in meta["strategies"]
    sel = json.loads(healed["selector_value"])["element_snapshot"]["selector"]
    props = [k["property"] for k in sel["key_candidates"]]
    assert "automation_id" not in props
    assert any(k.get("match") == "contains" for k in sel["key_candidates"])


def test_propose_uia_name_contains():
    step = {
        "action": "input",
        "selector_value": json.dumps(
            {
                "element_snapshot": {
                    "selector": {
                        "anchor_props": "Edit",
                        "key_candidates": [
                            {"property": "uia-name", "value": "订单号", "match": "equals"},
                        ],
                        "parent_chain": [],
                    }
                }
            },
            ensure_ascii=False,
        ),
    }
    healed, meta = propose_healed_desktop_step(step)
    assert healed is not None
    assert "name_match_contains" in meta["strategies"]
    sel = json.loads(healed["selector_value"])["element_snapshot"]["selector"]
    assert sel["key_candidates"][0]["match"] == "contains"


def test_propose_uia_clear_parent_chain():
    step = {
        "action": "click",
        "selector_value": json.dumps(
            {
                "element_snapshot": {
                    "selector": {
                        "anchor_props": "Button",
                        "key_candidates": [
                            {"property": "uia-name", "value": "确定", "match": "contains"},
                        ],
                        "parent_chain": [
                            {"control_type": "Window", "name": "A"},
                            {"control_type": "Pane", "name": "B"},
                        ],
                    }
                }
            },
            ensure_ascii=False,
        ),
    }
    healed, meta = propose_healed_desktop_step(step)
    assert healed is not None
    assert "clear_parent_chain" in meta["strategies"]
    sel = json.loads(healed["selector_value"])["element_snapshot"]["selector"]
    assert sel["parent_chain"] == []


def test_uia_heal_retry_still_no_fake_green(monkeypatch):
    monkeypatch.setenv("DESKTOP_RUNTIME_HEAL", "1")
    calls = {"n": 0}

    def _exec(step):
        calls["n"] += 1
        return {"status": "failed", "error": "not found", "verified": False}

    step = {
        "action": "click",
        "selector_value": json.dumps(
            {
                "element_snapshot": {
                    "selector": {
                        "anchor_props": "Button",
                        "key_candidates": [
                            {"property": "automation_id", "value": "x", "match": "equals"},
                            {"property": "uia-name", "value": "OK", "match": "equals"},
                        ],
                        "parent_chain": [],
                    }
                }
            },
            ensure_ascii=False,
        ),
    }
    result, meta = run_desktop_step_with_optional_heal(step, execute_fn=_exec)
    assert calls["n"] == 2
    assert meta["heal_attempted"] is True
    assert meta["heal_succeeded"] is False
    assert result["status"] == "failed"


def test_heal_disabled_no_retry(monkeypatch):
    monkeypatch.setenv("DESKTOP_RUNTIME_HEAL", "0")
    assert desktop_runtime_heal_enabled() is False
    calls = {"n": 0}

    def _exec(step):
        calls["n"] += 1
        return {"status": "failed", "error": "no window"}

    step = {
        "action": "attach_window",
        "desktop_spec": {"window_title_re": "^ExactTitle$"},
    }
    result, meta = run_desktop_step_with_optional_heal(step, execute_fn=_exec)
    assert calls["n"] == 1
    assert meta["heal_attempted"] is False
    assert result["status"] == "failed"
    assert result.get("desktop_heal", {}).get("reason") == "disabled"


def test_heal_retry_success_not_fake_on_warning(monkeypatch):
    monkeypatch.setenv("DESKTOP_RUNTIME_HEAL", "1")
    calls = {"n": 0}

    def _exec(step):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "failed", "error": "not found"}
        # 第二次若仍 warning，不得当绿
        return {"status": "warning", "warning": "soft"}

    step = {
        "action": "attach_window",
        "desktop_spec": {"window_title_re": "^Title$"},
    }
    result, meta = run_desktop_step_with_optional_heal(step, execute_fn=_exec)
    assert calls["n"] == 2
    assert meta["heal_attempted"] is True
    assert meta["heal_succeeded"] is False
    assert result["status"] == "warning"


def test_heal_retry_true_success(monkeypatch):
    monkeypatch.setenv("DESKTOP_RUNTIME_HEAL", "1")
    calls = {"n": 0}

    def _exec(step):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "failed", "error": "not found"}
        return {"status": "success", "extracted_text": "Title"}

    step = {
        "action": "attach_window",
        "desktop_spec": {"window_title_re": "^Title$"},
    }
    result, meta = run_desktop_step_with_optional_heal(step, execute_fn=_exec)
    assert meta["heal_succeeded"] is True
    assert result["status"] == "success"
    assert result["desktop_heal"]["heal_succeeded"] is True


def test_capability_matrix_y5_closed():
    m = heal_capability_matrix()
    assert m["y5"]["closed"] is True
    assert m["layers"]["desktop"]["runtime_heal"] == "partial"
    assert m["marketing_claim_allowed"] is False


def test_capability_matrix_desktop_partial():
    m = heal_capability_matrix()
    assert m["layers"]["desktop"]["runtime_heal"] == "partial"
    c = summarize_heal_claim(layer="desktop")
    assert c["allowed"] is False
    assert c["reason"] == "DESKTOP_HEAL_PARTIAL_ONLY"
