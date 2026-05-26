# -*- coding: utf-8 -*-
"""浏览器扩展 WebSocket 桥（Phase 2）。"""

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
    "clients": set(),
    "last_extension_pick": None,
}
_server_thread: Optional[threading.Thread] = None


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
        clients = len(_bridge_state.get("clients") or [])
        return {
            "success": True,
            "bridge_running": bool(_bridge_state.get("started")),
            "ws_port": int(_bridge_state.get("port") or _ws_port()),
            "connected_clients": clients,
            "extension_connected": clients > 0,
        }


def _run_ws_server(port: int) -> None:
    try:
        import websockets
    except ImportError:
        return

    clients: Set[Any] = set()

    async def handler(ws):
        clients.add(ws)
        with _bridge_lock:
            _bridge_state["clients"] = set(clients)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "pick" and isinstance(msg.get("payload"), dict):
                    with _bridge_lock:
                        _bridge_state["last_extension_pick"] = msg["payload"]
                elif msg.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
        finally:
            clients.discard(ws)
            with _bridge_lock:
                _bridge_state["clients"] = set(clients)

    async def main():
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
