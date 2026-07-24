"""Desktop 有限运行时自愈：策略提案与失败不假绿。"""
from __future__ import annotations

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


def test_propose_no_strategy_for_click():
    healed, meta = propose_healed_desktop_step(
        {"action": "click", "desktop_spec": {"template_path": "x.png"}}
    )
    assert healed is None
    assert meta.get("reason") == "unsupported_action"


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


def test_capability_matrix_desktop_partial():
    m = heal_capability_matrix()
    assert m["layers"]["desktop"]["runtime_heal"] == "partial"
    c = summarize_heal_claim(layer="desktop")
    assert c["allowed"] is False
    assert c["reason"] == "DESKTOP_HEAL_PARTIAL_ONLY"
