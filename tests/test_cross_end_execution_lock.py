# -*- coding: utf-8 -*-
"""跨端执行锁门禁：忙拒绝 / ImportError 不绕过 / 释放后可再入。"""

from contextlib import contextmanager
from unittest.mock import patch

from ai_modules.execute.orchestrator import execute_cross_end_plan
from execution_lock import ExecutionLockError


def _tiny_plan(plan_id="lock-plan"):
    return {
        "plan_id": plan_id,
        "scenario": "lock test",
        "stages": [
            {
                "id": "h1",
                "layer": "hitl",
                "prompt": "x",
                "timeout_s": 0.2,
                "poll_interval_s": 0.05,
                "sync_point": "done",
            }
        ],
    }


def test_cross_end_lock_busy_returns_failure():
    @contextmanager
    def _busy(**kwargs):
        raise ExecutionLockError("本机已有自动化任务在执行（Playwright/桌面），请稍后再试。")
        yield False  # pragma: no cover

    with patch("execution_lock.execution_guard", _busy):
        out = execute_cross_end_plan(_tiny_plan("busy-1"), lock_timeout_sec=1)
    assert out.get("success") is False
    assert out.get("lock") == "busy"
    assert out.get("error_code") == "EXECUTION_LOCK_BUSY"
    assert out.get("stage_results") == []


def test_cross_end_lock_import_error_not_bypass():
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "execution_lock":
            raise ImportError("missing execution_lock")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=guarded_import):
        out = execute_cross_end_plan(_tiny_plan("imp-2"), lock_timeout_sec=1)
    assert out.get("success") is False
    assert out.get("lock") == "unavailable"
    assert out.get("error_code") == "EXECUTION_LOCK_UNAVAILABLE"


def test_cross_end_acquire_lock_false_skips():
    out = execute_cross_end_plan(_tiny_plan("skip-1"), acquire_lock=False)
    assert out.get("lock") == "skipped"
    # HITL 超时仍应失败（业务），但锁被跳过
    assert out.get("success") is False


def test_cross_end_lock_held_then_released_allows_second():
    calls = {"n": 0}

    @contextmanager
    def _ok(**kwargs):
        calls["n"] += 1
        yield True

    with patch("execution_lock.execution_guard", _ok):
        out1 = execute_cross_end_plan(_tiny_plan("held-1"), lock_timeout_sec=1)
        out2 = execute_cross_end_plan(_tiny_plan("held-2"), lock_timeout_sec=1)
    assert calls["n"] == 2
    assert out1.get("lock") == "held"
    assert out2.get("lock") == "held"
