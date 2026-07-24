# -*- coding: utf-8 -*-
"""R08/R09/R10：门禁 Demo（HITL + Desktop 闸门 + RiskGuard）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_hitl import reset_hitl_state_for_tests
from ai_modules.security.risk_guard import reset_risk_guard_for_tests

_ROOT = Path(__file__).resolve().parents[1]
_DEMO = _ROOT / "demos" / "goai-agentteams"


@pytest.fixture(autouse=True)
def _clean():
    reset_hitl_state_for_tests()
    reset_risk_guard_for_tests()
    yield
    reset_hitl_state_for_tests()
    reset_risk_guard_for_tests()


@pytest.mark.parametrize(
    "variant,expect_status,must_kinds",
    [
        ("pass", "success", {"hitl", "risk"}),
        ("hitl_timeout", "failed", {"hitl"}),
        ("l2_denied", "failed", {"hitl", "risk"}),
        ("desktop_softfail", "failed", {"hitl"}),
    ],
)
def test_guards_demo_variants(tmp_path, variant, expect_status, must_kinds):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "goai_run_demo", _DEMO / "run_demo.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    out = mod.run_demo(
        mode="simulate",
        suite="guards",
        variant=variant,
        artifacts_dir=tmp_path / "art",
    )
    assert out.get("ok") is True, out
    assert out.get("status") == expect_status
    kinds = set(out.get("evidence_kinds") or [])
    assert must_kinds <= kinds, kinds

    pack = out.get("trace_pack") or {}
    assert pack.get("ok") is not False
    pack_dir = Path(pack.get("pack_dir") or "")
    assert pack_dir.is_dir()
    # Trace 应能看见 HITL / Risk 事件文件（按变体）
    stages = (pack_dir / "stage_results.json")
    assert stages.is_file()
    if "hitl" in must_kinds:
        # pass/timeout/denied 均经过 HITL；softfail 也经过
        assert any(
            (s.get("hitl_events") if isinstance(s, dict) else None)
            for s in __import__("json").loads(stages.read_text(encoding="utf-8"))
        )
    if variant == "l2_denied":
        data = __import__("json").loads(stages.read_text(encoding="utf-8"))
        l2 = [s for s in data if isinstance(s, dict) and s.get("stage_id") == "stage-l2-clear"]
        assert l2 and l2[0].get("ok_assert") is False
        assert l2[0].get("error_code") == "RISK_APPROVAL_REQUIRED"


def test_order_demo_still_works(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "goai_run_demo2", _DEMO / "run_demo.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    out = mod.run_demo(
        mode="simulate",
        variant="consistent",
        artifacts_dir=tmp_path / "art",
    )
    assert out.get("ok") is True
    assert out.get("status") == "success"
