# -*- coding: utf-8 -*-
"""插件市场后台安装任务（切换页面不中断）。"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_lock = threading.Lock()
_jobs_mem: Dict[str, Dict[str, Any]] = {}

ProgressCallback = Callable[[int, str], None]

# 耗时较长的运行时包默认后台安装
BACKGROUND_PLUGIN_IDS = frozenset(
    {
        "mobile-android-platform-tools",
    }
)


def _jobs_file() -> Path:
    from web_capture.plugin_market import software_extensions_root

    return software_extensions_root() / "plugin_install_jobs.json"


def _load_all() -> Dict[str, Any]:
    path = _jobs_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_all(data: Dict[str, Any]) -> None:
    path = _jobs_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_job(job_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        disk = _load_all()
        rec = dict(disk.get(job_id) or _jobs_mem.get(job_id) or {})
        rec.update(patch)
        rec["job_id"] = job_id
        rec["updated_at"] = datetime.now(timezone.utc).isoformat()
        disk[job_id] = rec
        _jobs_mem[job_id] = rec
        _save_all(disk)
        return rec


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    jid = (job_id or "").strip()
    if not jid:
        return None
    with _lock:
        if jid in _jobs_mem:
            return dict(_jobs_mem[jid])
        disk = _load_all()
        rec = disk.get(jid)
        return dict(rec) if rec else None


def list_active_jobs() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with _lock:
        disk = _load_all()
        merged = {**disk, **_jobs_mem}
    for rec in merged.values():
        if (rec.get("state") or "") == "running":
            out.append(dict(rec))
    return out


def make_progress_callback(job_id: str) -> ProgressCallback:
    def _cb(percent: int, label: str) -> None:
        _write_job(
            job_id,
            {
                "state": "running",
                "percent": max(0, min(100, int(percent))),
                "label": (label or "").strip() or "安装中…",
            },
        )

    return _cb


def start_install_job(plugin_id: str) -> str:
    """启动后台安装，立即返回 job_id。"""
    pid = (plugin_id or "").strip()
    job_id = str(uuid.uuid4())
    _write_job(
        job_id,
        {
            "plugin_id": pid,
            "state": "running",
            "percent": 0,
            "label": "准备安装…",
            "install_ok": None,
            "error": "",
            "result": {},
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    def _worker() -> None:
        try:
            from web_capture.plugin_market import install_plugin_sync

            progress_cb = make_progress_callback(job_id)
            result = install_plugin_sync(pid, progress_cb=progress_cb)
            if result.get("success"):
                _write_job(
                    job_id,
                    {
                        "state": "done",
                        "percent": 100,
                        "label": "安装完成",
                        "install_ok": True,
                        "error": "",
                        "result": result,
                        "message": result.get("message") or "",
                    },
                )
            else:
                _write_job(
                    job_id,
                    {
                        "state": "failed",
                        "percent": 100,
                        "label": "安装失败",
                        "install_ok": False,
                        "error": result.get("error") or "安装失败",
                        "result": result,
                    },
                )
        except Exception as exc:
            _write_job(
                job_id,
                {
                    "state": "failed",
                    "percent": 100,
                    "label": "安装失败",
                    "install_ok": False,
                    "error": str(exc),
                    "result": {},
                },
            )

    threading.Thread(target=_worker, name=f"plugin-install-{pid}", daemon=True).start()
    return job_id


def should_install_in_background(plugin_id: str) -> bool:
    return (plugin_id or "").strip() in BACKGROUND_PLUGIN_IDS
