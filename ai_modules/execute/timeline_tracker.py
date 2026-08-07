# -*- coding: utf-8 -*-
"""跨端执行时间线追踪器：实时记录阶段/变量/设备事件，供前端可视化调试。

功能：
- 每次跨端执行创建独立 TimelineTracker 实例
- 记录 stage 开始/结束、变量写入、设备状态、HITL/Risk 事件
- 支持 SSE 实时推送（通过 /api/ai/cross-end/timeline/<run_id>/events）
- 执行完成后生成完整时间线 JSON 供离线分析
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

_LOCK = threading.RLock()
# run_id -> TimelineTracker
_TRACKERS: Dict[str, "TimelineTracker"] = {}
_MAX_TRACKERS = 50

try:
    from uat_logger import uat_logger
except Exception:
    import logging
    uat_logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TimelineEvent:
    """单条时间线事件。"""
    event_id: str
    run_id: str
    kind: str  # stage_start | stage_end | var_write | device_event | hitl | risk | error | note
    ts: str
    elapsed_ms: float = 0.0
    stage_id: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StageTimeline:
    """单个阶段的时间线摘要。"""
    stage_id: str
    layer: str = ""
    status: str = "pending"  # pending | running | success | failed | skipped
    started_at: str = ""
    finished_at: str = ""
    elapsed_ms: float = 0.0
    ok_assert: Optional[bool] = None
    error: str = ""
    error_code: str = ""
    executor: str = ""
    steps_executed: int = 0
    device_results: List[Dict[str, Any]] = field(default_factory=list)
    extracted_vars: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = ""
    hitl_outcome: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TimelineTracker:
    """一次跨端执行的时间线追踪器。"""

    def __init__(self, run_id: str, plan_id: str = "", scenario: str = ""):
        self.run_id = run_id
        self.plan_id = plan_id
        self.scenario = scenario
        self.created_at = time.time()
        self.started_at: str = ""
        self.finished_at: str = ""
        self.status: str = "created"  # created | running | success | failed
        self.events: List[TimelineEvent] = []
        self.stages: Dict[str, StageTimeline] = {}
        self.variables: Dict[str, Any] = {}
        self._var_history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._sse_queues: List[Any] = []
        self._breakpoints: Dict[str, Dict[str, Any]] = {}  # stage_id -> config
        self._step_mode: bool = False  # 单步模式：每阶段暂停等待 resume
        self._paused_stage: Optional[str] = None
        self._pause_event = threading.Event()
        self._pause_event.set()  # 默认不暂停
        self._t0 = time.perf_counter()

    def _elapsed(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)

    def _emit(self, kind: str, detail: Dict[str, Any], stage_id: str = "") -> TimelineEvent:
        ev = TimelineEvent(
            event_id=f"te-{uuid.uuid4().hex[:10]}",
            run_id=self.run_id,
            kind=kind,
            ts=_utc_iso(),
            elapsed_ms=self._elapsed(),
            stage_id=stage_id,
            detail=detail,
        )
        with self._lock:
            self.events.append(ev)
        self._notify_sse(ev)
        return ev

    def _notify_sse(self, event: TimelineEvent) -> None:
        """推送 SSE 事件到所有监听队列。"""
        dead = []
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._lock:
            snapshot = list(self._sse_queues)
        for q in snapshot:
            try:
                q.append(payload)
            except Exception:
                dead.append(q)
        if dead:
            with self._lock:
                for d in dead:
                    try:
                        self._sse_queues.remove(d)
                    except Exception:
                        pass

    def add_sse_queue(self, q: Any) -> None:
        with self._lock:
            self._sse_queues.append(q)

    def remove_sse_queue(self, q: Any) -> None:
        with self._lock:
            try:
                self._sse_queues.remove(q)
            except Exception:
                pass

    # ---- 断点 & 单步 ----

    def set_breakpoint(self, stage_id: str, condition: str = "", enabled: bool = True) -> None:
        """设置断点。condition 为空则无条件暂停。"""
        self._breakpoints[stage_id] = {
            "stage_id": stage_id,
            "condition": condition,
            "enabled": enabled,
            "hit_count": 0,
        }
        self._emit("breakpoint_set", {"stage_id": stage_id, "condition": condition})

    def remove_breakpoint(self, stage_id: str) -> None:
        self._breakpoints.pop(stage_id, None)
        self._emit("breakpoint_removed", {"stage_id": stage_id})

    def clear_breakpoints(self) -> None:
        self._breakpoints.clear()
        self._emit("breakpoints_cleared", {})

    def get_breakpoints(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._breakpoints)

    def set_step_mode(self, enabled: bool) -> None:
        """启用/禁用单步模式。"""
        self._step_mode = enabled
        self._emit("step_mode_changed", {"enabled": enabled})
        if not enabled:
            self._pause_event.set()  # 释放所有暂停

    def check_breakpoint(self, stage_id: str) -> bool:
        """检查是否命中断点。返回 True 表示需要暂停。"""
        if self._step_mode:
            return True  # 单步模式下每阶段都暂停
        bp = self._breakpoints.get(stage_id)
        if not bp or not bp.get("enabled"):
            return False
        cond = bp.get("condition", "")
        if not cond:
            bp["hit_count"] = bp.get("hit_count", 0) + 1
            self._emit("breakpoint_hit", {"stage_id": stage_id, "hit_count": bp["hit_count"]})
            return True
        # 条件断点：检查变量
        try:
            val = self.variables.get(cond)
            if val is not None:
                bp["hit_count"] = bp.get("hit_count", 0) + 1
                self._emit("breakpoint_hit", {"stage_id": stage_id, "condition": cond, "value": str(val)[:200]})
                return True
        except Exception:
            pass
        return False

    def pause_at(self, stage_id: str) -> None:
        """在指定阶段暂停，等待 resume。"""
        self._paused_stage = stage_id
        self._pause_event.clear()
        self._emit("paused", {"stage_id": stage_id})
        self._pause_event.wait()  # 阻塞直到 resume
        self._paused_stage = None

    def resume(self) -> None:
        """恢复执行。"""
        self._pause_event.set()
        self._emit("resumed", {"stage_id": self._paused_stage or ""})

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def get_var_diff(self, stage_id: str) -> Dict[str, Any]:
        """获取阶段执行前后的变量差异。"""
        before_key = f"_vars_before_{stage_id}"
        with self._lock:
            before = {}
            for item in self._var_history:
                if isinstance(item, dict) and item.get("key") == before_key:
                    before = item.get("value", {})
                    if not isinstance(before, dict):
                        before = {}
                    break
            after = dict(self.variables)
        added = {k: after[k] for k in after if k not in before}
        changed = {k: {"before": before[k], "after": after[k]} for k in after if k in before and before[k] != after[k]}
        removed = {k: before[k] for k in before if k not in after}
        return {"added": added, "changed": changed, "removed": removed}

    # ---- 生命周期 ----

    def mark_start(self) -> None:
        self.started_at = _utc_iso()
        self.status = "running"
        self._emit("note", {"message": "跨端执行开始", "plan_id": self.plan_id, "scenario": self.scenario})

    def mark_finish(self, success: bool, error: str = "") -> None:
        self.finished_at = _utc_iso()
        self.status = "success" if success else "failed"
        self._emit("note", {
            "message": f"跨端执行{'成功' if success else '失败'}",
            "success": success,
            "error": error or "",
        })

    # ---- 阶段事件 ----

    def snapshot_vars_before(self, stage_id: str) -> None:
        """记录阶段执行前的变量快照（用于 var_diff）。"""
        with self._lock:
            self._var_history.append({
                "key": f"_vars_before_{stage_id}",
                "value": dict(self.variables),
                "source": "snapshot",
                "ts": _utc_iso(),
                "elapsed_ms": self._elapsed(),
            })

    def stage_start(self, stage_id: str, layer: str = "", executor: str = "") -> None:
        st = StageTimeline(
            stage_id=stage_id,
            layer=layer,
            status="running",
            started_at=_utc_iso(),
            executor=executor,
        )
        with self._lock:
            self.stages[stage_id] = st
        self._emit("stage_start", {
            "layer": layer,
            "executor": executor,
        }, stage_id=stage_id)

    def stage_end(
        self,
        stage_id: str,
        *,
        ok: bool,
        elapsed_ms: float = 0,
        error: str = "",
        error_code: str = "",
        steps_executed: int = 0,
        device_results: Optional[List[Dict[str, Any]]] = None,
        extracted: Optional[Dict[str, Any]] = None,
        risk_level: str = "",
        hitl_outcome: str = "",
    ) -> None:
        with self._lock:
            st = self.stages.get(stage_id)
            if st:
                st.status = "success" if ok else "failed"
                st.finished_at = _utc_iso()
                st.elapsed_ms = elapsed_ms
                st.ok_assert = ok
                st.error = error
                st.error_code = error_code
                st.steps_executed = steps_executed
                st.device_results = list(device_results or [])
                st.extracted_vars = dict(extracted or {})
                st.risk_level = risk_level
                st.hitl_outcome = hitl_outcome
        self._emit("stage_end", {
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "error": error,
            "error_code": error_code,
            "steps_executed": steps_executed,
            "device_count": len(device_results or []),
            "risk_level": risk_level,
            "hitl_outcome": hitl_outcome,
        }, stage_id=stage_id)

    # ---- 变量事件 ----

    def var_write(self, key: str, value: Any, source: str = "") -> None:
        """记录变量写入事件（敏感键自动脱敏）。"""
        import re
        sensitive = bool(re.search(r"(password|token|secret|api_key|cookie)", key, re.I))
        display_value = "***" if sensitive else str(value)[:200]
        with self._lock:
            self.variables[key] = display_value
            self._var_history.append({
                "key": key,
                "value": display_value,
                "source": source,
                "ts": _utc_iso(),
                "elapsed_ms": self._elapsed(),
            })
        self._emit("var_write", {
            "key": key,
            "value": display_value,
            "source": source,
            "sensitive": sensitive,
        })

    # ---- 设备事件 ----

    def device_event(self, device_udid: str, kind: str, detail: Dict[str, Any]) -> None:
        self._emit("device_event", {
            "device_udid": device_udid,
            "kind": kind,
            **detail,
        })

    # ---- HITL / Risk ----

    def hitl_event(self, gate_id: str, outcome: str, detail: Dict[str, Any]) -> None:
        self._emit("hitl", {
            "gate_id": gate_id,
            "outcome": outcome,
            **detail,
        })

    def risk_event(self, level: str, decision: str, detail: Dict[str, Any]) -> None:
        self._emit("risk", {
            "level": level,
            "decision": decision,
            **detail,
        })

    # ---- 查询 ----

    def to_dict(self) -> Dict[str, Any]:
        """导出完整时间线 JSON。"""
        with self._lock:
            return {
                "run_id": self.run_id,
                "plan_id": self.plan_id,
                "scenario": self.scenario,
                "status": self.status,
                "created_at": datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat(),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "total_elapsed_ms": self._elapsed(),
                "events": [e.to_dict() for e in self.events],
                "stages": {sid: st.to_dict() for sid, st in self.stages.items()},
                "variables": dict(self.variables),
                "var_history": list(self._var_history),
                "breakpoints": dict(self._breakpoints),
                "step_mode": self._step_mode,
                "paused_stage": self._paused_stage,
                "event_count": len(self.events),
                "stage_count": len(self.stages),
            }

    def get_stage(self, stage_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            st = self.stages.get(stage_id)
            return st.to_dict() if st else None

    def get_events_since(self, since_ms: float = 0) -> List[Dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self.events if e.elapsed_ms >= since_ms]


# ---- 全局管理 ----

def get_or_create_tracker(
    run_id: str,
    plan_id: str = "",
    scenario: str = "",
) -> TimelineTracker:
    """获取或创建时间线追踪器。"""
    with _LOCK:
        tracker = _TRACKERS.get(run_id)
        if tracker is None:
            tracker = TimelineTracker(run_id, plan_id=plan_id, scenario=scenario)
            _TRACKERS[run_id] = tracker
            # 清理过旧 tracker
            if len(_TRACKERS) > _MAX_TRACKERS:
                oldest = sorted(_TRACKERS.items(), key=lambda kv: kv[1].created_at)
                for rid, _ in oldest[: max(0, len(_TRACKERS) - _MAX_TRACKERS + 1)]:
                    _TRACKERS.pop(rid, None)
        return tracker


def get_tracker(run_id: str) -> Optional[TimelineTracker]:
    with _LOCK:
        return _TRACKERS.get(run_id)


def list_trackers(status: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    """列出活跃 tracker 摘要。"""
    with _LOCK:
        trackers = list(_TRACKERS.values())
    if status:
        trackers = [t for t in trackers if t.status == status]
    trackers.sort(key=lambda t: t.created_at, reverse=True)
    return [{
        "run_id": t.run_id,
        "plan_id": t.plan_id,
        "scenario": t.scenario,
        "status": t.status,
        "created_at": datetime.fromtimestamp(t.created_at, tz=timezone.utc).isoformat(),
        "stage_count": len(t.stages),
        "event_count": len(t.events),
    } for t in trackers[:limit]]


def remove_tracker(run_id: str) -> bool:
    with _LOCK:
        return _TRACKERS.pop(run_id, None) is not None
