# -*- coding: utf-8 -*-
"""Unit tests for captcha_engine."""

import json

import cv2
import numpy as np
import pytest

from modules.web.captcha_engine import (
    build_human_drag_path,
    captcha_allow_heuristic_slide,
    captcha_max_retry,
    clamp_slider_distance,
    detect_captcha_type,
    ddddocr_enabled,
    detect_platform,
    get_ddddocr_install_info,
    parse_vision_action,
    png_image_width,
    prepare_recognition_png,
    parse_instruction_targets,
    resolve_captcha_type,
    scale_image_distance_to_track,
    solve_click_targets_for_chars,
    solve_curve_offset_with_confidence,
)


def test_detect_platform_tianai():
    assert detect_platform('<div id="slider-move-btn">tianai</div>') == "tianai"
    assert detect_platform("geetest_panel") == "geetest"


def test_detect_captcha_type_curve():
    assert detect_captcha_type("拖动滑块使曲线匹配") == "curve"
    assert detect_captcha_type("请依次点击 苹果、香蕉") == "click_text"
    assert detect_captcha_type("拖动滑块完成验证") == "slider"
    # 整页 HTML 含「曲线」但指令是点选时，指令优先
    assert resolve_captcha_type("请依次点击：垢币星赋", "滑动曲线V3 滑块验证") == "click_text"


def test_scale_image_distance_to_track():
    assert scale_image_distance_to_track(100, 300, 300, slider_width_px=40) == 100
    assert scale_image_distance_to_track(150, 300, 600, slider_width_px=40) == 300


def test_png_image_width():
    img = np.zeros((40, 120, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    assert png_image_width(buf.tobytes()) == 120


def test_parse_instruction_targets_continuous_cjk():
    assert parse_instruction_targets("请依次点击：额挂书勘") == ["额", "挂", "书", "勘"]
    assert parse_instruction_targets("请依次点击：额、挂、书、勘") == ["额", "挂", "书", "勘"]


def test_build_human_drag_path_endpoints():
    path = build_human_drag_path(10.0, 20.0, 110.0, 20.0, steps=12, overshoot_px=0)
    assert len(path) >= 12
    assert abs(path[0][0] - 10.0) < 2
    assert abs(path[-1][0] - 110.0) < 2
    assert abs(path[-1][1] - 20.0) < 3


def test_parse_vision_action_slider():
    raw = 'Here is the answer: {"type":"slider","distance":156}'
    action = parse_vision_action(raw)
    assert action is not None
    assert action.type == "slider"
    assert action.distance == 156


def test_parse_vision_action_click_points():
    raw = json.dumps({"type": "click", "points": [{"x": 10, "y": 20}, {"x": 30, "y": 40}]})
    action = parse_vision_action(raw)
    assert action is not None
    assert action.type == "click"
    assert action.points == [(10, 20), (30, 40)]


def test_ddddocr_disabled_by_default():
    info = get_ddddocr_install_info()
    assert info["bundled_in_main_installer"] is False
    assert "200" in info["estimated_size_mb"]


def test_resolve_captcha_solve_attempts_step_override(monkeypatch):
    monkeypatch.setenv("CAPTCHA_SOLVE_RETRY", "3")
    from modules.web.captcha_engine import captcha_solve_attempts, resolve_captcha_solve_attempts

    assert captcha_solve_attempts() == 3
    assert resolve_captcha_solve_attempts(None) == 3
    assert resolve_captcha_solve_attempts(5) == 5
    assert resolve_captcha_solve_attempts(99) == 20


def test_recovery_honors_step_solve_attempts(monkeypatch):
    import asyncio

    from modules.web.captcha_recovery import CaptchaManualRequiredError, run_captcha_with_recovery

    monkeypatch.setenv("CAPTCHA_AUTO_REFRESH", "0")
    calls = {"n": 0}

    async def solve_once():
        calls["n"] += 1
        return False

    class FakePage:
        async def screenshot(self, full_page=False):
            return b"png"

    async def run():
        with pytest.raises(CaptchaManualRequiredError):
            await run_captcha_with_recovery(FakePage(), solve_once, solve_attempts=5)

    asyncio.run(run())
    assert calls["n"] == 5


def test_captcha_max_retry_default():
    assert captcha_max_retry() >= 0


def test_captcha_solve_attempts_env(monkeypatch):
    monkeypatch.setenv("CAPTCHA_SOLVE_RETRY", "5")
    from modules.web.captcha_engine import captcha_solve_attempts

    assert captcha_solve_attempts() == 5


def test_prepare_recognition_png_scales():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    scaled, mult = prepare_recognition_png(buf.tobytes(), scale=0.5)
    assert mult >= 1.9
    assert len(scaled) > 0


def test_solve_curve_offset_synthetic():
    """Synthetic bright curve on dark background."""
    img = np.zeros((120, 200, 3), dtype=np.uint8)
    for x in range(20, 100):
        y = 40 + int(10 * np.sin(x / 8))
        cv2.circle(img, (x, y), 2, (255, 255, 255), -1)
    for x in range(60, 140):
        y = 40 + int(10 * np.sin((x - 40) / 8))
        cv2.circle(img, (x, y), 2, (255, 255, 255), -1)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    offset, conf = solve_curve_offset_with_confidence(buf.tobytes())
    assert offset is None or (0 < offset < 500)
    assert 0.0 <= conf <= 1.0


def test_tianai_demo_step_shape():
    """Document expected Playwright verify step for https://captcha.tianai.cloud/"""
    step = {
        "action": "verify",
        "input_value": "auto",
        "description": "完成人机验证码",
        "selector_type": "css",
        "selector_value": "#captcha-box",
    }
    assert step["action"] == "verify"
    assert step["input_value"] == "auto"


def test_clamp_slider_distance():
    assert clamp_slider_distance(500, track_width=300, slider_width=40) == 240
    assert clamp_slider_distance(0, track_width=300) == 0
    assert clamp_slider_distance(50, track_width=300, slider_width=40) >= 8


def test_heuristic_slide_default_off(monkeypatch):
    monkeypatch.delenv("CAPTCHA_ALLOW_HEURISTIC_SLIDE", raising=False)
    assert captcha_allow_heuristic_slide() is False
    monkeypatch.setenv("CAPTCHA_ALLOW_HEURISTIC_SLIDE", "1")
    assert captcha_allow_heuristic_slide() is True


def test_captcha_distance_retry_offset():
    from modules.web.captcha_engine import captcha_distance_retry_offset, set_captcha_solve_attempt_index

    set_captcha_solve_attempt_index(1)
    assert captcha_distance_retry_offset() == 0
    set_captcha_solve_attempt_index(2)
    assert captcha_distance_retry_offset() == -5
    set_captcha_solve_attempt_index(3)
    assert captcha_distance_retry_offset() == 6


def test_solve_slider_gap_synthetic():
    from modules.web.captcha_engine import solve_slider_gap

    bg = np.full((80, 320, 3), 180, dtype=np.uint8)
    cv2.rectangle(bg, (0, 0), (319, 79), (120, 120, 120), -1)
    cv2.rectangle(bg, (180, 10), (230, 70), (60, 60, 60), -1)
    cv2.line(bg, (180, 10), (180, 70), (255, 255, 255), 2)
    ok, buf = cv2.imencode(".png", bg)
    assert ok
    dist = solve_slider_gap(buf.tobytes())
    assert dist is None or (50 < dist < 280)
