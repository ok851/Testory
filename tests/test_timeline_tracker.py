# -*- coding: utf-8 -*-
"""TimelineTracker 单元测试：事件记录、断点、单步、var_diff、SSE。"""
from __future__ import annotations

import json
import threading
import time

import pytest

from ai_modules.execute.timeline_tracker import (
    TimelineTracker,
    get_or_create_tracker,
    get_tracker,
    list_trackers,
    remove_tracker,
)


# ------------------------------------------------------------------
# 基础生命周期
# ------------------------------------------------------------------

class TestTimelineLifecycle:
    def test_create_and_mark_start_finish(self):
        t = TimelineTracker("run-001", plan_id="p1", scenario="test")
        assert t.status == "created"
        assert t.run_id == "run-001"

        t.mark_start()
        assert t.status == "running"
        assert t.started_at != ""

        t.mark_finish(success=True)
        assert t.status == "success"
        assert t.finished_at != ""

    def test_mark_finish_failed(self):
        t = TimelineTracker("run-002")
        t.mark_start()
        t.mark_finish(success=False, error="boom")
        assert t.status == "failed"

    def test_to_dict_contains_all_fields(self):
        t = TimelineTracker("run-003", plan_id="p1", scenario="s1")
        t.mark_start()
        d = t.to_dict()
        assert d["run_id"] == "run-003"
        assert d["plan_id"] == "p1"
        assert "events" in d
        assert "stages" in d
        assert "variables" in d
        assert "breakpoints" in d
        assert "event_count" in d


# ------------------------------------------------------------------
# 事件记录
# ------------------------------------------------------------------

class TestTimelineEvents:
    def test_stage_start_end(self):
        t = TimelineTracker("run-e1")
        t.stage_start("s1", layer="api", executor="classic")
        assert "s1" in t.stages
        assert t.stages["s1"].status == "running"
        assert t.stages["s1"].layer == "api"

        t.stage_end("s1", ok=True, elapsed_ms=123.4, steps_executed=5)
        assert t.stages["s1"].status == "success"
        assert t.stages["s1"].elapsed_ms == 123.4

    def test_stage_end_failure(self):
        t = TimelineTracker("run-e2")
        t.stage_start("s1")
        t.stage_end("s1", ok=False, error="timeout", error_code="SYNC_TIMEOUT")
        assert t.stages["s1"].status == "failed"
        assert t.stages["s1"].error == "timeout"

    def test_var_write_non_sensitive(self):
        t = TimelineTracker("run-e3")
        t.var_write("order_id", "ORD-123", source="stage-1")
        assert t.variables["order_id"] == "ORD-123"

    def test_var_write_sensitive_redacted(self):
        t = TimelineTracker("run-e3b")
        t.var_write("api_password", "secret123")
        assert t.variables["api_password"] == "***"

    def test_device_event(self):
        t = TimelineTracker("run-e4")
        t.device_event("dev-001", "connected", {"model": "Pixel"})
        assert len(t.events) >= 1
        last = t.events[-1]
        assert last.kind == "device_event"
        assert last.detail["device_udid"] == "dev-001"

    def test_hitl_event(self):
        t = TimelineTracker("run-e5")
        t.hitl_event("gate-1", "resumed", {"user": "admin"})
        last = t.events[-1]
        assert last.kind == "hitl"
        assert last.detail["outcome"] == "resumed"

    def test_risk_event(self):
        t = TimelineTracker("run-e6")
        t.risk_event("L2", "denied", {"reason": "no token"})
        last = t.events[-1]
        assert last.kind == "risk"


# ------------------------------------------------------------------
# 断点 & 单步
# ------------------------------------------------------------------

class TestBreakpointsAndStepMode:
    def test_set_and_check_breakpoint(self):
        t = TimelineTracker("run-bp1")
        t.set_breakpoint("s1", condition="", enabled=True)
        assert t.check_breakpoint("s1") is True
        assert t.check_breakpoint("s2") is False

    def test_remove_breakpoint(self):
        t = TimelineTracker("run-bp2")
        t.set_breakpoint("s1")
        t.remove_breakpoint("s1")
        assert t.check_breakpoint("s1") is False

    def test_conditional_breakpoint_variable_exists(self):
        """条件断点：变量存在即命中。"""
        t = TimelineTracker("run-bp3")
        t.set_breakpoint("s1", condition="error_count")
        # 变量不存在 → 不命中
        assert t.check_breakpoint("s1") is False
        # 设置变量后 → 命中
        t.var_write("error_count", 0)
        assert t.check_breakpoint("s1") is True

    def test_step_mode_all_stages_break(self):
        t = TimelineTracker("run-sm1")
        t.set_step_mode(True)
        assert t._step_mode is True
        # 单步模式下每阶段都命中
        assert t.check_breakpoint("s1") is True
        assert t.check_breakpoint("s2") is True

    def test_step_mode_pause_resume(self):
        t = TimelineTracker("run-sm2")
        reached = threading.Event()

        def waiter():
            t.pause_at("s1")
            reached.set()

        th = threading.Thread(target=waiter, daemon=True)
        th.start()
        time.sleep(0.1)
        assert not reached.is_set()  # should be blocked

        t.resume()
        th.join(timeout=2)
        assert reached.is_set()

    def test_is_paused(self):
        t = TimelineTracker("run-sm3")
        assert t.is_paused() is False


# ------------------------------------------------------------------
# var_diff
# ------------------------------------------------------------------

class TestVarDiff:
    def test_var_diff_basic(self):
        t = TimelineTracker("run-vd1")
        t.var_write("x", 1, source="init")
        t.snapshot_vars_before("s1")
        t.var_write("x", 2, source="s1")
        t.var_write("y", 10, source="s1")

        diff = t.get_var_diff("s1")
        # var_write stores as str(value)[:200]
        assert "y" in diff["added"]
        assert diff["changed"]["x"]["before"] == "1"
        assert diff["changed"]["x"]["after"] == "2"

    def test_var_diff_no_changes(self):
        t = TimelineTracker("run-vd2")
        t.var_write("a", 1)
        t.snapshot_vars_before("s1")
        diff = t.get_var_diff("s1")
        assert diff["added"] == {}
        assert diff["changed"] == {}
        assert diff["removed"] == {}

    def test_var_diff_removed(self):
        t = TimelineTracker("run-vd3")
        t.var_write("old_key", "val")
        t.snapshot_vars_before("s1")
        t.variables = {}
        diff = t.get_var_diff("s1")
        assert "old_key" in diff["removed"]


# ------------------------------------------------------------------
# SSE 队列
# ------------------------------------------------------------------

class TestSSEQueues:
    def test_add_remove_sse_queue(self):
        t = TimelineTracker("run-sse1")
        q = []
        t.add_sse_queue(q)
        t.var_write("order_id", "ORD-1")
        assert len(q) == 1
        payload = json.loads(q[0])
        assert payload["kind"] == "var_write"

        t.remove_sse_queue(q)
        t.var_write("order_id2", "ORD-2")
        assert len(q) == 1

    def test_dead_queue_auto_removed(self):
        t = TimelineTracker("run-sse2")

        class DeadQueue:
            def append(self, _):
                raise RuntimeError("dead")

        dq = DeadQueue()
        t.add_sse_queue(dq)
        t.var_write("k", "v")
        assert dq not in t._sse_queues


# ------------------------------------------------------------------
# 全局管理
# ------------------------------------------------------------------

class TestGlobalTrackers:
    def test_get_or_create(self):
        t1 = get_or_create_tracker("run-g1", plan_id="p1")
        t2 = get_or_create_tracker("run-g1")
        assert t1 is t2
        assert t1.plan_id == "p1"
        remove_tracker("run-g1")

    def test_get_tracker(self):
        get_or_create_tracker("run-g2")
        assert get_tracker("run-g2") is not None
        assert get_tracker("nonexistent") is None
        remove_tracker("run-g2")

    def test_list_trackers(self):
        get_or_create_tracker("run-l1")
        get_or_create_tracker("run-l2")
        result = list_trackers(limit=10)
        ids = [t["run_id"] for t in result]
        assert "run-l1" in ids
        remove_tracker("run-l1")
        remove_tracker("run-l2")

    def test_remove_tracker(self):
        get_or_create_tracker("run-rm1")
        assert remove_tracker("run-rm1") is True
        assert remove_tracker("run-rm1") is False

    def test_max_trackers_eviction(self):
        from ai_modules.execute.timeline_tracker import _TRACKERS, _MAX_TRACKERS
        for i in range(_MAX_TRACKERS + 10):
            get_or_create_tracker(f"run-evict-{i}")
        assert len(_TRACKERS) <= _MAX_TRACKERS + 5
        for i in range(_MAX_TRACKERS + 10):
            remove_tracker(f"run-evict-{i}")
