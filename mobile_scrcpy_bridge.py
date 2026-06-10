# -*- coding: utf-8 -*-
"""
scrcpy 视频 WebSocket 桥接（模拟器高帧率画布投屏）。

将 scrcpy-server H.264 帧通过 WebSocket 推送到浏览器 WebCodecs 解码。
真机仍走 screencap 降级路径。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from mobile_env_config import adb_path, scrcpy_bridge_port, scrcpy_mirror_fps, scrcpy_path

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
_http_stream_locks: Dict[str, threading.Lock] = {}


def _bridge_bind_host() -> str:
    """WebSocket 桥监听地址（默认 0.0.0.0，便于局域网浏览器连接）。"""
    return (os.environ.get("MOBILE_SCRCPY_BRIDGE_BIND") or "0.0.0.0").strip() or "0.0.0.0"


def _bridge_host() -> str:
    return _bridge_bind_host()


def _stable_serial_port(serial: str) -> int:
    """跨进程稳定的 adb forward 端口（勿用 hash()，Python 会随机盐）。"""
    key = (serial or "emulator-5554").strip() or "emulator-5554"
    bucket = zlib.crc32(key.encode("utf-8")) & 0xFFFFFFFF
    return 27183 + (bucket % 500)


def _scrcpy_control_enabled() -> bool:
    raw = (os.environ.get("MOBILE_SCRCPY_CONTROL") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


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


_SC_CONTROL_MSG_INJECT_TOUCH_EVENT = 2
_AMOTION_EVENT_ACTION_DOWN = 0
_AMOTION_EVENT_ACTION_UP = 1
_AMOTION_EVENT_ACTION_MOVE = 2
_POINTER_ID_GENERIC = (1 << 64) - 1


def _vendor_scrcpy_dir() -> Path:
    return Path(__file__).resolve().parent / "static" / "vendor"


def _sync_scrcpy_server_to_vendor() -> None:
    """将插件目录中的 scrcpy-server 同步到 static/vendor，便于打包与降级检测。"""
    vendor = _vendor_scrcpy_dir()
    if list(vendor.glob("scrcpy-server*")):
        return
    sources: list[Path] = []
    exe = scrcpy_path()
    if exe and exe not in ("scrcpy", "scrcpy.exe"):
        sources.extend(Path(exe).resolve().parent.glob("scrcpy-server*"))
    try:
        from mobile_scrcpy_bundles import scrcpy_install_dir

        sources.extend(scrcpy_install_dir().glob("scrcpy-server*"))
    except Exception:
        pass
    for src in sources:
        if src.is_file() and src.stat().st_size > 1000:
            try:
                vendor.mkdir(parents=True, exist_ok=True)
                dest = vendor / src.name
                if not dest.is_file():
                    shutil.copy2(src, dest)
                    uat_logger.info("已同步 scrcpy-server 到 %s", dest)
            except Exception as exc:
                uat_logger.debug("同步 scrcpy-server 失败: %s", exc)
            return


def _find_scrcpy_server_jar() -> Optional[str]:
    """定位 scrcpy-server（与 scrcpy 可执行文件同目录或 vendor）。"""
    _sync_scrcpy_server_to_vendor()
    candidates: list[Path] = []
    exe = scrcpy_path()
    if exe and exe not in ("scrcpy", "scrcpy.exe"):
        p = Path(exe).resolve().parent
        candidates.extend(p.glob("scrcpy-server*"))
    try:
        from mobile_scrcpy_bundles import scrcpy_install_dir

        candidates.extend(scrcpy_install_dir().glob("scrcpy-server*"))
    except Exception:
        pass
    candidates.extend(_vendor_scrcpy_dir().glob("scrcpy-server*"))
    seen: set[str] = set()
    for c in candidates:
        key = str(c.resolve()) if c.exists() else str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.is_file() and c.stat().st_size > 1000:
            return str(c)
    return None


def _run_adb(serial: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = [adb_path()]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)


def _scrcpy_server_version() -> str:
    cached = getattr(_scrcpy_server_version, "_cache", None)
    if cached:
        return cached
    versions: list[str] = []
    exe = scrcpy_path()
    if exe and exe not in ("scrcpy", "scrcpy.exe") and Path(exe).is_file():
        try:
            r = subprocess.run(
                [exe, "--version"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            text = (r.stdout or "") + (r.stderr or "")
            m = re.search(r"([\d]+\.[\d]+(?:\.[\d]+)?)", text)
            if m:
                versions.append(m.group(1))
        except Exception:
            pass
    jar = _find_scrcpy_server_jar() or ""
    m = re.search(r"scrcpy-server[-_]?v?([\d.]+)", jar, re.I)
    if m:
        versions.append(m.group(1))
    ver = versions[0] if versions else "2.4"
    _scrcpy_server_version._cache = ver
    return ver


def _version_candidates() -> list[str]:
    primary = _scrcpy_server_version()
    out: list[str] = []
    for v in (primary, "2.4", "2.1", "3.1", "3.0"):
        if v and v not in out:
            out.append(v)
    return out


def iter_scrcpy_http_stream(serial: str):
    """
    经 Flask 同源 HTTP 输出 H.264（uint32 长度前缀 + payload）。
    避免浏览器连独立 8767 端口失败（局域网/防火墙）。
    同一 serial 仅允许一条 HTTP 流，防止重复拉起 scrcpy-server 拖垮模拟器。
    """
    serial = (serial or "").strip()
    if not serial:
        return
    if not _find_scrcpy_server_jar():
        return
    with _lock:
        stream_lock = _http_stream_locks.setdefault(serial, threading.Lock())
    if not stream_lock.acquire(blocking=False):
        uat_logger.warning("scrcpy HTTP 流已在输出 serial=%s，跳过重复会话", serial)
        return
    sess = ScrcpyDeviceSession(serial)
    try:
        sess.start()
        while sess.running:
            packet = sess.read_packet()
            if not packet:
                break
            yield struct.pack(">I", len(packet)) + packet
    except Exception as exc:
        uat_logger.warning("scrcpy HTTP 流结束 serial=%s: %s", serial, exc)
    finally:
        sess.stop()
        stream_lock.release()


def warm_scrcpy_session(serial: str, *, timeout: float = 20.0) -> Tuple[bool, str]:
    """验证 scrcpy 能收到视频帧（失败则前端应降级 screencap）。"""
    serial = (serial or "").strip()
    if not serial:
        return False, "缺少 serial"
    if not _find_scrcpy_server_jar():
        return False, "未找到 scrcpy-server"
    last_err = ""
    for attempt in range(2):
        sess = ScrcpyDeviceSession(serial)
        try:
            sess.start()
            deadline = time.time() + max(5.0, timeout)
            while time.time() < deadline:
                packet = sess.read_packet()
                if packet and len(packet) > 32:
                    return True, "ok"
                if not sess.running:
                    break
                time.sleep(0.12)
            last_err = "scrcpy 长时间无视频帧（模拟器可能尚未完成启动）"
        except Exception as exc:
            last_err = str(exc) or "scrcpy 预热失败"
        finally:
            sess.stop()
        if attempt == 0:
            time.sleep(1.0)
    return False, last_err or "scrcpy 预热失败"


class ScrcpyDeviceSession:
    """单设备 scrcpy-server 会话（H.264 over TCP）。"""

    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.local_port = _stable_serial_port(serial)
        self._socket: Optional[socket.socket] = None
        self._control_socket: Optional[socket.socket] = None
        self._shell_proc: Optional[subprocess.Popen] = None
        self._server_jar = _find_scrcpy_server_jar()
        self._version = _scrcpy_server_version()
        self.running = False
        self._control_lock = threading.Lock()

    def start(self) -> None:
        if not self._server_jar:
            raise RuntimeError(
                "未找到 scrcpy-server。请安装 scrcpy 并将 scrcpy-server 置于 scrcpy.exe 同目录，"
                "或复制到 static/vendor/scrcpy-server"
            )
        last_err: Optional[Exception] = None
        for ver in _version_candidates():
            self._version = ver
            try:
                self._start_once()
                return
            except Exception as exc:
                last_err = exc
                self.stop()
        raise RuntimeError(str(last_err) if last_err else "scrcpy 启动失败")

    def _start_once(self) -> None:
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

        max_fps = scrcpy_mirror_fps()
        server_args = [
            f"max_fps={max_fps}",
            "tunnel_forward=true",
            f"control={'true' if _scrcpy_control_enabled() else 'false'}",
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
        time.sleep(2.0)
        sock = socket.create_connection(("127.0.0.1", self.local_port), timeout=12)
        sock.settimeout(20.0)
        dummy = sock.recv(1)
        if not dummy:
            sock.close()
            raise RuntimeError("scrcpy 连接握手失败（请点「停止」后重新启动模拟器）")
        device_name = self._read_exact(sock, 64)
        uat_logger.info(
            "scrcpy 已连接: serial=%s version=%s device=%s",
            serial,
            self._version,
            device_name.split(b"\x00")[0].decode("utf-8", errors="replace"),
        )
        self._socket = sock
        if _scrcpy_control_enabled():
            try:
                ctrl = socket.create_connection(("127.0.0.1", self.local_port), timeout=8)
                ctrl.settimeout(5.0)
                self._control_socket = ctrl
            except Exception as exc:
                uat_logger.warning("scrcpy 控制通道未连接 serial=%s: %s", serial, exc)
                self._control_socket = None
        self.running = True

    def _inject_touch_event(
        self,
        action: int,
        x: int,
        y: int,
        *,
        screen_width: int,
        screen_height: int,
        pressure: int = 0xFFFF,
        buttons: int = 0,
    ) -> bool:
        sock = self._control_socket
        if not sock:
            return False
        pressure_val = pressure if action == _AMOTION_EVENT_ACTION_DOWN else 0
        btn_val = buttons if action != _AMOTION_EVENT_ACTION_UP else 0
        msg = struct.pack(
            ">BBQIIHHHII",
            _SC_CONTROL_MSG_INJECT_TOUCH_EVENT,
            action,
            _POINTER_ID_GENERIC,
            max(0, int(x)) << 16,
            max(0, int(y)) << 16,
            max(1, int(screen_width)),
            max(1, int(screen_height)),
            pressure_val,
            1,
            btn_val,
        )
        with self._control_lock:
            try:
                sock.sendall(msg)
                return True
            except Exception as exc:
                uat_logger.debug("scrcpy 注入触控失败 serial=%s: %s", self.serial, exc)
                return False

    def inject_tap(self, x: int, y: int, *, screen_width: int, screen_height: int) -> bool:
        if not self._inject_touch_event(
            _AMOTION_EVENT_ACTION_DOWN,
            x,
            y,
            screen_width=screen_width,
            screen_height=screen_height,
            buttons=1,
        ):
            return False
        time.sleep(0.02)
        return self._inject_touch_event(
            _AMOTION_EVENT_ACTION_UP,
            x,
            y,
            screen_width=screen_width,
            screen_height=screen_height,
        )

    def inject_swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        screen_width: int,
        screen_height: int,
        steps: int = 8,
    ) -> bool:
        if not self._inject_touch_event(
            _AMOTION_EVENT_ACTION_DOWN,
            x1,
            y1,
            screen_width=screen_width,
            screen_height=screen_height,
            buttons=1,
        ):
            return False
        count = max(2, int(steps))
        for i in range(1, count):
            t = i / count
            mx = int(x1 + (x2 - x1) * t)
            my = int(y1 + (y2 - y1) * t)
            if not self._inject_touch_event(
                _AMOTION_EVENT_ACTION_MOVE,
                mx,
                my,
                screen_width=screen_width,
                screen_height=screen_height,
                buttons=1,
            ):
                return False
            time.sleep(0.012)
        if not self._inject_touch_event(
            _AMOTION_EVENT_ACTION_UP,
            x2,
            y2,
            screen_width=screen_width,
            screen_height=screen_height,
        ):
            return False
        return True

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
        if self._control_socket:
            try:
                self._control_socket.close()
            except Exception:
                pass
            self._control_socket = None
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
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                await loop.run_in_executor(None, self.device.start)
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                self.device.stop()
                if attempt < 2:
                    await asyncio.sleep(1.2 * (attempt + 1))
        if last_err is not None:
            raise last_err
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


def _handle_ws_control_message(serial: str, raw: str) -> None:
    """处理浏览器经 WebSocket 发来的触控/滑动（scrcpy 控制通道）。"""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(msg, dict):
        return
    mtype = (msg.get("type") or "").strip().lower()
    if mtype not in ("tap", "swipe", "touch"):
        return
    with _lock:
        sess = _active_sessions.get(serial)
    if not sess or not sess.device.running:
        return
    dev = sess.device
    sw = max(1, int(msg.get("screen_width") or msg.get("w") or 1080))
    sh = max(1, int(msg.get("screen_height") or msg.get("h") or 1920))
    if mtype == "swipe":
        ok = dev.inject_swipe(
            int(msg.get("x1") or 0),
            int(msg.get("y1") or 0),
            int(msg.get("x2") or 0),
            int(msg.get("y2") or 0),
            screen_width=sw,
            screen_height=sh,
        )
    else:
        ok = dev.inject_tap(
            int(msg.get("x") or 0),
            int(msg.get("y") or 0),
            screen_width=sw,
            screen_height=sh,
        )
    if not ok:
        uat_logger.debug("scrcpy_ws 触控未送达 serial=%s type=%s", serial, mtype)


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
                raw = await websocket.recv()
            except Exception:
                break
            if isinstance(raw, str):
                _handle_ws_control_message(serial, raw)
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
    server_ready = bool(jar)
    if server_ready:
        if listening:
            message = "就绪（投屏桥运行中）"
        else:
            message = "就绪（连接设备时自动启动）"
    elif _bridge_failed_msg:
        message = _bridge_failed_msg
    else:
        message = "未就绪（可选：插件市场安装 scrcpy）"
    return {
        "ok": server_ready,
        "message": message,
        "bridge_running": listening,
        "bridge_thread_alive": _bridge_thread is not None and _bridge_thread.is_alive(),
        "bridge_port": port,
        "bridge_host": host,
        "bridge_port_listening": listening,
        "bridge_error": _bridge_failed_msg,
        "scrcpy_server_jar": jar or "",
        "scrcpy_server_ready": server_ready,
        "scrcpy_path": scrcpy_path(),
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


def stop_all_bridge_sessions() -> None:
    """退出时停止所有 scrcpy WebSocket 会话。"""
    with _lock:
        sessions = list(_active_sessions.values())
        _active_sessions.clear()
        _clients_by_serial.clear()
    for sess in sessions:
        try:
            sess.device.stop()
        except Exception:
            pass
