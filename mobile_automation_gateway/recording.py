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
_poll_stop_events: Dict[str, threading.Event] = {}
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


def clear_live_steps(udid: str) -> None:
    """清空桌面端 live 缓冲，避免新录制混入旧步骤。"""
    udid = (udid or "").strip()
    if not udid:
        return
    with _recording_lock:
        _live_steps[udid] = []


def _deactivate_session(udid: str) -> None:
    """停止旧轮询线程、触摸录制器并清空会话，确保下次录制从空白开始。"""
    udid = (udid or "").strip()
    if not udid:
        return
    stop_evt = _poll_stop_events.get(udid)
    if stop_evt:
        stop_evt.set()
    with _recording_lock:
        sess = _recording_sessions.get(udid)
        if sess:
            sess["active"] = False
    t = _poll_threads.get(udid)
    if t is not None and t.is_alive():
        t.join(timeout=3.0)
    with _recording_lock:
        _live_steps[udid] = []
    _poll_stop_events.pop(udid, None)


def start_recording_session(udid: str, *, screenshot_per_step: bool = True) -> Dict[str, Any]:
    udid = (udid or "").strip()
    if not udid:
        return {"success": False, "error": "缺少 udid"}
    # 原缺陷：未先停旧会话/清缓冲，poll 线程可能仍持有上一轮步骤。
    _deactivate_session(udid)
    time.sleep(0.05)
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
        time.sleep(0.15)
    except Exception:
        pass
    try:
        plugin_rpc.start_recording(udid, screenshot_per_step=screenshot_per_step)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    # 通知设备端开始录制（手机端自行录制，PC 端不介入）
    screen_w, screen_h = 1080, 1920
    try:
        from mobile_adb_control import adb_get_screen_size

        screen_w, screen_h = adb_get_screen_size(udid)
    except Exception:
        pass
    with _recording_lock:
        _recording_sessions[udid] = {
            "active": True,
            "screenshot_per_step": screenshot_per_step,
            "started_at": time.time(),
            "step_count": 0,
            "screen_width": screen_w,
            "screen_height": screen_h,
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
    # 1. 立即标记本地会话停止并广播，PC 端状态不再依赖设备端轮询。
    stop_evt = _poll_stop_events.get(udid)
    if stop_evt:
        stop_evt.set()
    current_count = 0
    with _recording_lock:
        sess = _recording_sessions.get(udid)
        if sess:
            sess["active"] = False
            current_count = int(sess.get("step_count") or 0)
    broadcast_event("recording_stopped", {"udid": udid, "step_count": current_count})

    # 2. 通知设备端停止录制（清理 overlay 和 armed mode）
    try:
        plugin_rpc.stop_recording(udid)
    except Exception:
        pass

    # 3. 等待轮询线程退出，确保所有已拉取的步骤都写入 _live_steps
    t = _poll_threads.get(udid)
    if t is not None and t.is_alive():
        t.join(timeout=3.0)

    # 4. 从设备端拉取最终步骤
    try:
        final_poll = plugin_rpc.poll_steps(udid, limit=100)
        if isinstance(final_poll, dict):
            final_steps = final_poll.get("steps") or []
            if final_steps:
                _append_device_steps(udid, final_steps)
    except Exception:
        pass

    # 5. 读取最终步骤列表并返回
    with _recording_lock:
        live = list(_live_steps.get(udid) or [])
    return {"success": True, "udid": udid, "steps": live, "step_count": len(live)}


def _append_device_steps(udid: str, device_steps: List[Dict[str, Any]]) -> None:
    """将设备端步骤归一化后写入 live steps 并广播 step 事件。"""
    with _recording_lock:
        sess = _recording_sessions.get(udid)
        if not sess:
            return
        screen_w = int(sess.get("screen_width") or 1080)
        screen_h = int(sess.get("screen_height") or 1920)
    for raw in device_steps:
        step = normalize_assistant_event(raw, screen_width=screen_w, screen_height=screen_h)
        with _recording_lock:
            s = _recording_sessions.get(udid)
            if not s:
                return
            s["step_count"] = int(s.get("step_count") or 0) + 1
            buf = _live_steps.setdefault(udid, [])
            buf.append(step)
            _schedule_broadcast({"type": "step", "payload": {"udid": udid, "step": step, "raw": raw}})


def _start_poll_thread(udid: str) -> None:
    # 先停止旧线程
    old_evt = _poll_stop_events.get(udid)
    if old_evt:
        old_evt.set()
    old_t = _poll_threads.get(udid)
    if old_t is not None and old_t.is_alive():
        old_t.join(timeout=2.0)

    stop_event = threading.Event()
    _poll_stop_events[udid] = stop_event

    def _worker() -> None:
        disconnect_since: Optional[float] = None
        consecutive_errors = 0
        while not stop_event.is_set():
            with _recording_lock:
                sess = _recording_sessions.get(udid)
                if not sess or not sess.get("active"):
                    break
                screenshot_per_step = bool(sess.get("screenshot_per_step"))
            try:
                ok, _ = plugin_rpc.ping_plugin(udid)
                if not ok:
                    if disconnect_since is None:
                        disconnect_since = time.time()
                    elif time.time() - disconnect_since >= 8.0:
                        broadcast_event(
                            "plugin_disconnected",
                            {"udid": udid, "message": "插件通信中断超过 8 秒，录制已暂停"},
                        )
                        with _recording_lock:
                            if udid in _recording_sessions:
                                _recording_sessions[udid]["active"] = False
                        break
                else:
                    disconnect_since = None

                # 从设备端获取实时步骤
                raw_status = plugin_rpc.poll_steps(udid, limit=20)
                poll_result = raw_status if isinstance(raw_status, dict) else {"recording_active": True}
                device_steps = poll_result.get("steps") or []
                if device_steps:
                    _append_device_steps(udid, device_steps)

                # 心跳：检查设备端是否仍认为在录制（仅作兜底，PC 端停止已即时广播）
                with _recording_lock:
                    still_active = bool(_recording_sessions.get(udid, {}).get("active"))
                if still_active and not poll_result.get("recording_active", True):
                    broadcast_event(
                        "recording_stopped",
                        {"udid": udid, "message": "设备端已停止录制"},
                    )
                    with _recording_lock:
                        if udid in _recording_sessions:
                            _recording_sessions[udid]["active"] = False
                    break

                # 截图：有新步骤或开启截图时更新
                if device_steps and screenshot_per_step:
                    try:
                        img, _ = plugin_rpc.take_screenshot(udid)
                        if img:
                            broadcast_event("screenshot_update", {
                                "udid": udid,
                                "screenshot_base64": base64.b64encode(img).decode("ascii"),
                                "step_count": len(gestures),
                            })
                    except Exception:
                        pass
            except Exception as exc:
                broadcast_event("error", {"udid": udid, "error": str(exc)})
                consecutive_errors += 1
                if consecutive_errors > 10:
                    with _recording_lock:
                        if udid in _recording_sessions:
                            _recording_sessions[udid]["active"] = False
                    break
                continue
            time.sleep(0.08)

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
