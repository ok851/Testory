# -*- coding: utf-8 -*-
"""跨端 mobile_extract_otp / 工具面单测。"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def test_parse_otp_from_chinese_sms():
    from modules.mobile.mobile_cross_end_tools import parse_otp_from_text

    assert parse_otp_from_text("【测试】您的验证码是 384921，5分钟内有效") == "384921"
    assert parse_otp_from_text("code: 112233") == "112233"
    assert parse_otp_from_text("hello world") is None


def test_mobile_extract_otp_uses_mock_env(monkeypatch):
    from modules.mobile.mobile_cross_end_tools import mobile_extract_otp

    monkeypatch.setenv("MOBILE_OTP_MOCK", "654321")
    out = mobile_extract_otp(timeout_sec=5)
    assert out["success"] is True
    assert out["sms_otp"] == "654321"
    assert out["variables"]["sms_otp"] == "654321"
    assert out["source"] == "mock_env"


def test_mobile_extract_otp_awaits_device_job(monkeypatch):
    from modules.mobile.mobile_cross_end_tools import mobile_extract_otp

    monkeypatch.delenv("MOBILE_OTP_MOCK", raising=False)

    def fake_enqueue(**kwargs):
        assert kwargs.get("job_kind") == "extract_otp"
        return "job-otp-1"

    def fake_wait(job_id, timeout_sec=120.0, **kwargs):
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

    with patch("modules.mobile.mobile_cross_end_tools.enqueue_mobile_job", side_effect=fake_enqueue):
        with patch("modules.mobile.mobile_cross_end_tools.wait_mobile_job", side_effect=fake_wait):
            with patch(
                "modules.mobile.mobile_cross_end_tools.ensure_mobile_hand_ready",
                return_value=None,
            ):
                out = mobile_extract_otp(timeout_sec=30, mock_allowed=True)
    assert out["success"] is True
    assert out["sms_otp"] == "998877"
    assert out["job_id"] == "job-otp-1"
    assert out["source"] == "device_await"


def test_ensure_mobile_hand_ready_fails_without_pair():
    from modules.mobile.mobile_cross_end_tools import ensure_mobile_hand_ready, mobile_run_steps

    with patch(
        "modules.mobile.mobile_sync_store.list_paired_devices_for_user",
        return_value=[],
    ):
        err = ensure_mobile_hand_ready(user_id=1)
        assert err and err.get("error_code") == "MOBILE_HAND_OFFLINE"
        out = mobile_run_steps(
            [{"action": "open_app", "description": "QQ"}],
            user_id=1,
            timeout_sec=5,
        )
        assert out.get("success") is False
        assert out.get("error_code") == "MOBILE_HAND_OFFLINE"


def test_ensure_mobile_hand_ready_fails_when_poller_stale(monkeypatch):
    from modules.mobile.mobile_cross_end_tools import ensure_mobile_hand_ready

    monkeypatch.delenv("MOBILE_OTP_MOCK", raising=False)
    monkeypatch.delenv("MOBILE_HAND_SKIP_POLLER", raising=False)
    with patch(
        "modules.mobile.mobile_sync_store.list_paired_devices_for_user",
        return_value=[{"device_id": "dev-a", "paired_at": 1.0, "poller_alive": False}],
    ):
        with patch(
            "modules.mobile.mobile_sync_store.device_poller_status_for_user",
            return_value={"alive_count": 0, "stale_sec": 45, "best": None},
        ):
            err = ensure_mobile_hand_ready(user_id=1)
    assert err and err.get("error_code") == "MOBILE_POLLER_STALE"


def test_ensure_mobile_hand_ready_ok_when_poller_alive(monkeypatch):
    from modules.mobile.mobile_cross_end_tools import ensure_mobile_hand_ready

    monkeypatch.delenv("MOBILE_OTP_MOCK", raising=False)
    with patch(
        "modules.mobile.mobile_sync_store.list_paired_devices_for_user",
        return_value=[{"device_id": "dev-a", "paired_at": 1.0, "poller_alive": True}],
    ):
        with patch(
            "modules.mobile.mobile_sync_store.device_poller_status_for_user",
            return_value={"alive_count": 1, "stale_sec": 45, "best": {"device_id": "dev-a"}},
        ):
            assert ensure_mobile_hand_ready(user_id=1) is None


def test_touch_device_poll_marks_alive(tmp_path, monkeypatch):
    import time
    from modules.mobile import mobile_sync_store as store

    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    store._STORE_PATH = None
    store._JOBS_PATH = None
    with store._LOCK:
        store._DEVICE_TOKENS.clear()
        store._DEVICE_TOKENS["tok-abc"] = {
            "device_id": "dev-1",
            "user_id": 9,
            "paired_at": time.time(),
        }
    store.touch_device_poll(token="tok-abc", device_id="dev-1", user_id=9)
    devices = store.list_paired_devices_for_user(9)
    assert devices and devices[0].get("poller_alive") is True


def test_run_job_persists_across_memory_clear(tmp_path, monkeypatch):
    """模拟双进程：enqueue 落盘后清空内存，pop 仍能领到。"""
    from modules.mobile import mobile_sync_store as store

    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    store._STORE_PATH = None
    store._JOBS_PATH = None
    store._JOBS_FILE_MTIME = 0.0
    with store._LOCK:
        store._RUN_JOBS.clear()
        store._RUN_EVENTS.clear()
        store._DEVICE_TOKENS.clear()

    jid = store.enqueue_run_job(
        case_id=0,
        steps=[{"action": "open_app", "description": "QQ"}],
        user_id=1,
        device_id="",
        job_kind="run_steps",
    )
    assert (tmp_path / "mobile_sync" / "run_jobs.json").is_file()

    # 模拟另一进程：内存空，只靠磁盘
    with store._LOCK:
        store._RUN_JOBS.clear()
        store._RUN_EVENTS.clear()
        store._JOBS_FILE_MTIME = 0.0

    got = store.pop_pending_run_for_device("dev-x", job_kind="run_steps", user_id=1)
    assert got is not None
    assert got["job_id"] == jid
    assert got["status"] == "running"


def test_pop_fallback_ignores_stale_device_binding(tmp_path, monkeypatch):
    """历史 device_id 绑错时，同用户 agent 任务仍可被当前手机领取。"""
    from modules.mobile import mobile_sync_store as store

    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    store._STORE_PATH = None
    store._JOBS_PATH = None
    store._JOBS_FILE_MTIME = 0.0
    with store._LOCK:
        store._RUN_JOBS.clear()
        store._RUN_EVENTS.clear()

    jid = store.enqueue_run_job(
        case_id=0,
        steps=[{"action": "open_app"}],
        user_id=1,
        device_id="stale-old-android-id",
        job_kind="run_steps",
        source="mobile_run_steps",
    )
    got = store.pop_pending_run_for_device("unknown", job_kind="run_steps", user_id=1)
    assert got is not None
    assert got["job_id"] == jid


def test_enqueue_mobile_job_strips_device_id(tmp_path, monkeypatch):
    import time

    from modules.mobile.mobile_cross_end_tools import enqueue_mobile_job
    from modules.mobile import mobile_sync_store as store

    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    store._STORE_PATH = None
    store._JOBS_PATH = None
    store._JOBS_FILE_MTIME = 0.0
    with store._LOCK:
        store._RUN_JOBS.clear()
        store._DEVICE_TOKENS["tok"] = {
            "user_id": 1,
            "device_id": "9fb4aba4caf76b09",
            "paired_at": time.time(),
        }

    jid = enqueue_mobile_job(
        steps=[{"action": "open_app"}],
        user_id=1,
        device_id="9fb4aba4caf76b09",
        job_kind="run_steps",
    )
    job = store.get_run_job(jid)
    assert job["device_id"] == ""


def test_dispatch_cross_end_tool_desktop_alias():
    from modules.mobile.mobile_cross_end_tools import DESKTOP_ALIAS_TOOL_NAMES, MOBILE_TOOL_NAMES, dispatch_cross_end_tool

    assert "desktop_click" in DESKTOP_ALIAS_TOOL_NAMES
    assert "mobile_extract_otp" in MOBILE_TOOL_NAMES
    with patch("modules.mobile.mobile_cross_end_tools.desktop_click", return_value={"success": True}):
        out = dispatch_cross_end_tool("desktop_click", {"description": "确定"})
    assert out.get("success") is True


def test_cross_end_tool_schemas_include_mobile_and_desktop():
    from modules.mobile.mobile_cross_end_tools import cross_end_tool_schemas

    names = {s["function"]["name"] for s in cross_end_tool_schemas()}
    assert "mobile_extract_otp" in names
    assert "desktop_launch" in names
    assert "mobile_run_steps" in names


def test_chat_tool_schemas_includes_cross_end():
    from modules.ai.ai_chat_tool_loop import chat_tool_schemas

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
    from modules.mobile.mobile_sync_store import enqueue_run_job, get_run_job

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


def test_pop_pending_job_kind_filter_does_not_swallow_run_steps(tmp_path, monkeypatch):
    """冒烟缺陷回归：取码轮询不得把 run_steps 标成 running 后丢弃。"""
    from modules.mobile import mobile_sync_store as mss
    from modules.mobile.mobile_sync_store import (
        enqueue_run_job,
        get_run_job,
        pop_pending_run_for_device,
    )

    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    mss._STORE_PATH = None
    mss._JOBS_PATH = None
    mss._JOBS_FILE_MTIME = 0.0
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


def test_normalize_device_step_expands_mobile_spec_string():
    from modules.mobile.mobile_sync_store import normalize_device_step

    step = {
        "action": "assert",
        "mobile_spec": '{"assert_text":"登录成功","save_as":"login_ok","max_retries":2}',
    }
    out = normalize_device_step(step)
    assert isinstance(out["mobile_spec"], dict)
    assert out["assert_text"] == "登录成功"
    assert out["save_as"] == "login_ok"
    assert out["max_retries"] == 2


def test_normalize_device_step_coerces_launch_app_to_open_app():
    from modules.mobile.mobile_sync_store import normalize_device_step

    out = normalize_device_step(
        {"action": "start_app", "description": "打开QQ应用"}
    )
    assert out["action"] == "open_app"
    assert out.get("package_name") == "com.tencent.mobileqq"
    assert out.get("selector_value") == "com.tencent.mobileqq"
    assert out["mobile_spec"].get("packageName") == "com.tencent.mobileqq"

    out2 = normalize_device_step(
        {"action": "launch_app", "package_name": "com.tencent.mm", "description": "微信"}
    )
    assert out2["action"] == "open_app"
    assert out2["mobile_spec"]["packageName"] == "com.tencent.mm"


def test_normalize_device_step_find_and_tap_to_tap():
    from modules.mobile.mobile_sync_store import normalize_device_step

    out = normalize_device_step(
        {"action": "find_and_tap", "description": "点击登录", "text": "登录"}
    )
    assert out["action"] == "tap"
    assert out["selector_type"] == "text"
    assert out["selector_value"] == "登录"


def test_normalize_tap_from_description_only():
    from modules.mobile.mobile_sync_store import normalize_device_step

    out = normalize_device_step({"action": "tap", "description": "登录"})
    assert out["action"] == "tap"
    assert out["selector_type"] == "text"
    assert out["selector_value"] == "登录"
    assert out["mobile_spec"]["text"] == "登录"

    out2 = normalize_device_step({"action": "tap", "description": "点击登录按钮"})
    assert out2["selector_value"] == "登录"

    out3 = normalize_device_step({"action": "tap", "description": "同意并继续"})
    assert out3["selector_value"] == "同意并继续"


def test_normalize_input_moves_phone_to_input_value():
    from modules.mobile.mobile_sync_store import normalize_device_step

    out = normalize_device_step(
        {
            "action": "input",
            "description": "输入手机号",
            "text": "16608943238",
        }
    )
    assert out["action"] == "input"
    assert out["input_value"] == "16608943238"
    assert out["selector_type"] == "text"
    assert out["selector_value"] == "手机号"
    assert out["mobile_spec"]["text"] == "手机号"


def test_normalize_wait_timeout_field():
    from modules.mobile.mobile_sync_store import normalize_device_step

    out = normalize_device_step(
        {"action": "wait", "description": "等待QQ加载", "timeout": 5000}
    )
    assert out["action"] == "wait"
    assert out["wait_duration_ms"] == 5000


def test_normalize_check_intent_sets_prefer_checkable():
    from modules.mobile.mobile_sync_store import normalize_device_step

    out = normalize_device_step(
        {"action": "tap", "description": "勾选同意协议", "selector_value": "已阅读并同意"}
    )
    assert out["action"] == "tap"
    assert out.get("prefer_checkable") is True
    assert out["mobile_spec"].get("prefer_checkable") is True
    assert out["selector_type"] == "text"


def test_record_mobile_tool_outcome_halts_after_two_failures():
    from modules.ai.ai_chat_tool_loop import _record_mobile_tool_outcome

    meta: dict = {}
    fail = '{"success": false, "ok": false, "error": "TAP_NO_TARGET"}'
    _record_mobile_tool_outcome(meta, "mobile_run_steps", fail)
    assert meta.get("mobile_fail_streak") == 1
    assert not meta.get("mobile_flow_halted")
    _record_mobile_tool_outcome(meta, "mobile_run_steps", fail)
    assert meta.get("mobile_fail_streak") == 2
    assert meta.get("mobile_flow_halted") is True
    assert "连续失败" in (meta.get("halt_reply") or "")
    _record_mobile_tool_outcome(
        meta, "mobile_run_steps", '{"success": true, "ok": true}'
    )
    # 已 halted 不因后续成功清除 halted（仅清 streak）；当前实现成功会清 streak
    assert meta.get("mobile_fail_streak") == 0


def test_busy_event_requeues_pending():
    from modules.mobile import mobile_sync_store as mss
    from modules.mobile.mobile_sync_store import append_run_events, enqueue_run_job, get_run_job, pop_pending_run_for_device

    with mss._LOCK:
        mss._RUN_JOBS.clear()
        mss._RUN_EVENTS.clear()

    jid = enqueue_run_job(
        case_id=1,
        steps=[{"action": "tap"}],
        user_id=1,
        device_id="dev-busy",
        job_kind="run_steps",
    )
    popped = pop_pending_run_for_device("dev-busy", job_kind="run_steps")
    assert popped and popped["job_id"] == jid
    assert get_run_job(jid)["status"] == "running"
    ok = append_run_events(
        jid,
        {"status": "busy", "error_code": "MOBILE_BUSY", "error": "busy"},
    )
    assert ok is True
    assert get_run_job(jid)["status"] == "pending"
    again = pop_pending_run_for_device("dev-busy", job_kind="run_steps")
    assert again and again["job_id"] == jid


def test_resolve_cross_end_vars_substitutes_sms_otp():
    from modules.ai.ai_chat_tool_loop import _resolve_cross_end_vars

    out = _resolve_cross_end_vars(
        {"text": "验证码 {{sms_otp}}", "nested": {"v": "{{sms_otp}}"}},
        {"sms_otp": "123456"},
    )
    assert out["text"] == "验证码 123456"
    assert out["nested"]["v"] == "123456"
