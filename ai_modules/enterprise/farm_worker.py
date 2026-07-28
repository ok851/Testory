# -*- coding: utf-8 -*-
"""农场队列 Worker：消化 queued 作业（同步批处理雏形）。

诚实约束：
- Worker 跑完 ≠ 业务用例并行通过
- ``jobs_succeeded`` 仅计农场作业；``case_pass_claimed`` / ``parallel_suite_pass_claimed`` 恒 false
- 非常驻守护进程；由 API / CLI 触发一批
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from .farm_jobs import get_job, list_jobs, run_job


_DISCLAIMER = (
    "Worker 仅处理农场队列中的 probe/live_health 等作业；"
    "不得把 drain 成功写成跨端用例或并行回归已通过。"
)


def list_queued_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    queued = [j for j in list_jobs(limit=200) if j.get("status") == "queued"]
    # 按创建时间升序先入先出
    queued = sorted(queued, key=lambda j: str(j.get("created_at") or ""))
    return queued[: max(1, min(int(limit or 50), 100))]


def drain_queued_jobs(*, limit: int = 20) -> Dict[str, Any]:
    """同步执行最多 ``limit`` 条 queued 任务。"""
    t0 = time.perf_counter()
    selected = list_queued_jobs(limit=limit)
    results: List[Dict[str, Any]] = []
    succeeded = 0
    failed = 0

    if not selected:
        return {
            "ok": True,
            "drained": 0,
            "succeeded": 0,
            "failed": 0,
            "results": [],
            "elapsed_ms": 0,
            "case_pass_claimed": False,
            "parallel_suite_pass_claimed": False,
            "disclaimer": _DISCLAIMER,
            "note": "队列为空",
        }

    for job in selected:
        jid = str(job.get("job_id") or "")
        if not jid:
            continue
        # 再次确认仍为 queued（避免并发重复）
        fresh = get_job(jid)
        if not fresh or fresh.get("status") != "queued":
            continue
        r = run_job(jid)
        results.append(
            {
                "job_id": jid,
                "ok": bool(r.get("ok")),
                "status": (r.get("job") or {}).get("status"),
                "error_code": r.get("error_code") or (r.get("job") or {}).get("error_code"),
            }
        )
        if r.get("ok"):
            succeeded += 1
        else:
            failed += 1

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    try:
        from .sla_evidence import record_metric

        record_metric(
            kind="farm_worker_drain",
            ok=failed == 0 and succeeded > 0,
            latency_ms=elapsed_ms,
            meta={"drained": len(results), "succeeded": succeeded, "failed": failed},
        )
    except Exception:
        pass

    return {
        "ok": True,  # Worker 本身完成一轮
        "drained": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
        "elapsed_ms": elapsed_ms,
        "case_pass_claimed": False,
        "parallel_suite_pass_claimed": False,
        "disclaimer": _DISCLAIMER,
    }
