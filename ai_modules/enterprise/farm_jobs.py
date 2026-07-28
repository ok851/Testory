# -*- coding: utf-8 -*-
"""执行农场任务队列（企业雏形）。

诚实约束：
- 入队 ≠ 已并行执行成功
- 仅 ``probe`` / ``live_health`` 类任务会实际调用探测；结果如实写入
- 未知 job_type 不得记为 success
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _root() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        base = Path(env).expanduser().resolve()
    else:
        base = Path(__file__).resolve().parents[2] / "data"
    d = base / "execution_farm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jobs_store() -> Path:
    return _root() / "jobs.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load() -> List[Dict[str, Any]]:
    path = _jobs_store()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    return [j for j in (jobs or []) if isinstance(j, dict)]


def _save(jobs: List[Dict[str, Any]]) -> None:
    _jobs_store().write_text(
        json.dumps({"jobs": jobs, "updated_at": _now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_DISCLAIMER = (
    "任务入队/完成状态仅描述农场作业本身；"
    "不得把 queued/running 或 probe 成功写成业务用例/并行回归已通过。"
)


def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    jobs = sorted(_load(), key=lambda j: str(j.get("created_at") or ""), reverse=True)
    return jobs[: max(1, min(int(limit or 50), 200))]


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    jid = (job_id or "").strip()
    for j in _load():
        if j.get("job_id") == jid:
            return dict(j)
    return None


def enqueue_job(
    *,
    job_type: str,
    node_id: str = "",
    payload: Optional[Dict[str, Any]] = None,
    auto_run: bool = False,
) -> Dict[str, Any]:
    """登记任务。``auto_run=True`` 时立即执行可安全跑的类型（probe/live_health）。"""
    jt = (job_type or "").strip().lower()
    if not jt:
        return {
            "ok": False,
            "error_code": "JOB_TYPE_REQUIRED",
            "error": "job_type 不能为空",
            "disclaimer": _DISCLAIMER,
        }
    if jt not in ("probe", "live_health", "dispatch_hint", "noop"):
        return {
            "ok": False,
            "error_code": "JOB_TYPE_UNSUPPORTED",
            "error": f"不支持的 job_type={jt!r}（雏形仅 probe/live_health/dispatch_hint/noop）",
            "disclaimer": _DISCLAIMER,
        }

    job = {
        "job_id": f"job-{uuid.uuid4().hex[:12]}",
        "job_type": jt,
        "node_id": (node_id or "").strip() or None,
        "payload": dict(payload or {}),
        "status": "queued",  # queued|running|succeeded|failed|cancelled
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
        "error_code": None,
        "disclaimer": _DISCLAIMER,
    }
    jobs = _load()
    jobs.append(job)
    _save(jobs)

    if auto_run:
        return run_job(job["job_id"])
    return {"ok": True, "job": job, "disclaimer": _DISCLAIMER}


def cancel_job(job_id: str) -> Dict[str, Any]:
    jobs = _load()
    for j in jobs:
        if j.get("job_id") == job_id:
            if j.get("status") in ("succeeded", "failed", "cancelled"):
                return {
                    "ok": False,
                    "error_code": "JOB_ALREADY_FINISHED",
                    "error": f"任务已结束 status={j.get('status')}",
                    "job": j,
                    "disclaimer": _DISCLAIMER,
                }
            j["status"] = "cancelled"
            j["finished_at"] = _now()
            j["error_code"] = "JOB_CANCELLED"
            _save(jobs)
            return {"ok": True, "job": j, "disclaimer": _DISCLAIMER}
    return {
        "ok": False,
        "error_code": "JOB_NOT_FOUND",
        "error": "任务不存在",
        "disclaimer": _DISCLAIMER,
    }


def run_job(job_id: str) -> Dict[str, Any]:
    """执行队列中的作业；失败如实标记 failed。"""
    jobs = _load()
    target = None
    for j in jobs:
        if j.get("job_id") == job_id:
            target = j
            break
    if not target:
        return {
            "ok": False,
            "error_code": "JOB_NOT_FOUND",
            "error": "任务不存在",
            "disclaimer": _DISCLAIMER,
        }
    if target.get("status") in ("succeeded", "failed", "cancelled"):
        return {
            "ok": False,
            "error_code": "JOB_ALREADY_FINISHED",
            "error": f"任务已结束 status={target.get('status')}",
            "job": target,
            "disclaimer": _DISCLAIMER,
        }

    target["status"] = "running"
    target["started_at"] = _now()
    _save(jobs)

    jt = str(target.get("job_type") or "")
    result: Dict[str, Any] = {}
    ok = False
    err = None
    err_code = None
    t0 = time.perf_counter()

    try:
        if jt == "noop":
            result = {"note": "noop 仅验证队列，未调度远程执行"}
            ok = True
        elif jt == "probe":
            from ai_modules.enterprise.execution_farm import probe_node, select_preferred_node

            nid = target.get("node_id") or ""
            if not nid:
                pref = select_preferred_node(require_online=False)
                nid = (pref or {}).get("node_id") or ""
                target["node_id"] = nid or None
            if not nid:
                ok = False
                err_code = "NO_NODE"
                err = "无可用节点可探测"
            else:
                result = probe_node(str(nid))
                ok = bool(result.get("ok"))
                if not ok:
                    err_code = result.get("error_code") or "PROBE_FAILED"
                    err = result.get("error") or "探测失败"
        elif jt == "live_health":
            from testory_mcp.gateway_live import probe_gateway_health

            result = probe_gateway_health()
            ok = bool(result.get("ok"))
            if not ok:
                err_code = result.get("error_code") or "HEALTH_FAILED"
                err = result.get("error") or "health 失败"
        elif jt == "dispatch_hint":
            from ai_modules.enterprise.execution_farm import dispatch_hint

            result = dispatch_hint(node_id=str(target.get("node_id") or ""))
            # hint 生成成功不算业务绿；作业本身以 hint.ok 为准
            ok = bool(result.get("ok"))
            if not ok:
                err_code = result.get("error_code") or "HINT_FAILED"
                err = result.get("error") or "无法生成调度建议"
        else:
            ok = False
            err_code = "JOB_TYPE_UNSUPPORTED"
            err = f"不支持 {jt}"
    except Exception as e:
        ok = False
        err_code = "JOB_EXCEPTION"
        err = str(e)[:300]

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    try:
        from ai_modules.enterprise.sla_evidence import record_metric

        record_metric(
            kind=f"farm_job_{jt}",
            ok=ok,
            latency_ms=elapsed_ms,
            meta={"job_id": job_id, "node_id": target.get("node_id")},
        )
    except Exception:
        pass

    target["finished_at"] = _now()
    target["result"] = result
    target["latency_ms"] = elapsed_ms
    if ok:
        target["status"] = "succeeded"
        target["error"] = None
        target["error_code"] = None
    else:
        target["status"] = "failed"
        target["error"] = err
        target["error_code"] = err_code
    _save(jobs)

    return {
        "ok": ok,
        "job": target,
        "disclaimer": _DISCLAIMER,
        # 显式区分：作业成功 ≠ 业务用例通过
        "case_pass_claimed": False,
    }


def jobs_summary() -> Dict[str, Any]:
    jobs = _load()
    counts = {
        "queued": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for j in jobs:
        st = str(j.get("status") or "")
        if st in counts:
            counts[st] += 1
    return {
        "job_count": len(jobs),
        "counts": counts,
        "recent": list_jobs(limit=10),
        "disclaimer": _DISCLAIMER,
    }
