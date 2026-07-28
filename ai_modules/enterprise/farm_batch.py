# -*- coding: utf-8 -*-
"""农场 fan-out 批次：对多节点并行探测（顺序执行、结果聚合）。

诚实约束：
- 批次完成 ≠ 业务用例并行回归已通过
- ``all_nodes_reachable`` 仅描述探测，``parallel_suite_pass_claimed`` 恒 false
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List

from .farm_jobs import enqueue_job, get_job
from .execution_farm import list_nodes


_DISCLAIMER = (
    "fan-out 批次仅聚合节点探测/健康作业；"
    "不得把 all_nodes_reachable 或 batch succeeded 写成并行用例套件通过。"
)


def run_probe_fanout(*, auto_run: bool = True) -> Dict[str, Any]:
    """为每个已登记节点入队 probe；默认立即执行并汇总。"""
    nodes = list_nodes()
    batch_id = f"batch-{uuid.uuid4().hex[:10]}"
    if not nodes:
        return {
            "ok": False,
            "batch_id": batch_id,
            "error_code": "NO_NODES",
            "error": "无已登记节点，无法 fan-out",
            "jobs": [],
            "all_nodes_reachable": False,
            "case_pass_claimed": False,
            "parallel_suite_pass_claimed": False,
            "disclaimer": _DISCLAIMER,
        }

    t0 = time.perf_counter()
    jobs: List[Dict[str, Any]] = []
    for n in nodes:
        nid = str(n.get("node_id") or "")
        r = enqueue_job(
            job_type="probe",
            node_id=nid,
            payload={"batch_id": batch_id, "fanout": True},
            auto_run=bool(auto_run),
        )
        job = r.get("job") if isinstance(r.get("job"), dict) else get_job(
            str((r.get("job") or {}).get("job_id") or "")
        )
        if job:
            jobs.append(job)
        elif r.get("ok") is False:
            jobs.append(
                {
                    "job_id": None,
                    "node_id": nid,
                    "status": "failed",
                    "error_code": r.get("error_code"),
                    "error": r.get("error"),
                }
            )

    succeeded = [j for j in jobs if j.get("status") == "succeeded"]
    failed = [j for j in jobs if j.get("status") == "failed"]
    queued = [j for j in jobs if j.get("status") in ("queued", "running")]
    all_reachable = bool(jobs) and len(succeeded) == len(jobs) and not queued
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    try:
        from .sla_evidence import record_metric

        record_metric(
            kind="farm_probe_fanout",
            ok=all_reachable,
            latency_ms=elapsed_ms,
            meta={"batch_id": batch_id, "node_count": len(nodes)},
        )
    except Exception:
        pass

    return {
        "ok": True,  # 批次编排完成（非业务绿）
        "batch_id": batch_id,
        "node_count": len(nodes),
        "job_count": len(jobs),
        "succeeded_count": len(succeeded),
        "failed_count": len(failed),
        "queued_count": len(queued),
        "jobs": jobs,
        "all_nodes_reachable": all_reachable,
        "elapsed_ms": elapsed_ms,
        "case_pass_claimed": False,
        "parallel_suite_pass_claimed": False,
        "disclaimer": _DISCLAIMER,
    }
