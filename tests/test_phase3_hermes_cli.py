"""hermes_heal_bridge vlm_ground 与 testory_cli 单测。"""
import json

from hermes_heal_bridge import (
    apply_vlm_ground_heal_to_step,
    build_vlm_ground_heal_candidate,
    merge_vlm_ground_into_locator_candidates,
)
from locator_tier_utils import parse_vlm_ground_prompt, split_locator_candidates


def test_build_vlm_ground_heal_candidate(monkeypatch):
    monkeypatch.delenv("LOCATOR_TIER_VLM_ENABLE", raising=False)
    cand = build_vlm_ground_heal_candidate("登录按钮")
    assert cand is not None
    assert cand.get("selector_type") == "vlm_ground"
    assert "登录" in parse_vlm_ground_prompt(cand.get("selector_value") or "")


def test_merge_vlm_ground_into_locator_candidates():
    merged = merge_vlm_ground_into_locator_candidates(None, "提交")
    assert merged
    arr = json.loads(merged) if isinstance(merged, str) else merged
    _, _, _, vlm = split_locator_candidates(arr)
    assert len(vlm) >= 1


def test_apply_vlm_ground_heal_to_step():
    step = {"action": "click", "description": "确定", "selector_value": "#ok"}
    assert apply_vlm_ground_heal_to_step(step) is True
    assert step.get("locator_candidates")


def test_testory_cli_web_readiness_help():
    import pytest
    from testory_cli.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["web", "readiness", "--help"])
    assert exc.value.code == 0
