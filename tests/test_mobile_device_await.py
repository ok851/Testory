# -*- coding: utf-8 -*-
"""跨端 mobile await_device_run：PC 等待手机本机执行上报。"""
from __future__ import annotations

from unittest.mock import patch

from ai_modules.execute.orchestrator import _execute_ui_stage
from ai_modules.plan.context_bus import CrossEndContext


def _ctx():
    return CrossEndContext()


def test_mobile_await_device_run_success():
    stage = {
        "id": "m-await",
        "layer": "mobile",
        "await_device_run": True,
        "await_timeout_sec": 2,
        "case_id": 0,
        "steps": [{"action": "tap", "description": "点登录"}],
    }

    def fake_wait(job_id, timeout_sec=600.0, poll_interval_sec=1.0):
        return {
            "job_id": job_id,
            "status": "success",
            "result_payload": {
                "status": "success",
                "success": True,
                "results": [{"status": "success", "action": "tap"}],
            },
        }

    with patch("modules.mobile.mobile_sync_store.enqueue_run_job", return_value="job-abc") as enq:
        with patch("modules.mobile.mobile_sync_store.wait_for_run_job", side_effect=fake_wait):
            result, _ = _execute_ui_stage(stage, _ctx())
    assert enq.called
    assert result.get("executor") == "await_device_run"
    assert result.get("ok_assert") is True
    assert result.get("mobile_job_id") == "job-abc"


def test_mobile_await_device_run_timeout_fails_honestly():
    stage = {
        "id": "m-timeout",
        "layer": "android",
        "await_device_run": True,
        "await_timeout_sec": 1,
        "steps": [{"action": "tap"}],
    }

    def fake_wait(job_id, timeout_sec=600.0, poll_interval_sec=1.0):
        return {
            "job_id": job_id,
            "status": "error",
            "error": "等待手机本机执行超时",
            "error_code": "MOBILE_DEVICE_AWAIT_TIMEOUT",
        }

    with patch("modules.mobile.mobile_sync_store.enqueue_run_job", return_value="job-t"):
        with patch("modules.mobile.mobile_sync_store.wait_for_run_job", side_effect=fake_wait):
            result, _ = _execute_ui_stage(stage, _ctx())
    assert result.get("ok_assert") is False
    assert result.get("error_code") == "MOBILE_DEVICE_AWAIT_TIMEOUT"
