# -*- coding: utf-8 -*-
"""
scrcpy 视频 WebSocket 桥接（真机高帧率画布投屏）。

将 scrcpy-server H.264 帧通过 WebSocket 推送到浏览器 WebCodecs 解码。
未安装 scrcpy 时由 adb screencap 降级。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import zlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Set, Tuple, Union

_SC_PACKET_PTS_MASK = 0x3FFFFFFFFFFFFFFF
_SC_PACKET_FLAG_CONFIG = 1 << 63
_SC_PACKET_FLAG_KEY_FRAME = 1 << 62


@dataclass(frozen=True)
class ScrcpyPacket:
    """scrcpy-server 单帧 H.264 载荷及 send_frame_meta 标志。"""

    payload: bytes
    is_config: bool = False
    is_key: bool = False

    @property
    def frame_meta(self) -> int:
        if self.is_config:
            return 1
        if self.is_key:
            return 2
        return 0


def pack_scrcpy_frame(packet: Union[ScrcpyPacket, bytes]) -> bytes:
    """浏览器统一帧格式：[meta:u8][len:u32][payload]。"""
    if isinstance(packet, ScrcpyPacket):
        meta = packet.frame_meta
        payload = packet.payload
    else:
        meta = 0
        payload = packet
    return struct.pack(">BI", meta, len(payload)) + payload

from mobile_env_config import adb_path, scrcpy_bridge_port, scrcpy_max_size, scrcpy_mirror_fps, scrcpy_path

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
_persistent_sessions: Dict[str, "ScrcpyDeviceSession"] = {}
_persistent_lock = threading.Lock()
_relays: Dict[str, "ScrcpyFrameRelay"] = {}
_relay_lock = threading.Lock()


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


def _bridge_connect_host(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "", "*") else host


def _foreign_bridge_healthy(host: str, port: int) -> bool:
    """端口被占用时，确认是否为可用的 WebSocket bridge（非裸 TCP 误占）。"""
    check_host = _bridge_connect_host(host)
    sock: Optional[socket.socket] = None
    try:
        sock = socket.create_connection((check_host, port), timeout=2)
        sock.settimeout(2)
        sock.sendall(
            b"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        data = sock.recv(128)
        return b"101" in data or b"Upgrade" in data
    except OSError:
        return False
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


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


def find_scrcpy_server_jar() -> Optional[str]:
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


def _find_scrcpy_server_jar() -> Optional[str]:
    return find_scrcpy_server_jar()


def _parse_version_from_jar_path(jar_path: str) -> Optional[str]:
    m = re.search(r"scrcpy-server[-_]?v?([\d.]+)", jar_path or "", re.I)
    return m.group(1) if m else None


def _exe_scrcpy_version() -> Optional[str]:
    exe = scrcpy_path()
    if not exe or exe in ("scrcpy", "scrcpy.exe") or not Path(exe).is_file():
        return None
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
        return m.group(1) if m else None
    except Exception:
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
    jar = _find_scrcpy_server_jar() or ""
    jar_ver = _parse_version_from_jar_path(jar)
    exe_ver = _exe_scrcpy_version()
    # JAR 版本优先，避免 exe 3.x 与 bundled 2.4 server 不匹配
    if jar_ver:
        ver = jar_ver
    elif exe_ver:
        ver = exe_ver
    else:
        ver = "2.4"
    _scrcpy_server_version._cache = ver
    return ver


def _version_candidates() -> list[str]:
    primary = _scrcpy_server_version()
    out: list[str] = []
    if primary.startswith("3."):
        fallbacks = ("3.1", "3.0", "2.4")
    else:
        fallbacks = ("2.4", "2.1")
    for v in (primary, *fallbacks):
        if v and v not in out:
            out.append(v)
    return out


def scrcpy_warm_timeout() -> float:
    raw = (os.environ.get("MOBILE_SCRCPY_WARM_TIMEOUT") or "20").strip()
    try:
        return max(8.0, min(60.0, float(raw)))
    except ValueError:
        return 20.0


def scrcpy_mirror_diagnostics(udid: str = "") -> Dict[str, Any]:
    """供 mirror/status API 暴露的 scrcpy 诊断字段（不触发 warm）。"""
    from mobile_env_config import resolve_mirror_backend, scrcpy_available

    serial = (udid or "").strip()
    backend = resolve_mirror_backend(serial)
    jar = _find_scrcpy_server_jar() or ""
    diag: Dict[str, Any] = {
        "mirror_backend_selected": backend,
        "scrcpy_available": scrcpy_available(),
        "scrcpy_server_jar": jar,
        "scrcpy_server_version": _scrcpy_server_version(),
        "version_candidates": _version_candidates(),
        "scrcpy_warm_timeout_sec": scrcpy_warm_timeout(),
        "scrcpy_session_active": False,
        "mirror_fallback_reason": "",
    }
    if serial:
        sess = _get_persistent_device(serial)
        diag["scrcpy_session_active"] = bool(sess and sess.running)
    if backend != "scrcpy_ws":
        if not scrcpy_available():
            diag["mirror_fallback_reason"] = "scrcpy 不可用（未找到 scrcpy.exe 或 scrcpy-server）"
        return diag
    if not jar:
        diag["mirror_fallback_reason"] = "未找到 scrcpy-server，已降级为截图投屏"
    return diag


def ensure_scrcpy_device_session(serial: str) -> Tuple[Optional["ScrcpyDeviceSession"], str]:
    """获取或启动持久 scrcpy-server 会话（同一设备复用，避免重复拉起）。"""
    serial = (serial or "").strip()
    if not serial:
        return None, "缺少 serial"
    if not _find_scrcpy_server_jar():
        return None, "未找到 scrcpy-server，请在插件市场安装 scrcpy 高帧率投屏"
    with _persistent_lock:
        existing = _persistent_sessions.get(serial)
        if existing and existing.running:
            return existing, ""
    sess = ScrcpyDeviceSession(serial)
    try:
        sess.start()
    except Exception as exc:
        err = str(exc) or "scrcpy 启动失败"
        uat_logger.warning("scrcpy 会话启动失败 serial=%s: %s", serial, err)
        return None, err
    with _persistent_lock:
        _persistent_sessions[serial] = sess
    return sess, ""


def stop_scrcpy_device_session(serial: str) -> None:
    """断开设备时释放 scrcpy-server 会话与帧广播。"""
    serial = (serial or "").strip()
    if not serial:
        return
    with _relay_lock:
        relay = _relays.pop(serial, None)
    if relay:
        relay._stopped = True
    with _persistent_lock:
        sess = _persistent_sessions.pop(serial, None)
    if sess:
        sess.stop()


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
        self._stderr_lines: Deque[str] = deque(maxlen=20)
        self._stderr_thread: Optional[threading.Thread] = None

    def _stderr_hint(self) -> str:
        if not self._stderr_lines:
            return ""
        return "; ".join(list(self._stderr_lines)[-5:])

    def _start_stderr_drain(self) -> None:
        proc = self._shell_proc
        if not proc or not proc.stderr:
            return
        self._stderr_lines.clear()

        def _drain() -> None:
            assert proc.stderr is not None
            try:
                for raw in proc.stderr:
                    text = raw.decode("utf-8", errors="replace").strip()
                    if text:
                        self._stderr_lines.append(text)
            except Exception:
                pass

        self._stderr_thread = threading.Thread(
            target=_drain,
            name=f"scrcpy-stderr-{self.serial}",
            daemon=True,
        )
        self._stderr_thread.start()

    def _wait_tcp_handshake(self, deadline: float) -> socket.socket:
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            proc = self._shell_proc
            if proc and proc.poll() is not None:
                hint = self._stderr_hint()
                msg = "scrcpy-server 进程已退出"
                if hint:
                    msg += f"：{hint}"
                raise RuntimeError(msg)
            try:
                sock = socket.create_connection(("127.0.0.1", self.local_port), timeout=2)
                sock.settimeout(20.0)
                dummy = sock.recv(1)
                if dummy:
                    return sock
                sock.close()
            except OSError as exc:
                last_err = exc
            time.sleep(0.35)
        hint = self._stderr_hint()
        msg = "scrcpy 连接握手失败：请确认手机已解锁，并重试连接"
        if hint:
            msg += f"（{hint}）"
        elif last_err:
            msg += f"（{last_err}）"
        raise RuntimeError(msg)

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
                hint = self._stderr_hint()
                if hint:
                    uat_logger.warning(
                        "scrcpy 版本 %s 启动失败 serial=%s: %s | stderr: %s",
                        ver,
                        self.serial,
                        exc,
                        hint,
                    )
                self.stop()
        err_msg = str(last_err) if last_err else "scrcpy 启动失败"
        hint = self._stderr_hint()
        if hint and hint not in err_msg:
            err_msg = f"{err_msg}（{hint}）"
        raise RuntimeError(err_msg)

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
        max_size = scrcpy_max_size()
        try:
            video_bit_rate = max(
                2_000_000,
                min(20_000_000, int(os.environ.get("MOBILE_SCRCPY_VIDEO_BITRATE", "12000000"))),
            )
        except ValueError:
            video_bit_rate = 12_000_000
        server_args = [
            f"max_fps={max_fps}",
            f"video_bit_rate={video_bit_rate}",
            "tunnel_forward=true",
            f"control={'true' if _scrcpy_control_enabled() else 'false'}",
            "audio=false",
            "show_touches=false",
            "send_frame_meta=true",
            "log_level=error",
        ]
        if max_size > 0:
            server_args.insert(1, f"max_size={max_size}")
        codec_opts = (os.environ.get("MOBILE_SCRCPY_CODEC_OPTIONS") or "").strip()
        if codec_opts:
            server_args.append(f"video_codec_options={codec_opts}")
        shell_cmd = (
            f"CLASSPATH={remote} app_process / com.genymobile.scrcpy.Server "
            f"{self._version} {' '.join(server_args)}"
        )
        self._shell_proc = subprocess.Popen(
            [adb_path(), "-s", serial, "shell", shell_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
        )
        self._start_stderr_drain()
        sock = self._wait_tcp_handshake(time.time() + 15.0)
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

    def read_packet(self) -> Optional[ScrcpyPacket]:
        sock = self._socket
        if not sock or not self.running:
            return None
        try:
            header = self._read_exact(sock, 12)
            pts, size = struct.unpack(">QI", header)
            if size <= 0 or size > 10_000_000:
                return None
            is_config = bool(pts & _SC_PACKET_FLAG_CONFIG)
            is_key = bool(pts & _SC_PACKET_FLAG_KEY_FRAME)
            payload = self._read_exact(sock, size)
            return ScrcpyPacket(payload, is_config=is_config, is_key=is_key)
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
        self._stderr_thread = None
        try:
            _run_adb(self.serial, "forward", "--remove", f"tcp:{self.local_port}")
        except Exception:
            pass


def _get_persistent_device(serial: str) -> Optional[ScrcpyDeviceSession]:
    with _persistent_lock:
        return _persistent_sessions.get((serial or "").strip())


class ScrcpyFrameRelay:
    """单设备单 scrcpy 会话 + 单读取线程，向 HTTP/WS 多客户端广播帧。"""

    def __init__(self, serial: str) -> None:
        self.serial = (serial or "").strip()
        self._stopped = False
        self._lock = threading.Lock()
        self._subscribers: Dict[int, queue.Queue] = {}
        self._next_sub_id = 0
        self._reader_thread: Optional[threading.Thread] = None

    def ensure_started(self) -> Tuple[bool, str]:
        sess, err = ensure_scrcpy_device_session(self.serial)
        if not sess:
            return False, err
        with self._lock:
            if self._stopped:
                self._stopped = False
            if self._reader_thread and self._reader_thread.is_alive():
                return True, ""
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name=f"scrcpy-relay-{self.serial}",
                daemon=True,
            )
            self._reader_thread.start()
        return True, ""

    def subscribe(self, maxsize: int = 24) -> Tuple[int, queue.Queue]:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._next_sub_id += 1
            sid = self._next_sub_id
            self._subscribers[sid] = q
        return sid, q

    def unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._subscribers.pop(sid, None)

    def _reader_loop(self) -> None:
        while not self._stopped:
            sess = _get_persistent_device(self.serial)
            if not sess or not sess.running:
                time.sleep(0.12)
                continue
            packet = sess.read_packet()
            if not packet:
                time.sleep(0.02)
                continue
            with self._lock:
                subs = list(self._subscribers.values())
            for sub_q in subs:
                try:
                    sub_q.put_nowait(packet)
                except queue.Full:
                    try:
                        sub_q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        sub_q.put_nowait(packet)
                    except queue.Full:
                        pass

    def stop(self) -> None:
        self._stopped = True
        with _relay_lock:
            _relays.pop(self.serial, None)
        with _persistent_lock:
            sess = _persistent_sessions.pop(self.serial, None)
        if sess:
            sess.stop()


def get_scrcpy_relay(serial: str) -> ScrcpyFrameRelay:
    serial = (serial or "").strip()
    with _relay_lock:
        relay = _relays.get(serial)
        if relay is None:
            relay = ScrcpyFrameRelay(serial)
            _relays[serial] = relay
        return relay


def iter_scrcpy_http_stream(serial: str):
    """
    经 Flask 同源 HTTP 输出 H.264（uint32 长度前缀 + payload）。
    与预热会话共用帧广播，避免重复拉起 scrcpy-server 或争抢 read_packet。
    """
    serial = (serial or "").strip()
    if not serial:
        return
    relay = get_scrcpy_relay(serial)
    ok, err = relay.ensure_started()
    if not ok:
        uat_logger.warning("scrcpy HTTP 无法启动 serial=%s: %s", serial, err)
        return
    uat_logger.info("scrcpy HTTP 流开始 serial=%s", serial)
    sid, pkt_queue = relay.subscribe()
    try:
        while not relay._stopped:
            try:
                packet = pkt_queue.get(timeout=5.0)
            except queue.Empty:
                sess = _get_persistent_device(serial)
                if not sess or not sess.running:
                    break
                continue
            if not packet:
                break
            yield pack_scrcpy_frame(packet)
    except Exception as exc:
        uat_logger.warning("scrcpy HTTP 流结束 serial=%s: %s", serial, exc)
    finally:
        relay.unsubscribe(sid)


def warm_scrcpy_session(serial: str, *, timeout: Optional[float] = None) -> Tuple[bool, str]:
    """连接设备时预热 scrcpy，成功则保持会话供 HTTP 流复用。"""
    serial = (serial or "").strip()
    if not serial:
        return False, "缺少 serial"
    warm_timeout = scrcpy_warm_timeout() if timeout is None else max(8.0, float(timeout))
    last_err = ""
    for attempt in range(2):
        if attempt > 0:
            stop_scrcpy_device_session(serial)
            time.sleep(0.8)
        relay = get_scrcpy_relay(serial)
        ok, err = relay.ensure_started()
        if not ok:
            last_err = err or "scrcpy 启动失败"
            continue
        sid, pkt_queue = relay.subscribe()
        try:
            deadline = time.time() + warm_timeout
            while time.time() < deadline:
                try:
                    packet = pkt_queue.get(timeout=0.35)
                except queue.Empty:
                    continue
                if isinstance(packet, ScrcpyPacket):
                    if packet.is_config or packet.payload:
                        return True, "ok"
                elif packet:
                    return True, "ok"
            last_err = "scrcpy 长时间无视频帧，请确认手机已解锁并允许 USB 调试"
        finally:
            relay.unsubscribe(sid)
        stop_scrcpy_device_session(serial)
    return False, last_err or "scrcpy 预热失败"


class ScrcpyWsSession:
    """WebSocket 客户端组共享帧广播（不再单独拉起 scrcpy-server）。"""

    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.relay = get_scrcpy_relay(serial)
        self._sub_id: Optional[int] = None
        self._queue: Optional[queue.Queue] = None
        self.task: Optional[asyncio.Task] = None

    async def start_relay(self) -> None:
        ok, err = self.relay.ensure_started()
        if not ok:
            raise RuntimeError(err or "scrcpy 启动失败")
        self._sub_id, self._queue = self.relay.subscribe()
        self.task = asyncio.create_task(self._relay_loop())

    async def _relay_loop(self) -> None:
        loop = asyncio.get_event_loop()
        pkt_queue = self._queue
        if not pkt_queue:
            return
        while not self.relay._stopped:
            try:
                packet = await loop.run_in_executor(
                    None, lambda: pkt_queue.get(timeout=0.5)
                )
            except queue.Empty:
                continue
            if not packet:
                break
            frame = pack_scrcpy_frame(packet)
            clients = list(_clients_by_serial.get(self.serial, set()))
            dead = []
            for ws in clients:
                try:
                    await ws.send(frame)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _clients_by_serial.get(self.serial, set()).discard(ws)

    async def stop(self) -> None:
        if self._sub_id is not None:
            self.relay.unsubscribe(self._sub_id)
            self._sub_id = None
            self._queue = None
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
    dev = _get_persistent_device(serial)
    if not dev or not dev.running:
        return
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
    from urllib.parse import unquote

    serial = "emulator-5554"
    if "?" in path:
        qs = path.split("?", 1)[1]
        for part in qs.split("&"):
            if part.startswith("serial="):
                serial = unquote(part.split("=", 1)[1])
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
        if _tcp_port_in_use(host, port) and not _bridge_ready():
            check_host = _bridge_connect_host(host)
            if _foreign_bridge_healthy(check_host, port):
                return True, f"scrcpy bridge 端口 {port} 已有可用服务，将直接复用"
            uat_logger.warning(
                "scrcpy bridge 端口 %s 被占用但非 WebSocket 服务，无法启动 bridge",
                port,
            )
            return (
                False,
                f"端口 {port} 被占用且非 scrcpy bridge，请结束占用进程或修改 MOBILE_SCRCPY_BRIDGE_PORT",
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
    loop = _bridge_loop
    for sess in sessions:
        try:
            if loop and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(sess.stop(), loop)
                fut.result(timeout=5)
            else:
                asyncio.run(sess.stop())
        except Exception:
            pass
