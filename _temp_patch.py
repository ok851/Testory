# -*- coding: utf-8 -*-
"""Temporary patch script for mobile_sync_store.py"""
import re

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_sync_store.py"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add get_run_job_status_lite after get_run_job
old1 = (
    'def get_run_job(job_id: str) -> Optional[Dict[str, Any]]:\n'
    '    _load_jobs_from_disk(force=True)\n'
    '    with _LOCK:\n'
    '        job = _RUN_JOBS.get(job_id)\n'
    '        return dict(job) if job else None\n'
    '\n'
    '\n'
    'def cancel_run_job'
)
new1 = (
    'def get_run_job(job_id: str) -> Optional[Dict[str, Any]]:\n'
    '    _load_jobs_from_disk(force=True)\n'
    '    with _LOCK:\n'
    '        job = _RUN_JOBS.get(job_id)\n'
    '        return dict(job) if job else None\n'
    '\n'
    '\n'
    'def get_run_job_status_lite(job_id: str) -> Optional[Dict[str, Any]]:\n'
    '    """轻量级状态查询：手机回放中轮询，仅返回 status / error / error_code / abort_reason。"""\n'
    '    _load_jobs_from_disk(force=True)\n'
    '    with _LOCK:\n'
    '        job = _RUN_JOBS.get(job_id)\n'
    '        if not job:\n'
    '            return None\n'
    '        return {\n'
    '            "job_id": job_id,\n'
    '            "status": str(job.get("status") or "").strip().lower(),\n'
    '            "error": job.get("error") or "",\n'
    '            "error_code": job.get("error_code") or "",\n'
    '            "abort_reason": job.get("abort_reason") or "",\n'
    '        }\n'
    '\n'
    '\n'
    'def cancel_run_job'
)
assert old1 in content, "old1 not found"
content = content.replace(old1, new1, 1)

# 2. Enhance cancel_run_job
old2 = (
    'def cancel_run_job(job_id: str, *, error: str = "", error_code: str = "MOBILE_JOB_CANCELLED") -> bool:\n'
    '    """将 pending/running job 标为 cancelled（任务中止时避免手机稍后误执行）。"""\n'
    '    _load_jobs_from_disk(force=True)\n'
    '    with _LOCK:\n'
    '        job = _RUN_JOBS.get(job_id)\n'
    '        if not job:\n'
    '            return False\n'
    '        st = str(job.get("status") or "").strip().lower()\n'
    '        if st in ("success", "error", "failed", "cancelled", "ok"):\n'
    '            return False\n'
    '        job["status"] = "cancelled"\n'
    '        job["error"] = (error or "").strip() or "任务已取消"\n'
    '        job["error_code"] = error_code\n'
    '        job["finished_at"] = time.time()\n'
    '        _persist_jobs_unlocked()\n'
    '        return True'
)
new2 = (
    'def cancel_run_job(\n'
    '    job_id: str,\n'
    '    *,\n'
    '    error: str = "",\n'
    '    error_code: str = "MOBILE_JOB_CANCELLED",\n'
    '    abort_reason: str = "",\n'
    ') -> bool:\n'
    '    """将 pending/running job 标为 cancelled（任务中止时避免手机稍后误执行）。\n'
    '\n'
    '    abort_reason: 给手机端看的取消原因（如 user_pause / timeout）。\n'
    '    """\n'
    '    _load_jobs_from_disk(force=True)\n'
    '    with _LOCK:\n'
    '        job = _RUN_JOBS.get(job_id)\n'
    '        if not job:\n'
    '            return False\n'
    '        st = str(job.get("status") or "").strip().lower()\n'
    '        if st in ("success", "error", "failed", "cancelled", "ok"):\n'
    '            return False\n'
    '        job["status"] = "cancelled"\n'
    '        job["error"] = (error or "").strip() or "任务已取消"\n'
    '        job["error_code"] = error_code\n'
    '        if abort_reason:\n'
    '            job["abort_reason"] = abort_reason\n'
    '        job["finished_at"] = time.time()\n'
    '        _persist_jobs_unlocked()\n'
    '        return True'
)
assert old2 in content, "old2 not found"
content = content.replace(old2, new2, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: mobile_sync_store.py updated")
