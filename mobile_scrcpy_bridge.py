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
_bridge_failed_msg: Optional[str] = None
_bridge_listening: bool = False
_active_sessions: Dict[str, "ScrcpyWsSession"] = {}
_clients_by_serial: Dict[str, Set[Any]] = {}


def _bridge_host() -> str:
    return (os.environ.get("MOBILE_SCRCPY_BRIDGE_HOST") or "127.0.0.1").strip() or "127.0.0.1"


def _tcp_port_in_use(host: str, port: int) -> bool:
    """
    检测端口是否已被占用。勿对 WebSocket 服务做 create_connection 探测——
    裸 TCP 连上即断开会触发 websockets「opening handshake failed」错误日志。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 勿设 SO_REUSEADDR：在 Windows 上可能对已 listen 的端口 bind 仍成功，导致误判为空闲
        sock.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def _bridge_ready() -> bool:
    return bool(_bridge_thread and _bridge_thread.is_alive() and _bridge_listening)


def _service_listening(host: str, port: int) -> bool:
    if _bridge_ready():
        return True
    return _tcp_port_in_use(host, port)


def _quiet_websockets_probe_errors() -> None:
    """屏蔽对 WS 端口做裸 TCP 探测时的握手失败堆栈（不影响真实客户端错误）。"""
    import logging

    class _Filter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            if "opening handshake failed" in msg or "did not receive a valid HTTP request" in msg:
                return False
            if record.exc_info and record.exc_info[1] is not None:
                exc_name = type(record.exc_info[1]).__name__
                if exc_name in ("InvalidMessage", "EOFError"):
                    return False
            return True

    for name in ("websockets.server", "websockets"):
        logging.getLogger(name).addFilter(_Filter())


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
    host = _bridge_host()
    port = scrcpy_bridge_port()
    listening = _service_listening(host, port)
    return {
        "bridge_running": listening,
        "bridge_thread_alive": _bridge_thread is not None and _bridge_thread.is_alive(),
        "bridge_port": port,
        "bridge_host": host,
        "bridge_port_listening": listening,
        "bridge_error": _bridge_failed_msg,
        "scrcpy_server_jar": jar or "",
        "scrcpy_server_ready": bool(jar),
        "active_serials": list(_active_sessions.keys()),
    }


def ensure_bridge_started() -> Tuple[bool, str]:
    global _bridge_thread, _bridge_loop, _bridge_failed_msg, _bridge_listening
    if not _find_scrcpy_server_jar():
        return False, "未找到 scrcpy-server，请安装 scrcpy 完整包"
    host = _bridge_host()
    port = scrcpy_bridge_port()

    with _lock:
        if _bridge_ready():
            return True, "bridge 已在运行"
        if _tcp_port_in_use(host, port) and not (_bridge_thread and _bridge_thread.is_alive()):
            return (
                True,
                f"scrcpy bridge 端口 {port} 已有服务在监听（可能为其它 app.py 实例），将直接复用",
            )
        if _bridge_failed_msg and not (_bridge_thread and _bridge_thread.is_alive()):
            return False, _bridge_failed_msg

        _bridge_failed_msg = None
        _bridge_listening = False

        def _run() -> None:
            global _bridge_loop, _bridge_failed_msg, _bridge_listening
            try:
                import websockets
            except ImportError:
                _bridge_failed_msg = "websockets 未安装，请 pip install websockets>=12"
                uat_logger.error(_bridge_failed_msg)
                return

            _quiet_websockets_probe_errors()

            async def _main() -> None:
                global _bridge_listening
                async with websockets.serve(
                    _ws_handler,
                    host,
                    port,
                    max_size=16 * 1024 * 1024,
                    ping_interval=20,
                ):
                    with _lock:
                        _bridge_listening = True
                    uat_logger.info("scrcpy WebSocket bridge 监听 ws://%s:%s", host, port)
                    try:
                        await asyncio.Future()
                    finally:
                        with _lock:
                            _bridge_listening = False

            _bridge_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_bridge_loop)
            try:
                _bridge_loop.run_until_complete(_main())
            except OSError as e:
                win_in_use = getattr(e, "winerror", None) == 10048 or e.errno in (10048, 98, 48)
                if win_in_use or "already in use" in str(e).lower() or "只允许使用一次" in str(e):
                    _bridge_failed_msg = (
                        f"无法绑定 ws://{host}:{port}：端口已被占用。"
                        "请只保留一个 python app.py，或修改 .env 中 MOBILE_SCRCPY_BRIDGE_PORT。"
                    )
                else:
                    _bridge_failed_msg = f"scrcpy bridge 启动失败：{e}"
                uat_logger.error(_bridge_failed_msg)
            except Exception as e:
                _bridge_failed_msg = f"scrcpy bridge 启动失败：{e}"
                uat_logger.exception("scrcpy bridge")

        _bridge_thread = threading.Thread(target=_run, name="scrcpy-bridge", daemon=True)
        _bridge_thread.start()

    for _ in range(24):
        if _bridge_ready():
            return True, f"bridge 已就绪（ws://{host}:{port}）"
        if _bridge_thread and not _bridge_thread.is_alive():
            break
        time.sleep(0.15)

    with _lock:
        if _bridge_failed_msg:
            return False, _bridge_failed_msg
    return False, f"scrcpy bridge 启动超时，请检查端口 {port} 是否被占用"
