# -*- coding: utf-8 -*-
"""浏览器扩展 WebSocket 桥。"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, Dict, Optional, Set

_bridge_lock = threading.Lock()
_bridge_state: Dict[str, Any] = {
    "started": False,
    "port": 0,
    "session_token": "",
    "clients": 0,
    "last_extension_pick": None,
    "arm_request": None,
    "toolbar_request": None,
    "disarm_request": False,
    "hide_toolbar_request": False,
}
_server_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_ws_clients: Set[Any] = set()


def _ws_port() -> int:
    return int(os.environ.get("WEB_CAPTURE_EXT_WS_PORT", "19222") or 19222)


def ensure_bridge_started(*, session_id: str = "") -> Dict[str, Any]:
    global _server_thread
    port = _ws_port()
    with _bridge_lock:
        _bridge_state["session_token"] = session_id or _bridge_state.get("session_token") or ""
        if _bridge_state.get("started"):
            return {
                "success": True,
                "ws_port": port,
                "message": "扩展桥接已在运行",
            }
        _bridge_state["port"] = port
        _bridge_state["started"] = True

    if _server_thread is None or not _server_thread.is_alive():
        _server_thread = threading.Thread(target=_run_ws_server, args=(port,), daemon=True)
        _server_thread.start()

    return {
        "success": True,
        "ws_port": port,
        "ws_url": f"ws://127.0.0.1:{port}",
        "message": "扩展桥接已启动，请在浏览器中启用 UAT 助手",
    }


def get_extension_status() -> Dict[str, Any]:
    with _bridge_lock:
        clients = int(_bridge_state.get("clients") or 0)
        return {
            "success": True,
            "bridge_running": bool(_bridge_state.get("started")),
            "ws_port": int(_bridge_state.get("port") or _ws_port()),
            "connected_clients": clients,
            "extension_connected": clients > 0,
        }


def consume_extension_pick() -> Optional[Dict[str, Any]]:
    with _bridge_lock:
        payload = _bridge_state.get("last_extension_pick")
        _bridge_state["last_extension_pick"] = None
    if isinstance(payload, dict):
        return payload
    return None


def broadcast_arm(*, api_base: str, session_id: str) -> Dict[str, Any]:
    msg = {
        "type": "arm_picker",
        "api_base": (api_base or "").rstrip("/"),
        "session_id": session_id,
    }
    with _bridge_lock:
        _bridge_state["arm_request"] = msg
        _bridge_state["disarm_request"] = False
    _schedule_broadcast(msg)
    return {"success": True, "message": "已向浏览器扩展发送捕获指令"}


def broadcast_show_toolbar(*, api_base: str, session_id: str) -> Dict[str, Any]:
    msg = {
        "type": "show_toolbar",
        "api_base": (api_base or "").rstrip("/"),
        "session_id": session_id,
    }
    with _bridge_lock:
        _bridge_state["toolbar_request"] = msg
        _bridge_state["hide_toolbar_request"] = False
    _schedule_broadcast(msg)
    return {"success": True, "message": "已向浏览器扩展发送悬浮窗指令"}


def broadcast_hide_toolbar() -> None:
    msg = {"type": "hide_toolbar"}
    with _bridge_lock:
        _bridge_state["hide_toolbar_request"] = True
        _bridge_state["toolbar_request"] = None
    _schedule_broadcast(msg)


def broadcast_disarm() -> None:
    msg = {"type": "disarm_picker"}
    with _bridge_lock:
        _bridge_state["disarm_request"] = True
        _bridge_state["arm_request"] = None
    _schedule_broadcast(msg)


def _schedule_broadcast(msg: Dict[str, Any]) -> None:
    loop = _loop
    if loop is None or not loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(_broadcast_json(msg), loop)


async def _broadcast_json(msg: Dict[str, Any]) -> None:
    raw = json.dumps(msg)
    dead = []
    for ws in list(_ws_clients):
        try:
            await ws.send(raw)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)
    with _bridge_lock:
        _bridge_state["clients"] = len(_ws_clients)


async def _maybe_push_pending(ws) -> None:
    with _bridge_lock:
        toolbar = _bridge_state.get("toolbar_request")
        arm = _bridge_state.get("arm_request")
        disarm = bool(_bridge_state.get("disarm_request"))
        hide_tb = bool(_bridge_state.get("hide_toolbar_request"))
    if toolbar:
        await ws.send(json.dumps(toolbar))
    if arm:
        await ws.send(json.dumps(arm))
    if disarm:
        await ws.send(json.dumps({"type": "disarm_picker"}))
    if hide_tb:
        await ws.send(json.dumps({"type": "hide_toolbar"}))


def _run_ws_server(port: int) -> None:
    global _loop
    try:
        import websockets
    except ImportError:
        with _bridge_lock:
            _bridge_state["started"] = False
        return

    async def handler(ws):
        _ws_clients.add(ws)
        with _bridge_lock:
            _bridge_state["clients"] = len(_ws_clients)
        try:
            await _maybe_push_pending(ws)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")
                if mtype == "pick" and isinstance(msg.get("payload"), dict):
                    with _bridge_lock:
                        _bridge_state["last_extension_pick"] = msg["payload"]
                elif mtype == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                    await _maybe_push_pending(ws)
        finally:
            _ws_clients.discard(ws)
            with _bridge_lock:
                _bridge_state["clients"] = len(_ws_clients)

    async def main():
        global _loop
        _loop = asyncio.get_running_loop()
        async with websockets.serve(handler, "127.0.0.1", port):
            await asyncio.Future()

    try:
        asyncio.run(main())
    except Exception:
        with _bridge_lock:
            _bridge_state["started"] = False


def get_extension_install_dir() -> str:
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    local = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "NewUITestPlatform",
        "extensions",
        "chrome",
    )
    if os.path.isdir(local):
        return local
    return os.path.join(pf, "NewUITestPlatform", "extensions", "chrome")
