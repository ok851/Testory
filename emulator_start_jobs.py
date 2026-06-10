# -*- coding: utf-8 -*-
"""模拟器后台启动任务（避免 HTTP 长时间阻塞）。"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_jobs_mem: Dict[str, Dict[str, Any]] = {}


def _write_job(job_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        rec = dict(_jobs_mem.get(job_id) or {})
        rec.update(patch)
        rec["job_id"] = job_id
        rec["updated_at"] = datetime.now(timezone.utc).isoformat()
        _jobs_mem[job_id] = rec
        return dict(rec)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    jid = (job_id or "").strip()
    if not jid:
        return None
    with _lock:
        rec = _jobs_mem.get(jid)
        return dict(rec) if rec else None


def list_active_jobs() -> List[Dict[str, Any]]:
    with _lock:
        return [dict(v) for v in _jobs_mem.values() if (v.get("state") or "") == "running"]


def start_emulator_job(
    avd_name: str,
    *,
    port: int = 5554,
    gpu: str = "host",
    no_window: bool = True,
) -> str:
    with _lock:
        for rec in _jobs_mem.values():
            if (rec.get("state") or "") == "running":
                raise RuntimeError("已有模拟器启动任务进行中，请勿重复点击")
    job_id = str(uuid.uuid4())
    _write_job(
        job_id,
        {
            "avd_name": avd_name,
            "state": "running",
            "percent": 5,
            "label": "准备启动模拟器…",
            "ok": None,
            "error": "",
            "result": {},
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    def _progress(percent: int, label: str) -> None:
        _write_job(
            job_id,
            {
                "state": "running",
                "percent": max(0, min(99, int(percent))),
                "label": (label or "").strip() or "启动中…",
            },
        )

    def _worker() -> None:
        try:
            from mobile_emulator_manager import start_avd

            ok, msg, meta = start_avd(
                avd_name,
                port=port,
                gpu=gpu,
                no_window=no_window,
                progress_cb=_progress,
            )
            if ok:
                _write_job(
                    job_id,
                    {
                        "state": "done",
                        "percent": 100,
                        "label": "启动完成",
                        "ok": True,
                        "error": "",
                        "message": msg,
                        "result": meta,
                    },
                )
            else:
                _write_job(
                    job_id,
                    {
                        "state": "failed",
                        "percent": 100,
                        "label": "启动失败",
                        "ok": False,
                        "error": msg,
                        "result": meta or {},
                    },
                )
        except Exception as exc:
            _write_job(
                job_id,
                {
                    "state": "failed",
                    "percent": 100,
                    "label": "启动失败",
                    "ok": False,
                    "error": str(exc),
                    "result": {},
                },
            )

    threading.Thread(target=_worker, name=f"emulator-start-{avd_name}", daemon=True).start()
    return job_id


def start_switch_model_job(
    preset_id: str,
    *,
    port: int = 5554,
    gpu: str = "host",
    no_window: bool = True,
    force_restart: bool = False,
) -> str:
    with _lock:
        for rec in _jobs_mem.values():
            if (rec.get("state") or "") == "running":
                raise RuntimeError("已有模拟器启动任务进行中，请勿重复点击")
    job_id = str(uuid.uuid4())
    _write_job(
        job_id,
        {
            "preset_id": preset_id,
            "job_type": "switch_model",
            "state": "running",
            "percent": 5,
            "label": "准备切换设备型号…",
            "ok": None,
            "error": "",
            "result": {},
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    def _progress(percent: int, label: str) -> None:
        _write_job(
            job_id,
            {
                "state": "running",
                "percent": max(0, min(99, int(percent))),
                "label": (label or "").strip() or "切换中…",
            },
        )

    def _worker() -> None:
        try:
            from mobile_emulator_manager import ensure_emulator_for_preset

            ok, msg, meta = ensure_emulator_for_preset(
                preset_id,
                port=port,
                gpu=gpu,
                no_window=no_window,
                force_restart=force_restart,
                progress_cb=_progress,
            )
            if ok:
                _write_job(
                    job_id,
                    {
                        "state": "done",
                        "percent": 100,
                        "label": "切换完成",
                        "ok": True,
                        "error": "",
                        "message": msg,
                        "result": meta,
                    },
                )
            else:
                _write_job(
                    job_id,
                    {
                        "state": "failed",
                        "percent": 100,
                        "label": "切换失败",
                        "ok": False,
                        "error": msg,
                        "result": meta or {},
                    },
                )
        except Exception as exc:
            _write_job(
                job_id,
                {
                    "state": "failed",
                    "percent": 100,
                    "label": "切换失败",
                    "ok": False,
                    "error": str(exc),
                    "result": {},
                },
            )

    threading.Thread(
        target=_worker,
        name=f"emulator-switch-{preset_id}",
        daemon=True,
    ).start()
    return job_id


def start_launch_studio_job(
    preset_id: str,
    *,
    port: int = 5554,
    gpu: str = "host",
    no_window: bool = True,
    force_restart: bool = False,
    try_appium: bool = False,
    client_host: str = "",
) -> str:
    """一键启动：环境准备 + 模拟器 + 投屏连接。"""
    with _lock:
        for rec in _jobs_mem.values():
            if (rec.get("state") or "") == "running":
                raise RuntimeError("已有模拟器启动任务进行中，请勿重复点击")
    job_id = str(uuid.uuid4())
    _write_job(
        job_id,
        {
            "preset_id": preset_id,
            "job_type": "launch_studio",
            "state": "running",
            "percent": 3,
            "label": "准备启动…",
            "ok": None,
            "error": "",
            "result": {},
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    def _progress(percent: int, label: str) -> None:
        _write_job(
            job_id,
            {
                "state": "running",
                "percent": max(0, min(99, int(percent))),
                "label": (label or "").strip() or "启动中…",
            },
        )

    def _worker() -> None:
        try:
            from mobile_studio_launch import finish_studio_connect, launch_emulator_studio

            ok, msg, meta = launch_emulator_studio(
                preset_id,
                port=port,
                gpu=gpu,
                no_window=no_window,
                force_restart=force_restart,
                progress_cb=_progress,
            )
            if not ok:
                _write_job(
                    job_id,
                    {
                        "state": "failed",
                        "percent": 100,
                        "label": "启动失败",
                        "ok": False,
                        "error": msg,
                        "result": meta or {},
                    },
                )
                return
            serial = (meta.get("serial") or "").strip()
            frame_id = meta.get("frame_preset_id") or "generic_19_9"
            _progress(98, "连接投屏…")
            connect_payload = finish_studio_connect(
                serial,
                frame_preset=frame_id,
                try_appium=try_appium,
                client_host=client_host,
            )
            result = {**meta, **connect_payload}
            _write_job(
                job_id,
                {
                    "state": "done",
                    "percent": 100,
                    "label": "已就绪",
                    "ok": True,
                    "error": "",
                    "message": msg,
                    "result": result,
                },
            )
        except Exception as exc:
            _write_job(
                job_id,
                {
                    "state": "failed",
                    "percent": 100,
                    "label": "启动失败",
                    "ok": False,
                    "error": str(exc),
                    "result": {},
                },
            )

    threading.Thread(
        target=_worker,
        name=f"emulator-launch-{preset_id}",
        daemon=True,
    ).start()
    return job_id
