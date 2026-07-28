# -*- coding: utf-8 -*-
"""跨端 mobile_extract_otp / 工具面单测。"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def test_parse_otp_from_chinese_sms():
    from mobile_cross_end_tools import parse_otp_from_text

    assert parse_otp_from_text("【测试】您的验证码是 384921，5分钟内有效") == "384921"
    assert parse_otp_from_text("code: 112233") == "112233"
    assert parse_otp_from_text("hello world") is None


def test_mobile_extract_otp_uses_mock_env(monkeypatch):
    from mobile_cross_end_tools import mobile_extract_otp

    monkeypatch.setenv("MOBILE_OTP_MOCK", "654321")
    out = mobile_extract_otp(timeout_sec=5)
    assert out["success"] is True
    assert out["sms_otp"] == "654321"
    assert out["variables"]["sms_otp"] == "654321"
    assert out["source"] == "mock_env"


def test_mobile_extract_otp_awaits_device_job(monkeypatch):
    from mobile_cross_end_tools import mobile_extract_otp

    monkeypatch.delenv("MOBILE_OTP_MOCK", raising=False)

    def fake_enqueue(**kwargs):
        assert kwargs.get("job_kind") == "extract_otp"
        return "job-otp-1"

    def fake_wait(job_id, timeout_sec=120.0):
        assert job_id == "job-otp-1"
        return {
            "job_id": job_id,
            "status": "success",
            "result_payload": {
                "status": "success",
                "success": True,
                "sms_otp": "998877",
                "variables": {"sms_otp": "998877"},
            },
        }

    with patch("mobile_cross_end_tools.enqueue_mobile_job", side_effect=fake_enqueue):
        with patch("mobile_cross_end_tools.wait_mobile_job", side_effect=fake_wait):
            out = mobile_extract_otp(timeout_sec=30, mock_allowed=True)
    assert out["success"] is True
    assert out["sms_otp"] == "998877"
    assert out["job_id"] == "job-otp-1"
    assert out["source"] == "device_await"


def test_dispatch_cross_end_tool_desktop_alias():
    from mobile_cross_end_tools import DESKTOP_ALIAS_TOOL_NAMES, MOBILE_TOOL_NAMES, dispatch_cross_end_tool

    assert "desktop_click" in DESKTOP_ALIAS_TOOL_NAMES
    assert "mobile_extract_otp" in MOBILE_TOOL_NAMES
    with patch("mobile_cross_end_tools.desktop_click", return_value={"success": True}):
        out = dispatch_cross_end_tool("desktop_click", {"description": "确定"})
    assert out.get("success") is True


def test_cross_end_tool_schemas_include_mobile_and_desktop():
    from mobile_cross_end_tools import cross_end_tool_schemas

    names = {s["function"]["name"] for s in cross_end_tool_schemas()}
    assert "mobile_extract_otp" in names
    assert "desktop_launch" in names
    assert "mobile_run_steps" in names


def test_chat_tool_schemas_includes_cross_end():
    from ai_chat_tool_loop import chat_tool_schemas

    schemas = chat_tool_schemas(
        allow_hermes=False,
        platform_type="desktop",
        allow_desktop_windows_tools=True,
        allow_refine_test_plan=False,
    )
    names = {s["function"]["name"] for s in schemas if "function" in s}
    assert "mobile_extract_otp" in names
    assert "desktop_input" in names


def test_enqueue_run_job_stores_kind():
    from mobile_sync_store import enqueue_run_job, get_run_job

    jid = enqueue_run_job(
        case_id=0,
        steps=[{"action": "extract_otp"}],
        user_id=1,
        job_kind="extract_otp",
        job_meta={"skill": "extract_otp"},
    )
    job = get_run_job(jid)
    assert job["job_kind"] == "extract_otp"
    assert job["job_meta"]["skill"] == "extract_otp"


def test_pop_pending_job_kind_filter_does_not_swallow_run_steps():
    """冒烟缺陷回归：取码轮询不得把 run_steps 标成 running 后丢弃。"""
    import mobile_sync_store as mss
    from mobile_sync_store import (
        enqueue_run_job,
        get_run_job,
        pop_pending_run_for_device,
    )

    with mss._LOCK:
        mss._RUN_JOBS.clear()
        mss._RUN_EVENTS.clear()

    j_run = enqueue_run_job(
        case_id=1,
        steps=[{"action": "tap"}],
        user_id=1,
        device_id="dev-a",
        job_kind="run_steps",
    )
    j_otp = enqueue_run_job(
        case_id=0,
        steps=[{"action": "extract_otp"}],
        user_id=1,
        device_id="dev-a",
        job_kind="extract_otp",
    )
    got = pop_pending_run_for_device("dev-a", job_kind="extract_otp")
    assert got is not None
    assert got["job_id"] == j_otp
    assert got["job_kind"] == "extract_otp"
    # run_steps 仍应 pending，可被正式回放路径取走
    still = get_run_job(j_run)
    assert still["status"] == "pending"
    got_run = pop_pending_run_for_device("dev-a", job_kind="run_steps")
    assert got_run is not None and got_run["job_id"] == j_run


def test_demo_otp_plan_json_loads():
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "demos" / "cross_end" / "desktop_mobile_otp_plan.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    stages = data["stages"]
    assert stages[1]["skill"] == "extract_otp"
    assert stages[1]["layer"] == "mobile"
    assert "{{sms_otp}}" in stages[2]["steps"][0]["input_value"]
