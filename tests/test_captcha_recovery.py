# -*- coding: utf-8 -*-
"""Tests for captcha recovery state machine."""

import pytest

from captcha_engine import captcha_auto_refresh_enabled, captcha_solve_attempts, captcha_total_solve_slots
from captcha_recovery import CaptchaManualRequiredError, run_captcha_with_recovery, save_captcha_failure_screenshot


def test_captcha_manual_required_error():
    err = CaptchaManualRequiredError("请手动完成", screenshot_path="/tmp/x.png")
    assert "请手动完成" in str(err)
    assert err.screenshot_path == "/tmp/x.png"


def test_save_failure_screenshot_empty():
    assert save_captcha_failure_screenshot(b"") is None


def test_solve_attempts_default(monkeypatch):
    monkeypatch.delenv("CAPTCHA_SOLVE_RETRY", raising=False)
    assert captcha_solve_attempts() == 3


def test_auto_refresh_default_off(monkeypatch):
    monkeypatch.delenv("CAPTCHA_AUTO_REFRESH", raising=False)
    assert captcha_auto_refresh_enabled() is False
    assert captcha_total_solve_slots() == 3


def test_recovery_same_image_retries_without_refresh(monkeypatch):
    import asyncio

    monkeypatch.setenv("CAPTCHA_SOLVE_RETRY", "3")
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
            await run_captcha_with_recovery(FakePage(), solve_once)

    asyncio.run(run())
    assert calls["n"] == 3
