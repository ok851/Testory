# -*- coding: utf-8 -*-
"""R06 Demo：下单失败故事离线可复现。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DEMO_SCRIPT = _ROOT / "demos" / "goai-agentteams" / "run_demo.py"


def _load_run_demo():
    spec = importlib.util.spec_from_file_location("goai_agentteams_run_demo", _DEMO_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def demo():
    return _load_run_demo()


def test_story_input_has_plan_and_assertions(demo):
    from pathlib import Path

    story = demo.load_story(Path(demo.__file__).resolve().parent / "input" / "order_fail_story.json")
    assert story["story_id"] == "order-fail-consistency"
    plan = story["plan"]
    assert len(plan["stages"]) >= 2
    assert plan["stages"][0]["layer"] == "api"
    assert plan["stages"][1]["layer"] == "web"
    assert len(plan["cross_end_assertions"]) >= 2


def test_simulate_consistent_passes_and_writes_artifacts(demo, tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "artifacts"
    result = demo.run_demo(mode="simulate", variant="consistent", artifacts_dir=out)
    assert result["ok"] is True
    assert result["status"] == "success"
    agents = result["agents_seen"]
    assert "Planner" in agents
    assert "WebApiExecutor" in agents
    assert "Verifier" in agents
    art = Path(result["artifacts_dir"])
    assert (art / "report.json").is_file()
    assert (art / "timeline.json").is_file()
    assert (art / "test_run_state.json").is_file()
    assert (art / "SUMMARY.md").is_file()
    assert result["report"]["passed"] is True


def test_simulate_mismatch_is_honest_red(demo, tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "artifacts"
    result = demo.run_demo(mode="simulate", variant="mismatch", artifacts_dir=out)
    assert result["ok"] is True  # 符合 expect_run_status=failed
    assert result["status"] == "failed"
    assert result["report"]["passed"] is False
    assert "Verifier" in result["agents_seen"]


def test_cli_main_consistent_exit_0(demo, tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path / "data"))
    code = demo.main([
        "--mode", "simulate",
        "--variant", "consistent",
        "--artifacts-dir", str(tmp_path / "art"),
    ])
    assert code == 0
