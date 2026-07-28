# -*- coding: utf-8 -*-
"""Testory ↔ Jenkins 统一门禁同步。

策略 ``both_must_pass``（默认）：两侧均终态后，仅当 Testory gate_passed 且
Jenkins result==SUCCESS 时 ``unified_gate_passed=true``。

诚实：任一侧失败 → 统一门禁红；缺一侧未完成 → status=running，不提前假绿。
"""

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
_POLLERS: Dict[str, threading.Thread] = {}


def _root() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        base = Path(env).expanduser().resolve()
    else:
        base = Path(__file__).resolve().parents[2] / "data"
    d = base / "ci_sync"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(sync_id: str) -> Path:
    safe = "".join(c for c in str(sync_id) if c.isalnum() or c in "-_")[:64] or "unknown"
    return _root() / f"{safe}.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _save(rec: Dict[str, Any]) -> Dict[str, Any]:
    sid = str(rec.get("sync_id") or "")
    rec["updated_at"] = _now()
    with _LOCK:
        _CACHE[sid] = dict(rec)
        try:
            _path(sid).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return dict(rec)


def get_sync(sync_id: str) -> Optional[Dict[str, Any]]:
    sid = (sync_id or "").strip()
    if not sid:
        return None
    with _LOCK:
        if sid in _CACHE:
            return dict(_CACHE[sid])
    p = _path(sid)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        with _LOCK:
            _CACHE[sid] = data
        return dict(data)
    return None


def list_syncs(limit: int = 30) -> List[Dict[str, Any]]:
    lim = max(1, min(int(limit or 30), 100))
    by_id: Dict[str, Dict[str, Any]] = {}
    with _LOCK:
        by_id.update({k: dict(v) for k, v in _CACHE.items()})
    try:
        for p in _root().glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("sync_id"):
                by_id[str(data["sync_id"])] = data
    except OSError:
        pass
    rows = sorted(by_id.values(), key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows[:lim]


def _jenkins_pass(result: Optional[str]) -> Optional[bool]:
    if result is None:
        return None
    r = str(result).strip().upper()
    if not r:
        return None
    if r == "SUCCESS":
        return True
    # UNSTABLE / FAILURE / ABORTED / null → 门禁红
    return False


def recompute_unified(rec: Dict[str, Any]) -> Dict[str, Any]:
    policy = str(rec.get("policy") or "both_must_pass")
    t = rec.get("testory") if isinstance(rec.get("testory"), dict) else {}
    j = rec.get("jenkins") if isinstance(rec.get("jenkins"), dict) else {}

    t_terminal = bool(t.get("terminal"))
    j_terminal = bool(j.get("terminal"))
    t_ok = t.get("gate_passed") if t_terminal else None
    j_ok = _jenkins_pass(j.get("result")) if j_terminal else None

    unified = None
    status = "running"
    if policy == "both_must_pass":
        if t_terminal and j_terminal:
            unified = bool(t_ok) and bool(j_ok)
            status = "success" if unified else "failed"
        elif t_terminal and not j.get("job_name") and not j.get("queue_url") and not j.get("build_url"):
            # 仅 Testory 一侧
            unified = bool(t_ok)
            status = "success" if unified else "failed"
        elif j_terminal and not t.get("run_id"):
            unified = bool(j_ok)
            status = "success" if unified else "failed"
        else:
            status = "running"
            unified = False  # 未齐两侧，不得宣称通过
    else:
        # either_pass：任一侧成功且两侧都终态（或仅一侧）
        if t_terminal or j_terminal:
            parts = []
            if t_terminal:
                parts.append(bool(t_ok))
            if j_terminal:
                parts.append(bool(j_ok))
            if (t_terminal or not t.get("run_id")) and (j_terminal or not j.get("job_name")):
                unified = any(parts) if parts else False
                status = "success" if unified else "failed"
            else:
                status = "running"
                unified = False

    rec["unified_gate_passed"] = bool(unified) if status in ("success", "failed") else False
    rec["status"] = status
    rec["sides"] = {
        "testory_terminal": t_terminal,
        "jenkins_terminal": j_terminal,
        "testory_ok": t_ok,
        "jenkins_ok": j_ok,
    }
    rec["disclaimer"] = (
        "unified_gate_passed 仅在策略要求的两侧均终态后才可能为 true；"
        "任一侧失败则为 false。触发 Jenkins 受理 ≠ 已通过。"
    )
    return rec


def create_sync(
    *,
    policy: str = "both_must_pass",
    testory_run_id: str = "",
    jenkins_job: str = "",
    label: str = "",
) -> Dict[str, Any]:
    sid = f"sync-{uuid.uuid4().hex[:12]}"
    rec = {
        "sync_id": sid,
        "policy": policy if policy in ("both_must_pass", "either_pass") else "both_must_pass",
        "label": (label or "").strip(),
        "status": "running",
        "unified_gate_passed": False,
        "created_at": _now(),
        "testory": {
            "run_id": (testory_run_id or "").strip() or None,
            "status": None,
            "gate_passed": None,
            "terminal": False,
            "poll_url": None,
        },
        "jenkins": {
            "job_name": (jenkins_job or "").strip() or None,
            "queue_url": None,
            "build_url": None,
            "build_number": None,
            "result": None,
            "building": None,
            "terminal": False,
        },
        "poll_url": f"/api/ci/sync/{sid}",
    }
    return _save(recompute_unified(rec))


def bind_testory_run(sync_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    from ci_adapter import get_run, is_terminal_status

    rec = get_sync(sync_id)
    if not rec:
        return None
    rid = (run_id or "").strip()
    t = dict(rec.get("testory") or {})
    t["run_id"] = rid
    t["poll_url"] = f"/api/ci/runs/{rid}"
    run = get_run(rid)
    if run:
        t["status"] = run.get("status")
        t["gate_passed"] = bool(run.get("gate_passed"))
        t["terminal"] = is_terminal_status(run.get("status"))
    rec["testory"] = t
    # 反向写 sync_id 到 ci run
    try:
        from ci_adapter import update_run_fields

        update_run_fields(rid, sync_id=sync_id, sync_poll_url=f"/api/ci/sync/{sync_id}")
    except Exception:
        pass
    return _save(recompute_unified(rec))


def bind_jenkins_trigger(
    sync_id: str,
    *,
    job_name: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    from ai_modules.enterprise.jenkins_trigger import trigger_jenkins_job

    rec = get_sync(sync_id)
    if not rec:
        return None
    # 把 sync_id 传给 Jenkins 便于流水线回调
    params = dict(parameters or {})
    params.setdefault("TESTORY_SYNC_ID", sync_id)
    if rec.get("testory", {}).get("run_id"):
        params.setdefault("TESTORY_RUN_ID", rec["testory"]["run_id"])
    trig = trigger_jenkins_job(job_name=job_name, parameters=params)
    j = dict(rec.get("jenkins") or {})
    j["job_name"] = job_name
    j["trigger"] = {
        "ok": trig.get("ok"),
        "status_code": trig.get("status_code"),
        "error": trig.get("error"),
    }
    j["queue_url"] = trig.get("queue_url")
    j["terminal"] = False
    j["result"] = None
    rec["jenkins"] = j
    if not trig.get("ok"):
        j["terminal"] = True
        j["result"] = "FAILURE"
        rec["jenkins"] = j
        rec["error"] = trig.get("error")
        rec["error_code"] = trig.get("error_code")
    saved = _save(recompute_unified(rec))
    if trig.get("ok"):
        _ensure_poller(sync_id)
    return saved


def refresh_jenkins_side(rec: Dict[str, Any]) -> Dict[str, Any]:
    from ai_modules.enterprise.jenkins_trigger import resolve_jenkins_build_status

    j = dict(rec.get("jenkins") or {})
    if j.get("terminal"):
        return rec
    st = resolve_jenkins_build_status(
        queue_url=j.get("queue_url") or "",
        build_url=j.get("build_url") or "",
    )
    if st.get("build_url"):
        j["build_url"] = st["build_url"]
    if st.get("build_number") is not None:
        j["build_number"] = st["build_number"]
    if st.get("building") is not None:
        j["building"] = st["building"]
    if st.get("result"):
        j["result"] = st["result"]
    if st.get("terminal"):
        j["terminal"] = True
    if st.get("error") and not j.get("queue_url") and not j.get("build_url"):
        j["terminal"] = True
        j["result"] = j.get("result") or "FAILURE"
        j["error"] = st.get("error")
    rec["jenkins"] = j
    return rec


def refresh_testory_side(rec: Dict[str, Any]) -> Dict[str, Any]:
    from ci_adapter import get_run, is_terminal_status

    t = dict(rec.get("testory") or {})
    rid = t.get("run_id")
    if not rid:
        return rec
    run = get_run(str(rid))
    if not run:
        return rec
    t["status"] = run.get("status")
    t["gate_passed"] = bool(run.get("gate_passed"))
    t["terminal"] = is_terminal_status(run.get("status"))
    t["poll_url"] = run.get("poll_url") or f"/api/ci/runs/{rid}"
    rec["testory"] = t
    return rec


def refresh_sync(sync_id: str) -> Optional[Dict[str, Any]]:
    rec = get_sync(sync_id)
    if not rec:
        return None
    if rec.get("status") in ("success", "failed") and rec.get("unified_gate_passed") is not None:
        # 已终态仍允许刷新展示
        pass
    rec = refresh_testory_side(rec)
    rec = refresh_jenkins_side(rec)
    rec = recompute_unified(rec)
    saved = _save(rec)
    # Testory 终态后尝试回写 Jenkins 描述
    try:
        _maybe_write_jenkins_description(saved)
    except Exception:
        pass
    return saved


def on_testory_run_finished(run_id: str) -> Optional[Dict[str, Any]]:
    """CI run 终态钩子：刷新关联 sync。"""
    from ci_adapter import get_run

    run = get_run(run_id)
    if not run:
        return None
    sid = str(run.get("sync_id") or "").strip()
    if not sid:
        # 按 run_id 反查
        for s in list_syncs(limit=50):
            if str((s.get("testory") or {}).get("run_id") or "") == str(run_id):
                sid = str(s.get("sync_id") or "")
                break
    if not sid:
        return None
    return refresh_sync(sid)


def _maybe_write_jenkins_description(rec: Dict[str, Any]) -> None:
    j = rec.get("jenkins") or {}
    t = rec.get("testory") or {}
    build_url = j.get("build_url")
    if not build_url or not t.get("terminal"):
        return
    if j.get("description_written"):
        return
    from ai_modules.enterprise.jenkins_trigger import submit_build_description

    text = (
        f"Testory sync={rec.get('sync_id')} "
        f"run={t.get('run_id')} status={t.get('status')} "
        f"gate_passed={t.get('gate_passed')} "
        f"unified_gate_passed={rec.get('unified_gate_passed')}"
    )
    ok = submit_build_description(str(build_url), text)
    if ok:
        j = dict(j)
        j["description_written"] = True
        rec["jenkins"] = j
        _save(rec)


def _ensure_poller(sync_id: str) -> None:
    with _LOCK:
        th = _POLLERS.get(sync_id)
        if th and th.is_alive():
            return

        def _loop():
            try:
                for _ in range(120):  # ~10 min @ 5s
                    rec = refresh_sync(sync_id)
                    if not rec:
                        break
                    if rec.get("status") in ("success", "failed"):
                        break
                    time.sleep(5.0)
            finally:
                with _LOCK:
                    _POLLERS.pop(sync_id, None)

        t = threading.Thread(target=_loop, name=f"ci-sync-{sync_id}", daemon=True)
        _POLLERS[sync_id] = t
        t.start()


def apply_jenkins_result(
    sync_id: str,
    *,
    result: str,
    build_url: str = "",
    build_number: Any = None,
    building: bool = False,
) -> Optional[Dict[str, Any]]:
    """Jenkins 流水线主动回写结果（与轮询等价）。"""
    rec = get_sync(sync_id)
    if not rec:
        return None
    j = dict(rec.get("jenkins") or {})
    r = (result or "").strip().upper() or None
    if build_url:
        j["build_url"] = str(build_url).rstrip("/")
    if build_number is not None:
        j["build_number"] = build_number
    j["building"] = bool(building)
    j["result"] = r
    j["terminal"] = (not building) and bool(r)
    j["source"] = "callback"
    rec["jenkins"] = j
    saved = _save(recompute_unified(rec))
    try:
        _maybe_write_jenkins_description(saved)
    except Exception:
        pass
    return saved


def apply_testory_result(
    sync_id: str,
    *,
    run_id: str = "",
    status: str = "",
    gate_passed: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """显式回写 Testory 侧（通常由 finalize 钩子调用）。"""
    rec = get_sync(sync_id)
    if not rec:
        return None
    t = dict(rec.get("testory") or {})
    if run_id:
        t["run_id"] = run_id
        t["poll_url"] = f"/api/ci/runs/{run_id}"
    if status:
        from ci_adapter import is_terminal_status

        t["status"] = status
        t["terminal"] = is_terminal_status(status)
    if gate_passed is not None:
        t["gate_passed"] = bool(gate_passed)
    rec["testory"] = t
    return _save(recompute_unified(rec))


def start_unified_sync(
    *,
    policy: str = "both_must_pass",
    testory_run_id: str = "",
    jenkins_job: str = "",
    jenkins_parameters: Optional[Dict[str, Any]] = None,
    label: str = "",
    auto_poll: bool = True,
) -> Dict[str, Any]:
    """创建同步会话：可绑定已有 Testory run，并/或触发 Jenkins。"""
    if not testory_run_id and not jenkins_job:
        return {
            "ok": False,
            "error_code": "SYNC_TARGETS_REQUIRED",
            "error": "至少提供 testory_run_id 或 jenkins_job",
        }
    rec = create_sync(
        policy=policy,
        testory_run_id=testory_run_id,
        jenkins_job=jenkins_job,
        label=label,
    )
    sid = rec["sync_id"]
    if testory_run_id:
        bind_testory_run(sid, testory_run_id)
    if jenkins_job:
        bind_jenkins_trigger(sid, job_name=jenkins_job, parameters=jenkins_parameters)
    elif auto_poll:
        pass
    out = refresh_sync(sid) or get_sync(sid)
    if auto_poll and out and out.get("status") == "running":
        _ensure_poller(sid)
    return {"ok": True, "sync": out}
