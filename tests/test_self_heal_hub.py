"""Self-heal Hub 能力矩阵与 Desktop 诚实扫描（Y5）。"""
from __future__ import annotations

from ai_modules.optimize.self_heal import (
    analyze_steps_for_self_heal,
    heal_capability_matrix,
    summarize_heal_claim,
)


def test_capability_matrix_desktop_no_runtime_heal():
    m = heal_capability_matrix()
    assert m["marketing_claim_allowed"] is False
    assert m["layers"]["desktop"]["runtime_heal"] == "partial"
    assert m["layers"]["desktop"]["static_scan"] is True
    assert m["layers"]["web"]["runtime_heal"] is True


def test_summarize_desktop_claim_forbidden():
    c = summarize_heal_claim(layer="desktop")
    assert c["allowed"] is False
    assert c["reason"] == "DESKTOP_HEAL_PARTIAL_ONLY"


def test_analyze_desktop_missing_launch_path():
    out = analyze_steps_for_self_heal(
        [
            {
                "action": "launch_app",
                "automation_layer": "desktop",
                "input_value": "",
                "desktop_spec": {},
            }
        ]
    )
    assert out["desktop_runtime_heal"] == "partial"
    assert out["marketing_claim_allowed"] is False
    assert out["desktop_steps"] == 1
    assert any("launch_app" in i for i in out["issues"])
    assert any("Desktop" in s for s in out["suggestions"])


def test_analyze_desktop_ok_with_alias_no_false_heal_claim():
    out = analyze_steps_for_self_heal(
        [
            {
                "action": "launch_app",
                "automation_layer": "desktop",
                "input_value": "@erp",
                "desktop_spec": {"alias": "erp"},
            },
            {
                "action": "attach_window",
                "automation_layer": "desktop",
                "desktop_spec": {"window_title_re": ".*ERP.*"},
            },
        ]
    )
    assert out["healthy"] is True
    assert out["desktop_runtime_heal"] == "partial"
    assert any("Desktop" in s for s in out["suggestions"])
