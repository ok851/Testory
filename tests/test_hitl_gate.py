# -*- coding: utf-8 -*-
"""HITL gate：阻塞 / 超时 / 取消 / 会话隔离 / 编排接入。"""

import threading
import time

import pytest

from agent_hitl import (
    cancel_hitl_gate,
    clear_hitl_gate,
    get_hitl_gate,
    mark_user_resumed,
    open_hitl_gate,
    reset_hitl_state_for_tests,
    resume_hitl_gate,
    set_need_user_action,
    wait_hitl_gate,
)
from ai_modules.execute.orchestrator import execute_cross_end_plan
from ai_modules.plan.context_bus import CrossEndContext
from ai_modules.plan.sync_manager import SyncPointManager


@pytest.fixture(autouse=True)
def _clean_hitl():
    reset_hitl_state_for_tests()
    yield
    reset_hitl_state_for_tests()


def test_wait_timeout_returns_false():
    open_hitl_gate("g-timeout", reason="captcha")
    assert wait_hitl_gate("g-timeout", timeout_s=0.35, poll_interval_s=0.05) is False
    assert get_hitl_gate("g-timeout") is None


def test_wait_resume_returns_true():
    open_hitl_gate("g-ok", reason="login")

    def _resume():
        time.sleep(0.15)
        assert resume_hitl_gate("g-ok") is True

    t = threading.Thread(target=_resume)
    t.start()
    assert wait_hitl_gate("g-ok", timeout_s=2.0, poll_interval_s=0.05) is True
    t.join(timeout=2)
    assert get_hitl_gate("g-ok") is None


def test_resume_idempotent():
    open_hitl_gate("g-idemp", reason="x")
    assert resume_hitl_gate("g-idemp") is True
    assert resume_hitl_gate("g-idemp") is True
    assert wait_hitl_gate("g-idemp", timeout_s=1.0, poll_interval_s=0.05) is True


def test_cancel_returns_false():
    open_hitl_gate("g-cancel", reason="x")

    def _cancel():
        time.sleep(0.1)
        cancel_hitl_gate("g-cancel")

    t = threading.Thread(target=_cancel)
    t.start()
    assert wait_hitl_gate("g-cancel", timeout_s=2.0, poll_interval_s=0.05) is False
    t.join(timeout=2)


def test_zero_timeout_not_success():
    open_hitl_gate("g-zero", reason="x")
    assert wait_hitl_gate("g-zero", timeout_s=0) is False


def test_missing_gate_without_auto_open_fails():
    assert wait_hitl_gate("missing", timeout_s=0.2, auto_open=False) is False


def test_session_isolation():
    open_hitl_gate("gate-a", reason="a", user_id="u1")
    open_hitl_gate("gate-b", reason="b", user_id="u2")
    assert resume_hitl_gate("gate-a") is True
    assert get_hitl_gate("gate-b")["status"] == "waiting"
    assert wait_hitl_gate("gate-a", timeout_s=1.0) is True
    assert get_hitl_gate("gate-b")["status"] == "waiting"


def test_mark_user_resumed_links_gate():
    open_hitl_gate("linked", reason="captcha", user_id="42")
    set_need_user_action("42", session_id="linked", reason="captcha")
    assert mark_user_resumed("42") is True
    assert get_hitl_gate("linked")["status"] == "resumed"
    assert wait_hitl_gate("linked", timeout_s=1.0) is True


def test_resume_after_timeout_fails():
    open_hitl_gate("late", reason="x")
    assert wait_hitl_gate("late", timeout_s=0.2, poll_interval_s=0.05) is False
    assert resume_hitl_gate("late") is False


def test_sync_manager_wait_for_human_not_always_true():
    ctx = CrossEndContext(plan_id="p1", scenario="s")
    mgr = SyncPointManager(ctx)
    assert mgr.wait_for_human("请确认", timeout_s=0.25, gate_id="sm-1") is False


def test_sync_manager_wait_resume():
    ctx = CrossEndContext(plan_id="p2", scenario="s")
    mgr = SyncPointManager(ctx)

    def _resume():
        time.sleep(0.12)
        resume_hitl_gate("sm-2")

    t = threading.Thread(target=_resume)
    t.start()
    assert mgr.wait_for_human("ok", timeout_s=2.0, gate_id="sm-2") is True
    t.join(timeout=2)


def test_orchestrator_hitl_timeout_fails_plan():
    plan = {
        "plan_id": "hitl-plan-1",
        "scenario": "hitl timeout",
        "stages": [
            {
                "id": "h1",
                "layer": "hitl",
                "prompt": "请输入验证码",
                "timeout_s": 0.3,
                "poll_interval_s": 0.05,
                "sync_point": "hitl_done",
            }
        ],
    }
    out = execute_cross_end_plan(plan, acquire_lock=False)
    assert out.get("success") is False
    assert out["stage_results"][0]["ok_assert"] is False
    assert "HITL" in (out.get("error") or out["stage_results"][0].get("error") or "")


def test_orchestrator_hitl_resume_passes():
    plan = {
        "plan_id": "hitl-plan-2",
        "scenario": "hitl ok",
        "stages": [
            {
                "id": "h2",
                "layer": "hitl",
                "gate_id": "orch-gate-2",
                "prompt": "确认登录",
                "timeout_s": 2.0,
                "poll_interval_s": 0.05,
                "sync_point": "hitl_done",
            }
        ],
    }

    def _resume():
        time.sleep(0.15)
        resume_hitl_gate("orch-gate-2")

    t = threading.Thread(target=_resume)
    t.start()
    out = execute_cross_end_plan(plan, acquire_lock=False)
    t.join(timeout=3)
    assert out.get("success") is True
    assert out["stage_results"][0]["ok_assert"] is True


def test_clear_unknown_gate_safe():
    clear_hitl_gate("nope")
