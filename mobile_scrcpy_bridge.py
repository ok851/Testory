# -*- coding: utf-8 -*-
"""
scrcpy 视频 WebSocket 桥接（模拟器高帧率画布投屏）。

将 scrcpy-server H.264 帧通过 WebSocket 推送到浏览器 WebCodecs 解码。
真机仍走 screencap 降级路径。
"""

from __future__ import annotations

import asyncio
import os
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from mobile_env_config import adb_path, mirror_fps, scrcpy_bridge_port, scrcpy_path

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_bridge_thread: Optional[threading.Thread] = None
_bridge_loop: Optional[asyncio.AbstractEventLoop] = None
_active_sessions: Dict[str, "ScrcpyWsSession"] = {}
_clients_by_serial: Dict[str, Set[Any]] = {}


def _find_scrcpy_server_jar() -> Optional[str]:
    """定位 scrcpy-server（与 scrcpy 可执行文件同目录或 vendor）。"""
    candidates: list[Path] = []
    exe = scrcpy_path()
    if exe and exe not in ("scrcpy", "scrcpy.exe"):
        p = Path(exe).resolve().parent
        candidates.extend(p.glob("scrcpy-server*"))
    root = Path(__file__).resolve().parent
    candidates.extend((root / "static" / "vendor").glob("scrcpy-server*"))
    for c in candidates:
        if c.is_file() and c.stat().st_size > 1000:
            return str(c)
    return None


def _run_adb(serial: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = [adb_path()]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)


class ScrcpyDeviceSession:
    """单设备 scrcpy-server 会话（H.264 over TCP）。"""

    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.local_port = 27183 + (hash(serial) % 500)
        self._socket: Optional[socket.socket] = None
        self._shell_proc: Optional[subprocess.Popen] = None
        self._server_jar = _find_scrcpy_server_jar()
        self._version = "3.1"
        self.running = False

    def start(self) -> None:
        if not self._server_jar:
            raise RuntimeError(
                "未找到 scrcpy-server。请安装 scrcpy 并将 scrcpy-server 置于 scrcpy.exe 同目录，"
                "或复制到 static/vendor/scrcpy-server"
            )
        serial = self.serial
        remote = "/data/local/tmp/scrcpy-server.jar"
        push = _run_adb(serial, "push", self._server_jar, remote, timeout=60)
        if push.returncode != 0:
            err = (push.stderr or push.stdout or b"").decode("utf-8", errors="replace")
            raise RuntimeError(f"推送 scrcpy-server 失败：{err}")

        _run_adb(serial, "forward", "--remove", f"tcp:{self.local_port}")
        fwd = _run_adb(serial, "forward", f"tcp:{self.local_port}", "localabstract:scrcpy")
        if fwd.returncode != 0:
            err = (fwd.stderr or fwd.stdout or b"").decode("utf-8", errors="replace")
            raise RuntimeError(f"adb forward 失败：{err}")

        max_fps = mirror_fps()
        server_args = [
            f"max_fps={max_fps}",
            "tunnel_forward=true",
            "control=false",
            "audio=false",
            "show_touches=false",
            "send_frame_meta=true",
            "log_level=error",
        ]
        shell_cmd = (
            f"CLASSPATH={remote} app_process / com.genymobile.scrcpy.Server "
            f"{self._version} {' '.join(server_args)}"
        )
        self._shell_proc = subprocess.Popen(
            [adb_path(), "-s", serial, "shell", shell_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
        )
        time.sleep(0.4)
        sock = socket.create_connection(("127.0.0.1", self.local_port), timeout=8)
        sock.settimeout(10.0)
        dummy = sock.recv(1)
        if not dummy:
            sock.close()
            raise RuntimeError("scrcpy 连接握手失败")
        device_name = self._read_exact(sock, 64)
        uat_logger.info(
            "scrcpy 已连接: serial=%s device=%s",
            serial,
            device_name.split(b"\x00")[0].decode("utf-8", errors="replace"),
        )
        self._socket = sock
        self.running = True

    def _read_exact(self, sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("scrcpy socket 已关闭")
            buf += chunk
        return buf

    def read_packet(self) -> Optional[bytes]:
        sock = self._socket
        if not sock or not self.running:
            return None
        try:
            header = self._read_exact(sock, 12)
            pts, size = struct.unpack(">QI", header)
            if size <= 0 or size > 10_000_000:
                return None
            payload = self._read_exact(sock, size)
            return payload
        except Exception as exc:
            uat_logger.debug("scrcpy 读帧结束 serial=%s: %s", self.serial, exc)
            self.running = False
            return None

    def stop(self) -> None:
        self.running = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        if self._shell_proc:
            try:
                self._shell_proc.terminate()
                self._shell_proc.wait(timeout=2)
            except Exception:
                try:
                    self._shell_proc.kill()
                except Exception:
                    pass
            self._shell_proc = None
        try:
            _run_adb(self.serial, "forward", "--remove", f"tcp:{self.local_port}")
        except Exception:
            pass


class ScrcpyWsSession:
    """WebSocket 客户端组共享一个 scrcpy 设备会话。"""

    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.device = ScrcpyDeviceSession(serial)
        self.task: Optional[asyncio.Task] = None

    async def start_relay(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.device.start)
        self.task = asyncio.create_task(self._relay_loop())

    async def _relay_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self.device.running:
            packet = await loop.run_in_executor(None, self.device.read_packet)
            if not packet:
                break
            clients = list(_clients_by_serial.get(self.serial, set()))
            dead = []
            for ws in clients:
                try:
                    await ws.send(packet)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _clients_by_serial.get(self.serial, set()).discard(ws)
        self.device.stop()

    async def stop(self) -> None:
        self.device.stop()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


async def _ws_handler(websocket: Any) -> None:
    path = ""
    try:
        path = websocket.request.path  # websockets >= 11
    except Exception:
        path = getattr(websocket, "path", "") or ""
    serial = "emulator-5554"
    if "?" in path:
        qs = path.split("?", 1)[1]
        for part in qs.split("&"):
            if part.startswith("serial="):
                serial = part.split("=", 1)[1]
    serial = serial.strip() or "emulator-5554"

    _clients_by_serial.setdefault(serial, set()).add(websocket)
    try:
        await websocket.send('{"type":"ready","serial":"' + serial + '"}')
        with _lock:
            sess = _active_sessions.get(serial)
            if not sess:
                sess = ScrcpyWsSession(serial)
                _active_sessions[serial] = sess
                await sess.start_relay()
        while True:
            try:
                await websocket.recv()
            except Exception:
                break
    finally:
        _clients_by_serial.get(serial, set()).discard(websocket)
        if not _clients_by_serial.get(serial):
            with _lock:
                old = _active_sessions.pop(serial, None)
            if old:
                await old.stop()


def bridge_health() -> Dict[str, Any]:
    jar = _find_scrcpy_server_jar()
    return {
        "bridge_running": _bridge_thread is not None and _bridge_thread.is_alive(),
        "bridge_port": scrcpy_bridge_port(),
        "scrcpy_server_jar": jar or "",
        "scrcpy_server_ready": bool(jar),
        "active_serials": list(_active_sessions.keys()),
    }


def ensure_bridge_started() -> Tuple[bool, str]:
    global _bridge_thread, _bridge_loop
    if not _find_scrcpy_server_jar():
        return False, "未找到 scrcpy-server，请安装 scrcpy 完整包"
    with _lock:
        if _bridge_thread and _bridge_thread.is_alive():
            return True, "bridge 已在运行"

        def _run() -> None:
            global _bridge_loop
            try:
                import websockets
            except ImportError:
                uat_logger.error("websockets 未安装，无法启动 scrcpy bridge")
                return

            async def _main() -> None:
                port = scrcpy_bridge_port()
                async with websockets.serve(
                    _ws_handler,
                    "127.0.0.1",
                    port,
                    max_size=16 * 1024 * 1024,
                    ping_interval=20,
                ):
                    uat_logger.info("scrcpy WebSocket bridge 监听 ws://127.0.0.1:%s", port)
                    await asyncio.Future()

            _bridge_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_bridge_loop)
            _bridge_loop.run_until_complete(_main())

        _bridge_thread = threading.Thread(target=_run, name="scrcpy-bridge", daemon=True)
        _bridge_thread.start()
    time.sleep(0.3)
    return True, "bridge 已启动"
