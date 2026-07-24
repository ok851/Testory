"""Z3：Mock(simulate) 与真机跨端结果同一 Schema。"""
from __future__ import annotations

from ai_modules.execute.result_schema import (
    CROSS_END_SCHEMA,
    STAGE_SCHEMA,
    mock_and_live_share_schema,
    normalize_cross_end_result,
    normalize_stage_result,
    validate_cross_end_result,
    validate_stage_result,
)


def test_normalize_stage_dual_writes_error_message():
    sr = normalize_stage_result(
        {
            "stage_id": "s1",
            "ok_assert": False,
            "error": "boom",
            "error_code": "X",
            "screenshot_path": "a.png",
        }
    )
    assert sr["schema"] == STAGE_SCHEMA
    assert sr["error_message"] == "boom"
    assert sr["extracted"] == {}
    assert sr["warnings"] == []
    assert any(e.get("path") == "a.png" for e in sr["evidence"])
    ok, errs = validate_stage_result(sr)
    assert ok, errs


def test_normalize_defaults_ok_assert_false():
    sr = normalize_stage_result({"stage_id": "x"})
    assert sr["ok_assert"] is False
    ok, _ = validate_stage_result(sr)
    assert ok


def test_mock_simulate_and_live_like_share_schema():
    # 精简版 simulate（旧字段仅 error，无 evidence）
    mock = {
        "success": True,
        "gate_passed": True,
        "stage_results": [
            {
                "stage_id": "api",
                "layer": "api",
                "ok_assert": True,
                "extracted": {"order_id": "ORD-1"},
            },
            {
                "stage_id": "web",
                "layer": "web",
                "ok_assert": False,
                "error": "assert fail",
                "error_code": "ASSERT_TEXT_MISMATCH",
                "screenshot_path": "shot.png",
            },
        ],
        "variables": {"order_id": "ORD-1"},
        "simulate": True,
    }
    # 真机风格（已有 warnings，缺 error_message）
    live = {
        "success": False,
        "gate_passed": False,
        "stage_results": [
            {
                "stage_id": "api",
                "layer": "api",
                "ok_assert": True,
                "extracted": {"order_id": "ORD-1"},
                "warnings": [],
                "evidence": [],
            },
            {
                "stage_id": "web",
                "layer": "web",
                "ok_assert": False,
                "error": "assert fail",
                "error_code": "ASSERT_TEXT_MISMATCH",
                "extracted": {},
                "warnings": ["soft"],
                "evidence": [{"type": "log", "text": "x"}],
            },
        ],
        "variables": {"order_id": "ORD-1"},
        "error": "跨端断言失败",
        "error_code": "CROSS_END_ASSERT_FAILED",
    }
    ok, errs = mock_and_live_share_schema(mock, live)
    assert ok, errs
    a = normalize_cross_end_result(mock)
    b = normalize_cross_end_result(live)
    assert a["schema"] == b["schema"] == CROSS_END_SCHEMA
    vok, verr = validate_cross_end_result(a)
    assert vok, verr


def test_demo_simulate_output_validates(monkeypatch, tmp_path):
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = root / "demos" / "goai-agentteams" / "run_demo.py"
    spec = importlib.util.spec_from_file_location("z3_demo", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader
    spec.loader.exec_module(mod)

    fn = mod.simulate_execute("consistent")
    out = fn({"plan_id": "p1", "stages": []})
    ok, errs = validate_cross_end_result(out)
    assert ok, errs
    assert out["schema"] == CROSS_END_SCHEMA
    assert all(s.get("schema") == STAGE_SCHEMA for s in out["stage_results"])
