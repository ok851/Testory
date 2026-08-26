"""ai_vision_grounding 解析与开关单测。"""
import os

from modules.ai.ai_vision_grounding import (
    parse_grounding_response,
    locator_tier_vlm_enabled,
    collect_vlm_prompts,
)
from modules.web.locator_tier_utils import build_vlm_ground_candidate


def test_parse_grounding_pixels():
    hit = parse_grounding_response('{"x": 100, "y": 200}', viewport_w=1280, viewport_h=720)
    assert hit is not None
    assert hit.cx == 100
    assert hit.cy == 200


def test_parse_grounding_ratio():
    hit = parse_grounding_response('{"fx": 0.5, "fy": 0.25}', viewport_w=1000, viewport_h=800)
    assert hit is not None
    assert hit.cx == 500
    assert hit.cy == 200


def test_parse_grounding_qwen_point():
    hit = parse_grounding_response(
        '{"point": {"x": 500, "y": 500}}',
        viewport_w=1000,
        viewport_h=1000,
    )
    assert hit is not None
    assert 490 <= hit.cx <= 510
    assert 490 <= hit.cy <= 510


def test_collect_vlm_prompts():
    lc = [build_vlm_ground_candidate("确定按钮")]
    prompts = collect_vlm_prompts(lc, locate_prompt="", description="备用描述")
    assert "确定按钮" in prompts


def test_locator_tier_vlm_env():
    os.environ["LOCATOR_TIER_VLM_ENABLE"] = "1"
    try:
        assert locator_tier_vlm_enabled() is True
    finally:
        os.environ.pop("LOCATOR_TIER_VLM_ENABLE", None)


def test_locator_tier_vlm_default_on():
    os.environ.pop("LOCATOR_TIER_VLM_ENABLE", None)
    assert locator_tier_vlm_enabled() is True
    os.environ["LOCATOR_TIER_VLM_ENABLE"] = "0"
    try:
        assert locator_tier_vlm_enabled() is False
    finally:
        os.environ.pop("LOCATOR_TIER_VLM_ENABLE", None)
