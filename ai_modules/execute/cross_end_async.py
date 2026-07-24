# -*- coding: utf-8 -*-
"""跨端执行异步队列（企业运营：HITL 等待时 UI 仍可 resume）。

诚实约束：
- 终态 success 仅来自 execute_cross_end_plan 真实结果
- 忙锁 / 不可用锁与同步路径同一语义
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_RUNS: Dict[str, Dict[str, Any]] = {}
_MAX_RUNS = 200


def _utc() -> float:
    return time.time()


def create_run(
    plan: Dict[str, Any],
    *,
    user_id: str = "",
    project_id: Any = None,
    trigger_source: str = "ui-async",
) -> Dict[str, Any]:
    run_id = f"cer-{uuid.uuid4().hex[:12]}"
    rec = {
        "run_id": run_id,
        "status": "queued",
        "created_at": _utc(),
        "updated_at": _utc(),
        "user_id": str(user_id or ""),
        "project_id": project_id,
        "plan_id": (plan or {}).get("plan_id"),
        "scenario": (plan or {}).get("scenario") or (plan or {}).get("name") or "",
        "result": None,
        "error": None,
        "error_code": None,
        "user_hint": None,
    }
    with _LOCK:
        _RUNS[run_id] = rec
        if len(_RUNS) > _MAX_RUNS:
            # 丢弃最旧
            oldest = sorted(_RUNS.items(), key=lambda kv: kv[1].get("created_at") or 0)
            for rid, _ in oldest[: max(0, len(_RUNS) - _MAX_RUNS)]:
                _RUNS.pop(rid, None)
    return dict(rec)


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        rec = _RUNS.get(str(run_id or "").strip())
        return dict(rec) if rec else None


def _patch(run_id: str, **fields: Any) -> None:
    with _LOCK:
        rec = _RUNS.get(run_id)
        if not rec:
            return
        rec.update(fields)
        rec["updated_at"] = _utc()


def start_run_thread(
    run_id: str,
    plan: Dict[str, Any],
    *,
    user_id: str = "",
    project_id: Any = None,
    trigger_source: str = "ui-async",
) -> None:
    def _worker() -> None:
        _patch(run_id, status="running")
        try:
            from ai_modules.execute.orchestrator import execute_cross_end_plan
            from ai_modules.plan.user_facing_errors import enrich_result_with_user_hint

            result = execute_cross_end_plan(
                plan,
                user_id=user_id,
                project_id=project_id,
                trigger_source=trigger_source,
            )
            try:
                enrich_result_with_user_hint(result)
            except Exception:
                pass
            status = "success" if result.get("success") is True else "failed"
            if result.get("lock") == "busy":
                status = "failed"
            if result.get("lock") == "unavailable":
                status = "failed"
            _patch(
                run_id,
                status=status,
                result=result,
                error=result.get("error"),
                error_code=result.get("error_code"),
                user_hint=result.get("user_hint"),
            )
        except Exception as exc:
            _patch(
                run_id,
                status="failed",
                error=str(exc),
                error_code="CROSS_END_ASYNC_ERROR",
                result={"success": False, "error": str(exc), "error_code": "CROSS_END_ASYNC_ERROR"},
            )

    t = threading.Thread(target=_worker, name=f"cross-end-{run_id}", daemon=True)
    t.start()


def reset_cross_end_async_for_tests() -> None:
    with _LOCK:
        _RUNS.clear()


def list_ops_gates(*, user_id: str = "") -> Dict[str, Any]:
    """运营面板：HITL waiting + RiskGuard pending。"""
    from agent_hitl import list_hitl_gates
    from ai_modules.security.risk_guard import list_pending_approvals

    hitl = list_hitl_gates(status="waiting")
    if user_id:
        uid = str(user_id)
        hitl = [
            g for g in hitl
            if not g.get("user_id") or str(g.get("user_id")) == uid
        ]
    risk = list_pending_approvals()
    return {
        "hitl_waiting": hitl,
        "risk_pending": risk,
        "hitl_count": len(hitl),
        "risk_count": len(risk),
    }
