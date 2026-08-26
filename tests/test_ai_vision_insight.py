"""ai_vision_insight 与 vision_step_report 单测。"""
import os
from pathlib import Path

from modules.ai.ai_vision_insight import insight_enabled, parse_yes_no_first_line, wait_vision_enabled
from modules.ai.ai_vision_grounding import locator_tier_vlm_enabled
from modules.ai.ai_vision_local import vision_enabled
from modules.ai.vision_step_report import VisionReplaySession, _friendly_step_label, vision_replay_enabled


def test_parse_yes_no():
    assert parse_yes_no_first_line("YES because ...") is True
    assert parse_yes_no_first_line("NO, not visible") is False
    assert parse_yes_no_first_line("是，可以看到") is True
    assert parse_yes_no_first_line("") is None


def test_defaults_on_when_env_unset(monkeypatch):
    for key in (
        "LOCATOR_TIER_VLM_ENABLE",
        "LOCAL_VISION_ENABLE",
        "AI_VISION_INSIGHT_ENABLE",
        "AI_WAIT_VISION_ENABLE",
        "VISION_STEP_REPORT_ENABLE",
    ):
        monkeypatch.delenv(key, raising=False)
    assert locator_tier_vlm_enabled() is True
    assert vision_enabled() is True
    assert insight_enabled() is True
    assert wait_vision_enabled() is True
    assert vision_replay_enabled() is True


def test_explicit_off(monkeypatch):
    monkeypatch.setenv("AI_VISION_INSIGHT_ENABLE", "0")
    assert insight_enabled() is False


def test_friendly_step_label():
    assert "确定" in _friendly_step_label({"action": "ai_tap", "description": "点击确定按钮"})
    assert _friendly_step_label({"action": "navigate"}) == "打开页面"


def test_vision_replay_session_finalize(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    sess = VisionReplaySession.start()
    sess.record(1, {"action": "click", "description": "登录"}, "success", "", b"fake", 120)
    meta = sess.finalize()
    assert meta["run_id"] == sess.run_id
    html_path = Path(tmp_path) / "reports" / "vision_replay" / sess.run_id / "index.html"
    assert html_path.is_file()
