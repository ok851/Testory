# -*- coding: utf-8 -*-
"""异步 CodeChange 任务持久化（data/ci_code_change/）。"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_CACHE: Dict[str, Dict[str, Any]] = {}


def _root() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        base = Path(env).expanduser().resolve()
    else:
        base = Path(__file__).resolve().parents[2] / "data"
    d = base / "ci_code_change"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(task_id: str) -> Path:
    safe = "".join(c for c in str(task_id) if c.isalnum() or c in "-_")[:64] or "unknown"
    return _root() / f"{safe}.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_task_id() -> str:
    return f"cc-{uuid.uuid4().hex[:16]}"


def save_task(rec: Dict[str, Any]) -> Dict[str, Any]:
    tid = str(rec.get("task_id") or "")
    rec["updated_at"] = _now()
    with _LOCK:
        _CACHE[tid] = dict(rec)
        try:
            _path(tid).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return dict(rec)


def update_task(task_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    rec = get_task(task_id)
    if not rec:
        return None
    rec.update(fields)
    return save_task(rec)


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    tid = (task_id or "").strip()
    if not tid:
        return None
    with _LOCK:
        if tid in _CACHE:
            return dict(_CACHE[tid])
    p = _path(tid)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        with _LOCK:
            _CACHE[tid] = data
        return dict(data)
    return None


def find_by_git_sha(git_sha: str, project_id: Any = None) -> Optional[Dict[str, Any]]:
    """幂等：同 git_sha（可选同 project）返回已有任务。"""
    sha = (git_sha or "").strip().lower()
    if not sha or sha in ("unknown", "none", "null"):
        return None
    for rec in list_tasks(limit=200):
        existing = str(rec.get("git_sha") or "").strip().lower()
        if existing != sha:
            continue
        if project_id is not None and str(rec.get("project_id") or "") != str(project_id):
            continue
        return dict(rec)
    return None


def list_tasks(limit: int = 30, *, tenant_id: Any = None) -> List[Dict[str, Any]]:
    lim = max(1, min(int(limit or 30), 500))
    by_id: Dict[str, Dict[str, Any]] = {}
    root = _root()
    # 仅合并仍落在当前 data root 下的缓存，避免跨 UAT_DATA_DIR 污染
    with _LOCK:
        stale = []
        for k, v in _CACHE.items():
            p = _path(k)
            try:
                if p.is_file() and root in p.parents:
                    by_id[k] = dict(v)
                else:
                    stale.append(k)
            except Exception:
                stale.append(k)
        for k in stale:
            _CACHE.pop(k, None)
    try:
        for p in root.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("task_id"):
                by_id[str(data["task_id"])] = data
    except OSError:
        pass
    rows = list(by_id.values())
    if tenant_id is not None:
        rows = [r for r in rows if str(r.get("tenant_id") or "") == str(tenant_id)]
    rows = sorted(rows, key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows[:lim]


def find_by_ci_run_id(ci_run_id: str) -> Optional[Dict[str, Any]]:
    rid = (ci_run_id or "").strip()
    if not rid:
        return None
    for rec in list_tasks(limit=300):
        if str(rec.get("ci_run_id") or "") == rid:
            return dict(rec)
    return None


def find_recent_duplicate(
    *,
    git_sha: str = "",
    project_id: Any = None,
    mr_key: str = "",
    window_minutes: int = 15,
) -> Optional[Dict[str, Any]]:
    """同 git_sha 或同 mr_key 在时间窗内去重。"""
    sha = (git_sha or "").strip().lower()
    mk = (mr_key or "").strip().lower()
    hit = find_by_git_sha(sha, project_id=project_id) if sha else None
    if hit:
        return hit
    if not mk or window_minutes <= 0:
        return None
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    for rec in list_tasks(limit=100):
        if project_id is not None and str(rec.get("project_id") or "") != str(project_id):
            continue
        if str(rec.get("mr_key") or "").strip().lower() != mk:
            continue
        ts = str(rec.get("created_at") or "").replace("Z", "")
        try:
            created = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        if (now - created).total_seconds() <= window_minutes * 60:
            return dict(rec)
    return None


def cleanup_expired_tasks(ttl_days: Optional[int] = None) -> Dict[str, Any]:
    """删除超过 TTL 的任务文件。"""
    from ai_modules.code_intel.policy import task_ttl_days
    import datetime

    days = int(ttl_days if ttl_days is not None else task_ttl_days())
    cutoff = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=days)
    removed = 0
    kept = 0
    errors = 0
    for rec in list_tasks(limit=500):
        tid = str(rec.get("task_id") or "")
        ts = str(rec.get("updated_at") or rec.get("created_at") or "").replace("Z", "")
        try:
            updated = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            kept += 1
            continue
        if updated >= cutoff:
            kept += 1
            continue
        try:
            p = _path(tid)
            with _LOCK:
                _CACHE.pop(tid, None)
                if p.is_file():
                    p.unlink()
            removed += 1
        except OSError:
            errors += 1
    return {"removed": removed, "kept": kept, "errors": errors, "ttl_days": days}


def create_queued_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    tid = new_task_id()
    rec = {
        "task_id": tid,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "poll_url": f"/api/ci/code-change/{tid}",
        "project_id": payload.get("project_id"),
        "tenant_id": payload.get("tenant_id"),
        "repo": payload.get("repo") or "",
        "branch": payload.get("branch") or "",
        "git_sha": payload.get("git_sha") or "",
        "mr_key": payload.get("mr_key") or "",
        "mr_description": (payload.get("mr_description") or "")[:8000],
        "build_id": payload.get("build_id") or "",
        "trigger_source": payload.get("trigger_source") or "ci",
        "analyze_only": bool(payload.get("analyze_only", True)),
        "generate_drafts": bool(payload.get("generate_drafts", False)),
        "trigger_run": bool(payload.get("trigger_run", False)),
        "callback_url": (payload.get("callback_url") or "").strip(),
        "changed_files": payload.get("changed_files") or [],
        "diff": (payload.get("diff") or "")[:200_000],
        "file_snippets": payload.get("file_snippets") or {},
        "impact": None,
        "recommended_case_ids": [],
        "at_risk_case_ids": [],
        "draft_case_ids": [],
        "draft_preview": [],
        "ci_run_id": None,
        "heal_proposals": [],
        "rollback_hint": None,
        "warnings": [],
        "error": None,
    }
    return save_task(rec)
