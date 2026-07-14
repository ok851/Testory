# -*- coding: utf-8 -*-
"""
Windows 桌面视觉框选录制器（单路径：VisualRegionPickerOverlay + session 同步）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_PICKER_AVAILABLE = sys.platform == "win32"
CAPTURE_MODE_VISUAL = "visual"

_session_lock = threading.Lock()
_session: Dict[str, Any] = {
    "active": False,
    "record_mode": False,
    "unified_mode": False,
    "recording": False,
    "paused": False,
    "armed": False,
    "desktop_spec": {},
    "last_pick": None,
    "recorded_steps": [],
    "error": "",
    "picker_closed": False,
    "shutdown_requested": False,
    "message": "",
    "capture_mode": CAPTURE_MODE_VISUAL,
}

_persist_disk_timer: Optional[threading.Timer] = None
_persist_disk_lock = threading.Lock()
_picker_proc: Optional[subprocess.Popen] = None
_picker_thread: Optional[threading.Thread] = None
_picker_ui_lock = threading.RLock()


def _picker_worker_alive() -> bool:
    global _picker_proc, _picker_thread
    if _picker_proc is not None:
        return _picker_proc.poll() is None
    if _picker_thread is not None:
        return _picker_thread.is_alive()
    return False


def _picker_worker_dead() -> bool:
    global _picker_proc, _picker_thread
    if _picker_proc is not None:
        return _picker_proc.poll() is not None
    if _picker_thread is not None:
        return not _picker_thread.is_alive()
    return False


def desktop_picker_available() -> bool:
    if not _PICKER_AVAILABLE:
        return False
    try:
        from desktop_runtime import desktop_runtime_available

        return desktop_runtime_available()
    except ImportError:
        return False


def _session_file_path() -> Path:
    raw = (os.environ.get("UAT_DESKTOP_PICKER_SESSION") or "").strip()
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "uat_desktop_picker_session.json"


def _is_picker_child_process() -> bool:
    return os.environ.get("UAT_PICKER_CHILD") == "1"


def _json_safe_session_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    steps = out.get("recorded_steps")
    if isinstance(steps, list):
        safe_steps: List[Dict[str, Any]] = []
        for st in steps:
            if not isinstance(st, dict):
                continue
            s = dict(st)
            meta = s.get("record_meta")
            if isinstance(meta, dict):
                pick = meta.get("pick")
                if isinstance(pick, dict):
                    s["record_meta"] = {
                        "inferred": bool(meta.get("inferred")),
                        "visual": bool(meta.get("visual")),
                        "pick": {
                            k: pick.get(k)
                            for k in (
                                "selector_type",
                                "selector_value",
                                "rectangle",
                                "pick_point",
                                "label",
                            )
                            if k in pick
                        },
                    }
            safe_steps.append(s)
        out["recorded_steps"] = safe_steps
    try:
        json.dumps(out, ensure_ascii=False, default=str)
        return out
    except Exception:
        out.pop("last_pick", None)
        return out


def _load_session_from_disk() -> Optional[Dict[str, Any]]:
    path = _session_file_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _persist_session_to_disk() -> None:
    try:
        with _session_lock:
            payload = _json_safe_session_payload(dict(_session))
        existing = _load_session_from_disk() or {}
        sent_disk = int(existing.get("_sent_count") or 0)
        sent_local = int(payload.get("_sent_count") or 0)
        if sent_disk > sent_local:
            payload["_sent_count"] = sent_disk
        disk_steps = list(existing.get("recorded_steps") or [])
        local_steps = list(payload.get("recorded_steps") or [])
        payload["recorded_steps"] = local_steps if len(local_steps) >= len(disk_steps) else disk_steps
        _session_file_path().write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _flush_persist_session_to_disk() -> None:
    global _persist_disk_timer
    with _persist_disk_lock:
        if _persist_disk_timer is not None:
            _persist_disk_timer.cancel()
            _persist_disk_timer = None
    _persist_session_to_disk()


def _schedule_persist_session_to_disk(delay_sec: float = 0.12) -> None:
    global _persist_disk_timer
    if not _is_picker_child_process():
        _persist_session_to_disk()
        return

    def _fire() -> None:
        global _persist_disk_timer
        with _persist_disk_lock:
            _persist_disk_timer = None
        _persist_session_to_disk()

    with _persist_disk_lock:
        if _persist_disk_timer is not None:
            _persist_disk_timer.cancel()
        _persist_disk_timer = threading.Timer(max(0.05, float(delay_sec)), _fire)
        _persist_disk_timer.daemon = True
        _persist_disk_timer.start()


def _patch_session_on_disk(**fields: Any) -> None:
    try:
        data = _load_session_from_disk() or {}
        data.update(fields)
        _session_file_path().write_text(
            json.dumps(_json_safe_session_payload(data), ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _clear_last_pick_on_disk() -> None:
    data = _load_session_from_disk()
    if not data or not data.get("last_pick"):
        return
    data["last_pick"] = None
    try:
        _session_file_path().write_text(
            json.dumps(data, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _set_session(**kwargs: Any) -> None:
    with _session_lock:
        _session.update(kwargs)
    if _is_picker_child_process():
        if kwargs.get("picker_closed") or kwargs.get("shutdown_requested"):
            _flush_persist_session_to_disk()
        else:
            _schedule_persist_session_to_disk()


def _session_snapshot() -> Dict[str, Any]:
    proc_dead = _picker_worker_dead() and (_picker_proc is not None or _picker_thread is not None)
    proc_alive = _picker_worker_alive()
    src: Optional[Dict[str, Any]] = None
    if proc_alive:
        src = _load_session_from_disk()
        if src is None:
            with _session_lock:
                src = dict(_session)
        elif src.get("picker_closed"):
            src = dict(src)
            src["picker_closed"] = False
    elif proc_dead:
        src = _load_session_from_disk()
        if src is None:
            with _session_lock:
                src = dict(_session)
        if not src.get("picker_closed"):
            src = {
                **src,
                "active": False,
                "picker_closed": True,
                "error": src.get("error") or "框选录制窗口已退出，请重新启动",
            }
    else:
        with _session_lock:
            src = dict(_session)
    assert src is not None
    return {
        "active": bool(src.get("active")),
        "record_mode": bool(src.get("record_mode")),
        "unified_mode": bool(src.get("unified_mode")),
        "recording": bool(src.get("recording")),
        "paused": bool(src.get("paused")),
        "armed": bool(src.get("armed")),
        "last_pick": src.get("last_pick"),
        "recorded_steps": list(src.get("recorded_steps") or []),
        "_sent_count": int(src.get("_sent_count") or 0),
        "error": src.get("error") or "",
        "picker_closed": bool(src.get("picker_closed")),
        "message": src.get("message") or "",
        "desktop_spec": dict(src.get("desktop_spec") or {}),
        "case_id": int(src.get("case_id") or 0),
        "capture_mode": src.get("capture_mode") or CAPTURE_MODE_VISUAL,
        "starting": bool(src.get("starting")),
    }


def _request_picker_shutdown() -> None:
    data = _load_session_from_disk() or {}
    with _session_lock:
        merged = {**dict(_session), **data}
        merged["shutdown_requested"] = True
        merged["active"] = False
        merged["armed"] = False
        merged["recording"] = False
        _session.update(merged)
    try:
        _session_file_path().write_text(
            json.dumps(merged, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _stop_picker_process(timeout: float = 8.0, *, fast: bool = False) -> None:
    global _picker_proc, _picker_thread
    proc = _picker_proc
    thread = _picker_thread
    _picker_proc = None
    _picker_thread = None
    if thread is not None and thread.is_alive():
        _request_picker_shutdown()
        thread.join(timeout=min(float(timeout), 3.0) if fast else float(timeout))
        return
    if not proc:
        return
    if fast:
        timeout = min(float(timeout), 1.5)
    if proc.poll() is None:
        _request_picker_shutdown()
        grace = 0.8 if fast else min(2.5, float(timeout))
        deadline = time.time() + grace
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.08 if fast else 0.12)
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1.5 if fast else 3.0)
                except Exception:
                    pass


def _picker_child_main(cfg_path: str) -> None:
    os.environ["UAT_PICKER_CHILD"] = "1"
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    os.environ["UAT_DESKTOP_PICKER_SESSION"] = str(
        cfg.get("session_path") or _session_file_path()
    )
    disk = _load_session_from_disk()
    if disk:
        with _session_lock:
            _session.clear()
            _session.update(disk)
    auto_rec = bool(cfg.get("record_mode") or cfg.get("unified_mode"))
    _set_session(
        active=True,
        picker_closed=False,
        starting=False,
        message="捕获器待命：请点悬浮条「开始捕获」或 F2，再点目标元素",
        record_mode=bool(cfg.get("record_mode")),
        unified_mode=bool(cfg.get("unified_mode")),
        capture_mode=CAPTURE_MODE_VISUAL,
        recording=auto_rec,
        armed=False,
        error="",
    )

    def _on_record(data: Dict[str, Any]) -> None:
        from desktop_visual_picker import (
            build_visual_recorded_step,
            schedule_uia_snapshot_enrichment,
        )

        pick = data.get("pick") or {}
        click_x = int(data.get("click_x") or (pick.get("pick_point") or {}).get("x") or 0)
        click_y = int(data.get("click_y") or (pick.get("pick_point") or {}).get("y") or 0)
        action = str(data.get("action") or pick.get("record_action") or "click")
        input_val = str(data.get("input_value") or pick.get("input_value") or "")
        step = build_visual_recorded_step(
            pick,
            action=action,
            input_value=input_val,
        )

        def _merge_uia(enriched_pick: Dict[str, Any]) -> None:
            new_step = build_visual_recorded_step(
                enriched_pick,
                action=action,
                input_value=input_val,
            )
            with _session_lock:
                recorded = list(_session.get("recorded_steps") or [])
                if recorded:
                    recorded[-1] = new_step
                    _session["recorded_steps"] = recorded
                _session["last_pick"] = {
                    **enriched_pick,
                    "record_action": step.get("action"),
                }
                _session["message"] = (
                    f"已录制第 {len(recorded)} 步（结构+视觉）"
                    if enriched_pick.get("element_snapshot")
                    else _session.get("message", "")
                )
            _flush_persist_session_to_disk()

        with _session_lock:
            recorded = list(_session.get("recorded_steps") or [])
            recorded.append(step)
            _session["recorded_steps"] = recorded
            _session["last_pick"] = {**pick, "record_action": step.get("action")}
            _session["message"] = f"已录制第 {len(recorded)} 步（visual）"
        _flush_persist_session_to_disk()
        schedule_uia_snapshot_enrichment(
            pick, click_x, click_y, on_done=_merge_uia
        )

    def _on_message(msg: str) -> None:
        _set_session(message=msg, error="")
        _flush_persist_session_to_disk()

    def _on_error(err: str) -> None:
        _set_session(error=err)
        _flush_persist_session_to_disk()

    def _on_close() -> None:
        _set_session(active=False, picker_closed=True, recording=False, armed=False)
        _flush_persist_session_to_disk()

    def _on_armed(armed: bool) -> None:
        _set_session(armed=bool(armed))
        _flush_persist_session_to_disk()

    from desktop_visual_picker import VisualRegionPickerOverlay

    VisualRegionPickerOverlay(
        on_record=_on_record,
        on_message=_on_message,
        on_error=_on_error,
        on_close=_on_close,
        on_armed_change=_on_armed,
        default_action=str(cfg.get("record_action") or "click"),
    ).run()


def _spawn_picker_process(
    desktop_spec: Dict[str, Any],
    record_mode: bool,
    unified_mode: bool,
    *,
    record_action: str = "click",
) -> None:
    global _picker_proc
    session_path = Path(tempfile.gettempdir()) / "uat_desktop_picker_session.json"
    try:
        session_path.unlink(missing_ok=True)
    except OSError:
        pass
    os.environ["UAT_DESKTOP_PICKER_SESSION"] = str(session_path)
    cfg_path = Path(tempfile.gettempdir()) / "uat_desktop_picker_cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "desktop_spec": desktop_spec,
                "record_mode": record_mode,
                "unified_mode": unified_mode,
                "capture_mode": CAPTURE_MODE_VISUAL,
                "record_action": record_action,
                "session_path": str(session_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    child_env = {
        **os.environ,
        "UAT_PICKER_CHILD": "1",
        "UAT_DESKTOP_PICKER_SESSION": str(session_path),
    }
    if getattr(sys, "frozen", False):
        global _picker_thread

        def _run_inline() -> None:
            try:
                os.environ.update(child_env)
                _picker_child_main(str(cfg_path))
            except Exception as exc:
                _set_session(error=str(exc), picker_closed=True, active=False)

        _picker_thread = threading.Thread(
            target=_run_inline,
            name="uat-desktop-picker",
            daemon=True,
        )
        _picker_thread.start()
        return

    script = str(Path(__file__).resolve())
    try:
        from install_paths import resolve_install_root

        cwd = str(resolve_install_root())
    except ImportError:
        cwd = str(Path(__file__).resolve().parent)
    _picker_proc = subprocess.Popen(
        [sys.executable, script, "--picker-child", str(cfg_path)],
        cwd=cwd,
        env=child_env,
    )


def start_desktop_picker(
    desktop_spec: Dict[str, Any],
    *,
    record_mode: bool = False,
    unified_mode: bool = False,
    prefer_web_clicks: bool = False,
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
    case_id: Optional[int] = None,
    skip_initial_stop: bool = False,
) -> Dict[str, Any]:
    del prefer_web_clicks, input_value, verify_type
    if not desktop_picker_available():
        try:
            from desktop_runtime import desktop_runtime_unavailable_reason

            err = desktop_runtime_unavailable_reason() or (
                "桌面框选录制不可用（需 Windows + opencv-python + mss）"
            )
        except ImportError:
            err = "桌面框选录制仅支持 Windows（见 requirements-windows.txt）"
        return {"success": False, "error": err}

    with _picker_ui_lock:
        if not skip_initial_stop:
            stop_desktop_picker(fast=True)
        _set_session(
            active=False,
            record_mode=record_mode,
            unified_mode=unified_mode,
            recording=False,
            paused=False,
            armed=False,
            desktop_spec=dict(desktop_spec or {}),
            last_pick=None,
            shutdown_requested=False,
            recorded_steps=[],
            _sent_count=0,
            case_id=int(case_id) if case_id else 0,
            error="",
            picker_closed=False,
            message="正在启动框选录制…",
            starting=True,
            capture_mode=CAPTURE_MODE_VISUAL,
        )
        _persist_session_to_disk()

        try:
            _spawn_picker_process(
                desktop_spec or {},
                record_mode,
                unified_mode,
                record_action=record_action,
            )
        except Exception as exc:
            return {"success": False, "error": f"无法启动框选录制窗口: {exc}"}

        deadline = time.time() + 1.2
        snap: Dict[str, Any] = {}
        while time.time() < deadline:
            time.sleep(0.06)
            if _picker_worker_dead():
                err = (_load_session_from_disk() or {}).get("error") or "框选录制进程已退出"
                return {"success": False, "error": err}
            snap = _session_snapshot()
            if snap.get("active"):
                break
        if _picker_worker_dead():
            err = (_load_session_from_disk() or {}).get("error") or "框选录制进程已退出"
            return {"success": False, "error": err}
        if _picker_worker_alive():
            _set_session(
                starting=False,
                recording=bool(record_mode or unified_mode),
                armed=False,
            )
            _persist_session_to_disk()
            return {
                "success": True,
                "record_mode": record_mode,
                "starting": False,
                "message": snap.get("message") or "框选录制已启动",
            }
        return {"success": False, "error": "框选录制窗口未就绪，请查看是否被安全软件拦截"}


def stop_desktop_picker(*, fast: bool = False, reset_automation: bool = True) -> Dict[str, Any]:
    with _picker_ui_lock:
        with _session_lock:
            was_active = bool(_session.get("active"))
            recorded = list(_session.get("recorded_steps") or [])
            last_pick = _session.get("last_pick")

        disk = _load_session_from_disk()
        had_proc = _picker_worker_alive()
        _stop_picker_process(timeout=1.5 if fast else 8.0, fast=fast)
        if not fast:
            time.sleep(0.1)
        if disk:
            with _session_lock:
                _session.update(disk)

        if reset_automation and (was_active or had_proc):
            try:
                from desktop_automation import sync_reset_desktop_automation

                sync_reset_desktop_automation()
            except Exception:
                pass

        if fast:
            _set_session(
                active=False,
                armed=False,
                recording=False,
                paused=False,
                picker_closed=False,
                shutdown_requested=True,
            )
        else:
            _set_session(
                active=False,
                armed=False,
                recording=False,
                paused=False,
                picker_closed=True,
            )
            _persist_session_to_disk()
        return {
            "success": True,
            "stopped": True,
            "recorded_steps": recorded,
            "last_pick": last_pick,
            "was_active": was_active,
        }


def _drain_unsent_recorded_steps(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not (snap.get("record_mode") or snap.get("unified_mode")):
        return []
    recorded = list(snap.get("recorded_steps") or [])
    sent = int(snap.get("_sent_count") or 0)
    if len(recorded) <= sent:
        return []
    return recorded[sent:]


def get_desktop_picker_status(*, consume_last_pick: bool = False) -> Dict[str, Any]:
    snap = _session_snapshot()
    last = snap.get("last_pick")
    new_steps: List[Dict[str, Any]] = []
    with _session_lock:
        pending = _drain_unsent_recorded_steps(snap)
        if pending:
            new_steps = pending
            sent_after = len(snap.get("recorded_steps") or [])
            _session["_sent_count"] = sent_after
            if _picker_worker_alive():
                _patch_session_on_disk(_sent_count=sent_after)
        if consume_last_pick and last:
            if _picker_worker_alive():
                _clear_last_pick_on_disk()
            _session["last_pick"] = None
            last = None
    out = {
        "success": True,
        **snap,
        "last_pick": last,
        "new_recorded_steps": new_steps,
    }
    if snap.get("picker_closed"):
        out["recorded_steps"] = list(snap.get("recorded_steps") or [])
    return out


def sync_start_desktop_picker(
    desktop_spec: Dict[str, Any],
    *,
    record_mode: bool = False,
    unified_mode: bool = False,
    prefer_web_clicks: bool = False,
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
    case_id: Optional[int] = None,
    skip_initial_stop: bool = False,
) -> Dict[str, Any]:
    return start_desktop_picker(
        desktop_spec,
        record_mode=record_mode,
        unified_mode=unified_mode,
        prefer_web_clicks=prefer_web_clicks,
        record_action=record_action,
        input_value=input_value,
        verify_type=verify_type,
        case_id=case_id,
        skip_initial_stop=skip_initial_stop,
    )


def sync_stop_desktop_picker(*, fast: bool = False) -> Dict[str, Any]:
    return stop_desktop_picker(fast=fast)


def sync_get_desktop_picker_status(**kwargs: Any) -> Dict[str, Any]:
    return get_desktop_picker_status(**kwargs)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--picker-child":
        _picker_child_main(sys.argv[2])
    else:
        print("桌面框选录制需由平台服务调用，或: python desktop_picker.py --picker-child <cfg.json>")
        sys.exit(1)
