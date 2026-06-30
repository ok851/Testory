# -*- coding: utf-8 -*-
"""录制会话管理与 WebSocket 事件广播。"""
from __future__ import annotations

import asyncio
import base64
import threading
import time
from typing import Any, Dict, List, Optional, Set

from mobile_assistant_events import normalize_assistant_event

from mobile_automation_gateway import plugin_rpc

_recording_lock = threading.Lock()
_recording_sessions: Dict[str, Dict[str, Any]] = {}
_live_steps: Dict[str, List[Dict[str, Any]]] = {}
_poll_threads: Dict[str, threading.Thread] = {}
_ws_clients: Set[Any] = set()
_ws_loop: Optional[asyncio.AbstractEventLoop] = None
_ws_lock = threading.Lock()


def set_ws_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _ws_loop
    _ws_loop = loop


def register_ws(ws) -> None:
    with _ws_lock:
        _ws_clients.add(ws)


def unregister_ws(ws) -> None:
    with _ws_lock:
        _ws_clients.discard(ws)


def _schedule_broadcast(msg: Dict[str, Any]) -> None:
    loop = _ws_loop
    if loop is None or not loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(_broadcast_json(msg), loop)


async def _broadcast_json(msg: Dict[str, Any]) -> None:
    import json

    raw = json.dumps(msg, ensure_ascii=False)
    dead = []
    for ws in list(_ws_clients):
        try:
            await ws.send_text(raw)
        except Exception:
            dead.append(ws)
    for ws in dead:
        unregister_ws(ws)


def broadcast_event(event_type: str, payload: Dict[str, Any]) -> None:
    _schedule_broadcast({"type": event_type, "payload": payload, "ts": time.time()})


def is_recording(udid: str) -> bool:
    with _recording_lock:
        return bool(_recording_sessions.get(udid, {}).get("active"))


def start_recording_session(udid: str, *, screenshot_per_step: bool = True) -> Dict[str, Any]:
    udid = (udid or "").strip()
    if not udid:
        return {"success": False, "error": "缺少 udid"}
    ok, msg = plugin_rpc.ensure_plugin_tunnel(udid)
    if not ok:
        return {"success": False, "error": msg}
    try:
        status = plugin_rpc.plugin_status(udid)
    except Exception as exc:
        return {"success": False, "error": f"无法连接设备插件: {exc}"}
    if not status.get("accessibility_enabled"):
        return {
            "success": False,
            "error": "无障碍服务未就绪。请在手机「Testory 助手」中开启无障碍后再录制。",
        }
    try:
        from mobile_adb_control import adb_press_home

        # 与设备端 SessionForegroundGuard 双保险：PC 侧也先发 Home，减少助手界面误录。
        adb_press_home(udid)
        time.sleep(0.25)
    except Exception:
        pass
    try:
        plugin_rpc.start_recording(udid, screenshot_per_step=screenshot_per_step)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    with _recording_lock:
        _recording_sessions[udid] = {
            "active": True,
            "screenshot_per_step": screenshot_per_step,
            "started_at": time.time(),
            "step_count": 0,
        }
        _live_steps[udid] = []
    _start_poll_thread(udid)
    broadcast_event("recording_started", {"udid": udid})
    return {
        "success": True,
        "udid": udid,
        "message": "正在返回桌面，录制就绪后请操作手机",
        "preparing_desktop": True,
    }


def stop_recording_session(udid: str) -> Dict[str, Any]:
    udid = (udid or "").strip()
    with _recording_lock:
        sess = _recording_sessions.get(udid)
        if sess:
            sess["active"] = False
        live = list(_live_steps.get(udid) or [])
    try:
        plugin_rpc.stop_recording(udid)
    except Exception:
        pass
    broadcast_event("recording_stopped", {"udid": udid, "step_count": len(live)})
    return {"success": True, "udid": udid, "steps": live, "step_count": len(live)}


def _start_poll_thread(udid: str) -> None:
    t = _poll_threads.get(udid)
    if t is not None and t.is_alive():
        return

    def _worker() -> None:
        disconnect_since: Optional[float] = None
        screen_width = 0
        screen_height = 0
        while True:
            with _recording_lock:
                sess = _recording_sessions.get(udid)
                if not sess or not sess.get("active"):
                    break
                screenshot_per_step = bool(sess.get("screenshot_per_step"))
                started_at = float(sess.get("started_at") or 0)
            try:
                ok, _ = plugin_rpc.ping_plugin(udid)
                if not ok:
                    if disconnect_since is None:
                        disconnect_since = time.time()
                    elif time.time() - disconnect_since >= 5.0:
                        broadcast_event(
                            "plugin_disconnected",
                            {"udid": udid, "message": "插件通信中断超过 5 秒，录制已暂停"},
                        )
                        with _recording_lock:
                            if udid in _recording_sessions:
                                _recording_sessions[udid]["active"] = False
                        break
                else:
                    disconnect_since = None
                try:
                    status = plugin_rpc.plugin_status(udid)
                    screen_width = int(status.get("screen_width") or screen_width or 0)
                    screen_height = int(status.get("screen_height") or screen_height or 0)
                    armed = str(status.get("armed_mode") or "").strip().lower()
                    agent_active = bool(status.get("agent_recording_active", True))
                    if not agent_active and armed == "idle" and time.time() - started_at > 1.5:
                        broadcast_event(
                            "recording_stopped",
                            {"udid": udid, "step_count": len(_live_steps.get(udid) or []), "source": "device"},
                        )
                        with _recording_lock:
                            if udid in _recording_sessions:
                                _recording_sessions[udid]["active"] = False
                        break
                except Exception:
                    pass
                raw_steps = plugin_rpc.poll_steps(udid, limit=10)
                for raw in raw_steps:
                    if not isinstance(raw, dict):
                        continue
                    step = normalize_assistant_event(
                        raw,
                        screen_width=screen_width,
                        screen_height=screen_height,
                    )
                    screenshot_b64 = ""
                    if screenshot_per_step:
                        try:
                            img, fmt = plugin_rpc.take_screenshot(udid)
                            screenshot_b64 = base64.b64encode(img).decode("ascii")
                            step.setdefault("mobile_spec", {})["screenshot_format"] = fmt
                        except Exception:
                            try:
                                from mobile_device_manager import capture_screenshot_png

                                img = capture_screenshot_png(udid)
                                screenshot_b64 = base64.b64encode(img).decode("ascii")
                                step.setdefault("mobile_spec", {})["screenshot_format"] = "png"
                            except Exception:
                                pass
                    payload = {
                        "udid": udid,
                        "step": step,
                        "raw": raw,
                        "screenshot_base64": screenshot_b64,
                    }
                    with _recording_lock:
                        if udid in _recording_sessions:
                            _recording_sessions[udid]["step_count"] = int(
                                _recording_sessions[udid].get("step_count") or 0
                            ) + 1
                        buf = _live_steps.setdefault(udid, [])
                        buf.append(step)
                    broadcast_event("step", payload)
            except Exception as exc:
                broadcast_event("error", {"udid": udid, "error": str(exc)})
            time.sleep(0.12)

    t = threading.Thread(target=_worker, daemon=True, name=f"mobile-rec-poll-{udid}")
    _poll_threads[udid] = t
    t.start()


def get_live_steps(udid: str) -> List[Dict[str, Any]]:
    udid = (udid or "").strip()
    with _recording_lock:
        return list(_live_steps.get(udid) or [])


def pause_recording_session(udid: str) -> Dict[str, Any]:
    udid = (udid or "").strip()
    try:
        plugin_rpc.pause_recording(udid)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    with _recording_lock:
        sess = _recording_sessions.get(udid)
        if sess:
            sess["paused"] = True
    return {"success": True, "udid": udid, "message": "录制已暂停"}


def resume_recording_session(udid: str) -> Dict[str, Any]:
    udid = (udid or "").strip()
    try:
        plugin_rpc.resume_recording(udid)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    with _recording_lock:
        sess = _recording_sessions.get(udid)
        if sess:
            sess["paused"] = False
    return {"success": True, "udid": udid, "message": "录制已继续"}


def recording_status(udid: str = "") -> Dict[str, Any]:
    with _recording_lock:
        if udid:
            sess = _recording_sessions.get(udid)
            return {
                "udid": udid,
                "active": bool(sess and sess.get("active")),
                "step_count": int(sess.get("step_count") or 0) if sess else 0,
            }
        return {
            "sessions": {
                k: {
                    "active": bool(v.get("active")),
                    "step_count": int(v.get("step_count") or 0),
                }
                for k, v in _recording_sessions.items()
            }
        }
