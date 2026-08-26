# -*- coding: utf-8 -*-
"""
scrcpy 视频 WebSocket 桥接（真机高帧率画布投屏）。

将 scrcpy-server H.264 帧通过 WebSocket 推送到浏览器 WebCodecs 解码。
未安装 scrcpy 或内嵌画布不可用时，由前端启动手机画面投屏（不再降级为 adb screencap）。
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
from typing import Any, Deque, Dict, List, Optional, Set, Tuple, Union

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

from modules.mobile.mobile_env_config import adb_path, scrcpy_bridge_port, scrcpy_bridge_url, scrcpy_max_size, scrcpy_mirror_fps, scrcpy_path

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
    bucket = zlib.crc32(key.encode("utf-8")) & 0x7FFFFFFF  # must fit in Java int
    return 27183 + (bucket % 500)


def _stable_serial_scid(serial: str) -> str:
    """scrcpy 3.x 会话 ID（8 位 hex，按 serial 稳定生成）。"""
    key = (serial or "emulator-5554").strip() or "emulator-5554"
    bucket = zlib.crc32(key.encode("utf-8")) & 0x7FFFFFFF
    return f"{bucket:08x}"


def _version_major(version: str) -> int:
    m = re.match(r"(\d+)", (version or "").strip())
    return int(m.group(1)) if m else 2


def _abstract_socket_name(version: str, scid: str) -> str:
    """adb forward 目标 abstract socket（3.x 需 scrcpy_<scid>）。"""
    if _version_major(version) >= 3:
        return f"localabstract:scrcpy_{scid}"
    return "localabstract:scrcpy"


def _read_exact_sock(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("scrcpy socket 已关闭")
        buf += chunk
    return buf


def read_forward_handshake(sock: socket.socket) -> bytes:
    """
    forward 隧道握手：标准协议先 1 字节 dummy(0x00) 再 64 字节 device name；
    旧版 server 无 dummy 时首字节即 device name 起始。
    """
    first = sock.recv(1)
    if not first:
        raise ConnectionError("scrcpy socket 已关闭")
    if first == b"\x00":
        return _read_exact_sock(sock, 64)
    return first + _read_exact_sock(sock, 63)


def _scrcpy_control_enabled() -> bool:
    raw = (os.environ.get("MOBILE_SCRCPY_CONTROL") or "1").strip().lower()
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
    return Path(__file__).resolve().parents[2] / "static" / "vendor"


def _sync_scrcpy_server_to_vendor() -> None:
    """将插件目录中的 scrcpy-server 同步到 static/vendor，便于打包与可用性检测。"""
    vendor = _vendor_scrcpy_dir()
    if list(vendor.glob("scrcpy-server*")):
        return
    sources: list[Path] = []
    exe = scrcpy_path()
    if exe and exe not in ("scrcpy", "scrcpy.exe"):
        sources.extend(Path(exe).resolve().parent.glob("scrcpy-server*"))
    try:
        from modules.mobile.mobile_scrcpy_bundles import scrcpy_install_dir

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



def _kill_stale_scrcpy_servers(serial: str) -> None:
    """清理设备上残留的 scrcpy-server 进程，避免 abstract socket 被占用。"""
    try:
        r = _run_adb(serial, "shell", "pkill -f com.genymobile.scrcpy.Server", timeout=8)
        if r.returncode == 0:
            uat_logger.info("已清理残留 scrcpy-server 进程 serial=%s", serial)
            time.sleep(0.5)
    except Exception:
        pass
    # also try kill -9
    try:
        _run_adb(serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server", timeout=8)
    except Exception:
        pass

def find_scrcpy_server_jar() -> Optional[str]:
    """定位 scrcpy-server（与 scrcpy 可执行文件同目录或 vendor）。"""
    _sync_scrcpy_server_to_vendor()
    candidates: list[Path] = []
    exe = scrcpy_path()
    if exe and exe not in ("scrcpy", "scrcpy.exe"):
        p = Path(exe).resolve().parent
        candidates.extend(p.glob("scrcpy-server*"))
    try:
        from modules.mobile.mobile_scrcpy_bundles import scrcpy_install_dir

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
    # 文件名带版本号时优先 JAR；裸 scrcpy-server 则用 exe --version
    if jar_ver:
        ver = jar_ver
    elif exe_ver:
        ver = exe_ver
    else:
        ver = "2.4"
    _scrcpy_server_version._cache = ver
    return ver


def _version_candidates() -> list[str]:
    """返回版本候选列表。只返回检测到的真实版本，避免版本不匹配导致 server 崩溃。"""
    primary = _scrcpy_server_version()
    return [primary] if primary else ["2.4"]

def scrcpy_warm_timeout() -> float:
    raw = (os.environ.get("MOBILE_SCRCPY_WARM_TIMEOUT") or "20").strip()
    try:
        return max(8.0, min(60.0, float(raw)))
    except ValueError:
        return 20.0


# ── 设备预检与自适应参数 ────────────────────────────────────────────

def _adb_exec(serial: str, shell_args: str, timeout: int = 15) -> Tuple[int, str, str]:
    """执行 adb shell 命令，返回 (returncode, stdout, stderr)。"""
    r = _run_adb(serial, "shell", shell_args, timeout=timeout)
    return r.returncode, (r.stdout or b"").decode("utf-8", errors="replace"), (r.stderr or b"").decode("utf-8", errors="replace")


def _read_adb_prop(serial: str, key: str, default: str = "") -> Tuple[bool, str]:
    """读取 Android 系统属性，返回 (成功, 值)。"""
    code, out, err = _adb_exec(serial, f"getprop {key}", timeout=10)
    val = (out + err).strip()
    if val and code == 0:
        return True, val
    return False, default


def _check_device_screen_on(serial: str) -> Tuple[bool, str]:
    """检查设备屏幕状态（亮/灭、锁定/解锁）。"""
    # 方法1：dumpsys power 检查屏幕亮灭
    code, out, err = _adb_exec(serial, "dumpsys power", timeout=10)
    text = (out + err).lower()
    screen_on = ("mwakefulness=awake" in text or
                 "mwakefulness=1" in text or
                 "displaypowerstate=on" in text or
                 "mscreenon=true" in text or
                 "mscreenonearly=true" in text or
                 "mstate=on" in text)

    # 方法2：dumpsys window 检查锁定状态
    code2, out2, err2 = _adb_exec(serial, "dumpsys window", timeout=10)
    text2 = (out2 + err2).lower()

    # 多种锁定状态特征
    has_keyguard = "mkeyguard" in text2 or "keyguard" in text2
    keyguard_showing = "mshowing=true" in text2
    keyguard_dismissed = "mdismissed=true" in text2
    dreaming = "mdreaming=true" in text2

    if dreaming:
        locked = True  # 设备正在休眠
    elif has_keyguard:
        # 有锁屏组件：正在显示 且 未解散 = 锁屏中
        locked = keyguard_showing and not keyguard_dismissed
    else:
        # 无法判断锁屏状态，假定未锁定
        locked = False

    if screen_on and not locked:
        return True, "屏幕已点亮且解锁"
    elif screen_on:
        return False, "屏幕已点亮但可能处于锁屏状态，请解锁手机"
    else:
        return False, "屏幕已熄灭，请点亮并解锁手机"


def _try_wake_screen(serial: str) -> bool:
    """尝试唤醒屏幕并解锁（多种方法组合）。仅在屏幕未点亮时按电源键。"""
    try:
        # 先检查当前屏幕状态
        screen_on, _ = _check_device_screen_on(serial)
        if screen_on:
            uat_logger.info("scrcpy 屏幕已点亮 serial=%s，跳过唤醒", serial)
            return True

        # 屏幕灭了，才按电源键唤醒
        _run_adb(serial, "shell", "input keyevent 224", timeout=5)  # KEYCODE_WAKEUP
        time.sleep(0.5)
        # 如果 WAKEUP 不够，再试 POWER
        screen_on2, _ = _check_device_screen_on(serial)
        if not screen_on2:
            _run_adb(serial, "shell", "input keyevent 26", timeout=5)  # KEYCODE_POWER
            time.sleep(0.8)

        # 上滑解锁（大部分 Android 设备）
        _run_adb(serial, "shell", "input swipe 540 1800 540 600 300", timeout=5)
        time.sleep(0.3)
        # MENU key 解锁
        _run_adb(serial, "shell", "input keyevent 82", timeout=5)   # KEYCODE_MENU
        time.sleep(0.3)
        # dismiss keyguard (Android 8+)
        _run_adb(serial, "shell", "wm dismiss-keyguard", timeout=5)
        time.sleep(0.5)
        # 保持屏幕常亮
        _run_adb(serial, "shell", "svc power stayon true", timeout=5)
        time.sleep(0.3)
        ok, msg = _check_device_screen_on(serial)
        if ok:
            uat_logger.info("scrcpy 唤醒屏幕成功 serial=%s", serial)
        else:
            uat_logger.warning("scrcpy 唤醒屏幕后状态: %s", msg)
        return ok
    except Exception as exc:
        uat_logger.debug("scrcpy 唤醒屏幕异常 serial=%s: %s", serial, exc)
        return False


def _get_android_sdk_level(serial: str) -> int:
    """获取设备 Android SDK 版本（整数）。"""
    ok, val = _read_adb_prop(serial, "ro.build.version.sdk")
    if ok and val:
        try:
            return int(val)
        except ValueError:
            pass
    # 回退: 从 release 版本号推断
    ok2, rel = _read_adb_prop(serial, "ro.build.version.release")
    if ok2 and rel:
        try:
            major = int(rel.split(".")[0])
            mapping = {14: 34, 13: 33, 12: 31, 11: 30, 10: 29, 9: 28, 8: 26, 7: 24, 6: 23, 5: 21, 4: 19}
            return mapping.get(major, 28)
        except ValueError:
            pass
    return 28  # 默认假定 Android 9+


def _get_device_memory_mb(serial: str) -> int:
    """获取设备总内存（MB），失败返回 0（未知）。"""
    code, out, err = _adb_exec(serial, "cat /proc/meminfo", timeout=10)
    text = out + err
    for line in text.split("\n"):
        if line.lower().startswith("memtotal:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) // 1024  # kB → MB
                except ValueError:
                    pass
    # 回退: dumpsys meminfo
    code2, out2, err2 = _adb_exec(serial, "dumpsys meminfo", timeout=10)
    text2 = out2 + err2
    for line in text2.split("\n"):
        if "total ram:" in line.lower():
            import re as _re
            m = _re.search(r"([\d,]+)\s*k", line.lower().replace(",", ""))
            if m:
                try:
                    return int(m.group(1)) // 1024
                except ValueError:
                    pass
    return 0


def _check_device_storage_mb(serial: str, path: str = "/data/local/tmp") -> int:
    """检查设备存储空间（MB），失败返回 -1。"""
    code, out, err = _adb_exec(serial, f"df -k {path}", timeout=10)
    text = out + err
    for line in text.split("\n"):
        if "Filesystem" in line or "文件系统" in line or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 4:
            try:
                available = int(parts[3]) // 1024  # kB → MB
                return available
            except ValueError:
                pass
    return -1


def _check_device_cpu_abi(serial: str) -> str:
    """检测设备 CPU 架构。"""
    ok, val = _read_adb_prop(serial, "ro.product.cpu.abi")
    if ok and val:
        return val.strip()
    ok2, val2 = _read_adb_prop(serial, "ro.product.cpu.abilist")
    if ok2 and val2:
        return val2.strip().split(",")[0]
    return "arm64-v8a"  # 默认


def _check_hardware_encoder(serial: str) -> Tuple[bool, str]:
    """检查设备是否支持 H.264 硬件编码器。"""
    code, out, err = _adb_exec(serial, "dumpsys media.codec", timeout=10)
    text = (out + err).lower()
    if "omx." in text:
        # 检查是否有 H.264/AVC 编码器
        if "video/avc" in text or "h.264" in text or "h264" in text:
            return True, "设备支持 H.264 硬件编码器 (OMX)"
    # 回退: 检测 MediaCodec 列表
    code2, out2, err2 = _adb_exec(serial, "ls /dev/video* 2>/dev/null; echo ---; ls /sys/class/video4linux/ 2>/dev/null", timeout=10)
    text2 = (out2 + err2).strip()
    if text2 and text2 != "---":
        return True, "检测到 V4L2 视频设备"
    # 大多数现代 Android 设备都支持
    return True, "假定支持 H.264 编码（现代设备均支持）"


def _diagnose_device_for_scrcpy(serial: str) -> Dict[str, Any]:
    """设备预检：收集所有可能影响 scrcpy 启动的信息。"""
    diag: Dict[str, Any] = {
        "serial": serial,
        "screen_ok": False,
        "screen_msg": "",
        "sdk_level": 28,
        "total_memory_mb": 0,
        "storage_free_mb": -1,
        "cpu_abi": "",
        "has_encoder": False,
        "encoder_msg": "",
        "warnings": [],
        "recommended_profile": "balanced",
    }

    # 1. 屏幕状态
    diag["screen_ok"], diag["screen_msg"] = _check_device_screen_on(serial)
    if not diag["screen_ok"]:
        diag["warnings"].append(diag["screen_msg"])

    # 2. Android SDK 版本
    diag["sdk_level"] = _get_android_sdk_level(serial)
    if diag["sdk_level"] < 21:
        diag["warnings"].append(f"Android SDK {diag['sdk_level']} 版本过低（需 ≥ 21）")
    elif diag["sdk_level"] < 24:
        diag["warnings"].append(f"Android SDK {diag['sdk_level']} 较低，可能不完全兼容")

    # 3. 内存
    diag["total_memory_mb"] = _get_device_memory_mb(serial)
    if 0 < diag["total_memory_mb"] < 2048:
        diag["recommended_profile"] = "conservative"
        diag["warnings"].append(f"设备内存较小（{diag['total_memory_mb']}MB），建议使用保守参数")
    elif diag["total_memory_mb"] >= 4096:
        diag["recommended_profile"] = "aggressive"

    # 4. 存储
    diag["storage_free_mb"] = _check_device_storage_mb(serial)
    if 0 <= diag["storage_free_mb"] < 50:
        diag["warnings"].append(f"/data/local/tmp 可用空间不足（{diag['storage_free_mb']}MB），scrcpy-server 可能推送失败")

    # 5. CPU 架构
    diag["cpu_abi"] = _check_device_cpu_abi(serial)

    # 6. 编码器
    diag["has_encoder"], diag["encoder_msg"] = _check_hardware_encoder(serial)

    # 7. 综合推荐
    if diag["sdk_level"] < 24 or diag["total_memory_mb"] < 2048:
        diag["recommended_profile"] = "conservative"
    elif diag["sdk_level"] < 21:
        diag["recommended_profile"] = "minimal"

    return diag


def _analyze_scrcpy_stderr(stderr_text: str) -> Dict[str, Any]:
    """
    分析 scrcpy-server stderr 输出，识别具体失败原因。
    返回 {"type": 错误类型, "message": 中文描述, "retry_with_lower": 是否需要降参数重试}
    """
    text = (stderr_text or "").lower()
    result: Dict[str, Any] = {
        "type": "unknown",
        "message": "",
        "retry_with_lower": False,
        "fatal": False,
    }

    # 编码器创建失败（最常见，需要降参数重试）
    if "fail to create encoder" in text or "create encoder" in text or "encoder error" in text:
        result["type"] = "encoder_create_fail"
        result["message"] = "设备编码器创建失败，可能是分辨率/码率过高"
        result["retry_with_lower"] = True
        return result

    if "unsupported resolution" in text or "unsupported size" in text or "invalid size" in text:
        result["type"] = "resolution_unsupported"
        result["message"] = "设备不支持当前分辨率"
        result["retry_with_lower"] = True
        return result

    if "bitrate" in text and ("too high" in text or "unsupported" in text or "invalid" in text):
        result["type"] = "bitrate_too_high"
        result["message"] = "码率过高，设备编码器不支持"
        result["retry_with_lower"] = True
        return result

    if "max_fps" in text and ("unsupported" in text or "invalid" in text):
        result["type"] = "fps_unsupported"
        result["message"] = "帧率设置不被设备支持"
        result["retry_with_lower"] = True
        return result

    # Java 类加载错误（jar 版本与设备不兼容）
    if "noclassdeffounderror" in text or "classnotfoundexception" in text:
        result["type"] = "class_not_found"
        result["message"] = "scrcpy-server 版本与设备不兼容（类缺失），请尝试其他版本"
        result["fatal"] = False
        return result

    if "unsupportedclassversionerror" in text:
        result["type"] = "unsupported_class_version"
        result["message"] = "scrcpy-server 编译版本过高，设备 Java 运行时版本不足"
        result["fatal"] = False
        return result

    # 原生库加载失败
    if "unsatisfiedlinkerror" in text or "library" in text and "load" in text:
        result["type"] = "native_library_fail"
        result["message"] = "scrcpy-server 原生库与设备 CPU 架构不匹配"
        result["fatal"] = True
        return result

    # 权限/安全限制
    if "securityexception" in text or "permission denied" in text:
        result["type"] = "permission_denied"
        result["message"] = "设备安全策略禁止 scrcpy-server 运行（SELinux/权限限制）"
        result["fatal"] = True
        return result

    # 显示服务
    if "unable to find a compatible display" in text or "display" in text and ("not found" in text or "error" in text):
        result["type"] = "display_not_found"
        result["message"] = "设备显示服务异常或屏幕完全关闭"
        result["fatal"] = True
        return result

    # 内存不足
    if "outofmemoryerror" in text or "out of memory" in text:
        result["type"] = "out_of_memory"
        result["message"] = "设备内存不足，scrcpy-server 无法启动"
        result["retry_with_lower"] = True
        return result

    # app_process 找不到
    if "app_process" in text and ("not found" in text or "error" in text):
        result["type"] = "app_process_missing"
        result["message"] = "设备缺少 app_process，可能是非标准 Android 系统"
        result["fatal"] = True
        return result

    # 未知错误
    if text:
        result["type"] = "unknown_error"
        result["message"] = f"scrcpy-server 报错: {stderr_text[:200]}"
    return result


# ── 自适应参数配置 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ScrcpyDeviceParams:
    """scrcpy-server 启动参数档位。"""
    profile_name: str  # aggressive / balanced / conservative / minimal
    max_fps: int
    video_bit_rate: int
    max_size: int  # 0 = 原生分辨率
    codec_options: str = ""

    def to_server_args(self, version: str, scid: str, control_enabled: bool) -> list:
        args = [
            f"max_fps={self.max_fps}",
            f"video_bit_rate={self.video_bit_rate}",
            "tunnel_forward=true",
            f"control={'true' if control_enabled else 'false'}",
            "audio=false",
            "show_touches=false",
            "send_frame_meta=true",
            "log_level=error",
        ]
        if _version_major(version) >= 3:
            args.append(f"scid={scid}")
        if self.max_size > 0:
            args.insert(1, f"max_size={self.max_size}")
        if self.codec_options:
            args.append(f"video_codec_options={self.codec_options}")
        return args


def _generate_param_profiles(diag: Dict[str, Any]) -> List[ScrcpyDeviceParams]:
    """
    根据设备诊断结果生成参数档位列表（从最合适到最保守）。
    设备越好，初始档位越高；失败后自动降档。
    """
    sdk = diag.get("sdk_level", 28)
    mem = diag.get("total_memory_mb", 2048)
    screen_ok = diag.get("screen_ok", True)

    # 从 env 读取用户自定义（优先）
    env_max_fps = scrcpy_mirror_fps()
    try:
        env_bitrate = max(2_000_000, min(20_000_000, int(
            os.environ.get("MOBILE_SCRCPY_VIDEO_BITRATE", "12000000"))))
    except ValueError:
        env_bitrate = 12_000_000
    env_max_size = scrcpy_max_size()
    env_codec = (os.environ.get("MOBILE_SCRCPY_CODEC_OPTIONS") or "").strip()

    profiles: List[ScrcpyDeviceParams] = []

    # ── 档位1：激进档（设备条件好 + 用户未自定义时使用） ──
    if sdk >= 28 and mem >= 3072:
        profiles.append(ScrcpyDeviceParams(
            profile_name="aggressive",
            max_fps=min(env_max_fps, 60),
            video_bit_rate=min(env_bitrate, 12_000_000),
            max_size=env_max_size,
            codec_options=env_codec,
        ))

    # ── 档位2：均衡档（大多数设备推荐初始使用） ──
    profiles.append(ScrcpyDeviceParams(
        profile_name="balanced",
        max_fps=min(env_max_fps, 30),
        video_bit_rate=min(env_bitrate, 8_000_000),
        max_size=env_max_size if env_max_size > 0 else 1920,
        codec_options=env_codec,
    ))

    # ── 档位3：保守档（内存不足或低版本 Android） ──
    profiles.append(ScrcpyDeviceParams(
        profile_name="conservative",
        max_fps=min(env_max_fps, 20),
        video_bit_rate=min(env_bitrate, 4_000_000),
        max_size=1400 if env_max_size <= 0 else min(env_max_size, 1400),
        codec_options=env_codec,
    ))

    # ── 档位4：最低档（极端兼容） ──
    profiles.append(ScrcpyDeviceParams(
        profile_name="minimal",
        max_fps=min(env_max_fps, 10),
        video_bit_rate=min(env_bitrate, 2_000_000),
        max_size=960 if env_max_size <= 0 else min(env_max_size, 960),
        codec_options=env_codec,
    ))

    # 重新排序：把推荐档位放在第一位，只向更低档位回退（不尝试更激进档位）
    rec = diag.get("recommended_profile", "balanced")
    rec_idx = {"aggressive": 0, "balanced": 1, "conservative": 2, "minimal": 3}
    start_idx = rec_idx.get(rec, 1)
    # 从推荐档位开始，只往后（更保守方向）重试，不往后更激进方向
    profiles = profiles[start_idx:]

    # 如果屏幕黑着，跳过激进档
    if not screen_ok:
        profiles = [p for p in profiles if p.profile_name != "aggressive"]

    if len(profiles) == 0:
        # 保底
        profiles.append(ScrcpyDeviceParams(
            profile_name="minimal",
            max_fps=10,
            video_bit_rate=2_000_000,
            max_size=960,
            codec_options="",
        ))

    # 去重（按参数值）
    seen: set = set()
    deduped: List[ScrcpyDeviceParams] = []
    for p in profiles:
        key = (p.max_fps, p.video_bit_rate, p.max_size, p.codec_options)
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped


def scrcpy_mirror_diagnostics(udid: str = "") -> Dict[str, Any]:
    """供 mirror/status API 暴露的 scrcpy 诊断字段（不触发 warm）。"""
    from modules.mobile.mobile_env_config import resolve_mirror_backend, scrcpy_available

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
        if sess and sess.running:
            diag["scrcpy_current_profile"] = (
                sess._current_params.profile_name if sess._current_params else None
            )
            diag["scrcpy_current_fps"] = (
                sess._current_params.max_fps if sess._current_params else None
            )
            diag["scrcpy_current_bitrate"] = (
                sess._current_params.video_bit_rate if sess._current_params else None
            )
            diag["scrcpy_current_max_size"] = (
                sess._current_params.max_size if sess._current_params else None
            )
        if sess and sess._device_diag:
            diag["device_diagnostics"] = {
                "sdk_level": sess._device_diag.get("sdk_level"),
                "total_memory_mb": sess._device_diag.get("total_memory_mb"),
                "storage_free_mb": sess._device_diag.get("storage_free_mb"),
                "cpu_abi": sess._device_diag.get("cpu_abi"),
                "screen_ok": sess._device_diag.get("screen_ok"),
                "screen_msg": sess._device_diag.get("screen_msg"),
                "warnings": sess._device_diag.get("warnings", []),
                "recommended_profile": sess._device_diag.get("recommended_profile"),
            }
    if backend != "scrcpy_ws":
        if not scrcpy_available():
            diag["mirror_fallback_reason"] = "scrcpy 不可用（未找到 scrcpy.exe 或 scrcpy-server）"
        return diag
    if not jar:
        diag["mirror_fallback_reason"] = "未找到 scrcpy-server，内嵌投屏不可用，可启动独立 scrcpy 窗口"
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
    """单设备 scrcpy-server 会话（H.264 over TCP），支持自动预检与自适应参数重试。"""

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
        self._current_params: Optional[ScrcpyDeviceParams] = None
        self._device_diag: Optional[Dict[str, Any]] = None

    def _stderr_hint(self) -> str:
        if not self._stderr_lines:
            return ""
        return "; ".join(list(self._stderr_lines)[-5:])

    def _stderr_full(self) -> str:
        if not self._stderr_lines:
            return ""
        return "\n".join(self._stderr_lines)

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

    def _wait_tcp_connect(self, deadline: float) -> socket.socket:
        """仅建立 TCP 连接（不读握手），供控制通道先建立。"""
        last_err = None
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
                return sock
            except OSError as exc:
                last_err = exc
            time.sleep(0.35)
        raise RuntimeError(f"scrcpy TCP 连接超时（{last_err}）")

    def _read_video_handshake(self, sock: socket.socket, deadline: float) -> bytes:
        """从已建立的视频 socket 读取握手数据。"""
        remaining = max(5.0, deadline - time.time())
        sock.settimeout(remaining)
        try:
            device_name = read_forward_handshake(sock)
            return device_name
        except Exception as exc:
            proc_alive = self._shell_proc and self._shell_proc.poll() is None
            hint = self._stderr_hint()
            msg = f"scrcpy 握手读取失败 ({type(exc).__name__}: {exc}, proc_alive={proc_alive})"
            if hint:
                msg += f" stderr={hint[:200]}"
            raise RuntimeError(msg) from exc

    def _wait_tcp_handshake(self, deadline: float) -> Tuple[socket.socket, bytes]:
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
                device_name = read_forward_handshake(sock)
                return sock, device_name
            except OSError as exc:
                last_err = exc
            except ConnectionError as exc:
                last_err = exc
            time.sleep(0.35)
        hint = self._stderr_hint()
        msg = "scrcpy 连接握手失败：请确认手机已解锁，并重试连接"
        if hint:
            msg += f"（{hint}）"
        elif last_err:
            msg += f"（{last_err}）"
        raise RuntimeError(msg)

    def _do_start_once(self, params: ScrcpyDeviceParams) -> None:
        """使用指定参数档位启动 scrcpy-server 一次。"""
        serial = self.serial
        remote = "/data/local/tmp/scrcpy-server.jar"

        # ① 清理残留进程（每次尝试前都清理，避免 abstract socket 被占用）
        _kill_stale_scrcpy_servers(serial)

        # ② 推送 jar
        push = _run_adb(serial, "push", self._server_jar, remote, timeout=60)
        if push.returncode != 0:
            err = (push.stderr or push.stdout or b"").decode("utf-8", errors="replace")
            # 检查是否是存储空间不足
            if "no space" in err.lower() or "space" in err.lower():
                free_mb = _check_device_storage_mb(serial)
                raise RuntimeError(
                    f"推送 scrcpy-server 失败（设备存储不足，{free_mb}MB 可用）：{err}"
                )
            raise RuntimeError(f"推送 scrcpy-server 失败：{err}")

        # ② adb forward
        scid = _stable_serial_scid(serial)
        abstract = _abstract_socket_name(self._version, scid)
        _run_adb(serial, "forward", "--remove", f"tcp:{self.local_port}")
        fwd = _run_adb(serial, "forward", f"tcp:{self.local_port}", abstract)
        if fwd.returncode != 0:
            err = (fwd.stderr or fwd.stdout or b"").decode("utf-8", errors="replace")
            raise RuntimeError(f"adb forward 失败：{err}")

        # ③ 构建 shell 命令
        server_args = params.to_server_args(
            self._version,
            scid,
            _scrcpy_control_enabled(),
        )
        shell_cmd = (
            f"CLASSPATH={remote} app_process / com.genymobile.scrcpy.Server "
            f"{self._version} {' '.join(server_args)}"
        )

        # ④ 启动进程
        self._shell_proc = subprocess.Popen(
            [adb_path(), "-s", serial, "shell", shell_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
        )
        self._start_stderr_drain()
        time.sleep(1.0)  # 等待 server 进程启动并绑定 abstract socket

        uat_logger.info(
            "scrcpy 尝试启动: serial=%s version=%s profile=%s fps=%d bitrate=%d max_size=%d",
            serial, self._version, params.profile_name,
            params.max_fps, params.video_bit_rate, params.max_size,
        )

        # ⑤ 建立 TCP 连接（视频 + 控制）
        # scrcpy-server 在 control=true 时等待两条 TCP 连接都建立后才发送握手
        # 必须先连控制通道，再读视频握手
        deadline = time.time() + 15.0
        sock = self._wait_tcp_connect(deadline)
        self._socket = sock

        # ⑥ 控制通道（在读握手前建立，否则 server 会超时退出）
        if _scrcpy_control_enabled():
            try:
                ctrl = socket.create_connection(("127.0.0.1", self.local_port), timeout=8)
                ctrl.settimeout(5.0)
                self._control_socket = ctrl
            except Exception as exc:
                uat_logger.warning("scrcpy 控制通道未连接 serial=%s: %s", serial, exc)
                self._control_socket = None

        # ⑦ 读取视频握手（此时两条连接已建立，server 会发送握手）
        time.sleep(0.3)  # 给 server 时间初始化两条连接
        device_name = self._read_video_handshake(sock, deadline)
        uat_logger.info(
            "scrcpy 已连接: serial=%s version=%s profile=%s device=%s control=%s",
            serial, self._version, params.profile_name,
            device_name.split(b"\x00")[0].decode("utf-8", errors="replace"),
            bool(self._control_socket),
        )
        self._current_params = params
        self.running = True

    def start(self) -> None:
        """启动 scrcpy-server，支持预检诊断、屏幕唤醒、自适应参数降级重试。"""
        if not self._server_jar:
            raise RuntimeError(
                "未找到 scrcpy-server。请安装 scrcpy 并将 scrcpy-server 置于 scrcpy.exe 同目录，"
                "或复制到 static/vendor/scrcpy-server"
            )

        # ── 阶段0：设备预检诊断 ──
        uat_logger.info("scrcpy 开始设备预检 serial=%s", self.serial)
        self._device_diag = _diagnose_device_for_scrcpy(self.serial)
        uat_logger.info(
            "scrcpy 预检结果 serial=%s: sdk=%d mem=%dMB storage=%dMB screen=%s profile=%s "
            "warnings=%s",
            self.serial,
            self._device_diag["sdk_level"],
            self._device_diag["total_memory_mb"],
            self._device_diag["storage_free_mb"],
            "OK" if self._device_diag["screen_ok"] else "BLACK/LOCKED",
            self._device_diag["recommended_profile"],
            self._device_diag["warnings"],
        )

        # ── 阶段0.5：尝试唤醒屏幕 ──
        if not self._device_diag["screen_ok"]:
            uat_logger.warning(
                "scrcpy 设备屏幕未点亮/锁定 serial=%s，尝试唤醒…", self.serial
            )
            _try_wake_screen(self.serial)
            # 重新检查
            screen_ok, screen_msg = _check_device_screen_on(self.serial)
            self._device_diag["screen_ok"] = screen_ok
            self._device_diag["screen_msg"] = screen_msg
            uat_logger.info(
                "scrcpy 唤醒后屏幕状态 serial=%s: %s", self.serial, screen_msg
            )

        # ── 阶段0.6：清理残留 scrcpy-server 进程和 stale socket ──
        _kill_stale_scrcpy_servers(self.serial)

        # ── 生成参数档位 ──
        param_profiles = _generate_param_profiles(self._device_diag)
        uat_logger.info(
            "scrcpy 参数档位 serial=%s: %s",
            self.serial,
            ", ".join(p.profile_name for p in param_profiles),
        )

        # ── 阶段1-2：参数档位 × 版本候选 双层重试 ──
        all_errors: List[Dict[str, Any]] = []
        skipped_versions: Set[str] = set()
        tried_profiles: List[str] = []
        start_deadline = time.time() + 60.0  # 总时限 60 秒

        for params in param_profiles:
            if time.time() > start_deadline:
                uat_logger.warning("scrcpy 启动超时（60s），未尝试: %s", params.profile_name)
                break
            profile_tried = False
            for ver in _version_candidates():
                if time.time() > start_deadline:
                    uat_logger.warning("scrcpy 启动超时（60s），跳过版本: %s", ver)
                    break
                if ver in skipped_versions:
                    uat_logger.debug("scrcpy 跳过已排除版本 %s serial=%s", ver, self.serial)
                    continue

                self._version = ver
                try:
                    self._do_start_once(params)
                    uat_logger.info(
                        "scrcpy ✅ 启动成功: serial=%s version=%s profile=%s",
                        self.serial, ver, params.profile_name,
                    )
                    return  # 成功！
                except Exception as exc:
                    err_str = str(exc)
                    stderr_full = self._stderr_full()
                    stderr_analysis = _analyze_scrcpy_stderr(stderr_full)

                    err_info = {
                        "version": ver,
                        "profile": params.profile_name,
                        "error": err_str,
                        "stderr_analysis": stderr_analysis,
                    }
                    all_errors.append(err_info)
                    profile_tried = True

                    uat_logger.warning(
                        "scrcpy 启动失败: serial=%s version=%s profile=%s (%s/%s/%s) error=%s stderr_analysis=%s",
                        self.serial, ver, params.profile_name,
                        params.max_fps, params.video_bit_rate, params.max_size,
                        err_str[:200],
                        stderr_analysis.get("type", "unknown"),
                    )
                    if stderr_full:
                        uat_logger.warning(
                            "scrcpy stderr serial=%s: %s", self.serial, stderr_full[:500]
                        )

                    # 根据 stderr 分析决定是否跳过此版本
                    if stderr_analysis.get("fatal"):
                        skipped_versions.add(ver)
                        uat_logger.warning(
                            "scrcpy 版本 %s 致命错误，跳过此版本: %s",
                            ver, stderr_analysis.get("message", ""),
                        )

                    # 编码器类错误：跳过剩余版本，直接降参数档位
                    if stderr_analysis.get("retry_with_lower"):
                        uat_logger.info(
                            "scrcpy 编码器瓶颈，跳过剩余版本候选，直接降参数档位"
                        )
                        break  # 跳出版本重试循环，进入下一个参数档位

                    # 非致命也非降参数类错误：尝试下一个版本
                    self.stop()

            if profile_tried:
                tried_profiles.append(params.profile_name)

            # 如果当前档位的所有版本都失败了且不是"降参数"类型
            # 继续下一个档位（通过外层循环自动进行）

        # ── 所有重试均失败 ──
        error_summary = "; ".join(
            f"[{e['profile']}/{e['version']}]{e['error'][:120]}"
            for e in all_errors[-6:]  # 最多展示最后6次错误
        )
        final_msg = f"scrcpy 启动失败（已尝试 {len(all_errors)} 次）"
        if error_summary:
            final_msg += f"：{error_summary}"
        uat_logger.error("scrcpy ❌ 全部重试失败 serial=%s: %s", self.serial, final_msg)
        raise RuntimeError(final_msg)

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
        self._current_params = None
        self._device_diag = None
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
    """单设备单 scrcpy 会话 + 单读取线程，向 HTTP/WS 多客户端广播帧。

    当 scrcpy-server 异常退出时自动重启会话（最多 3 次），避免 HTTP/WS 流断开。"""

    _MAX_SESSION_RESTARTS = 3  # 会话自动重启上限

    def __init__(self, serial: str) -> None:
        self.serial = (serial or "").strip()
        self._stopped = False
        self._lock = threading.Lock()
        self._subscribers: Dict[int, queue.Queue] = {}
        self._next_sub_id = 0
        self._reader_thread: Optional[threading.Thread] = None
        # 健康追踪
        self._last_frame_time: float = 0.0
        self._session_restart_count: int = 0

    @property
    def seconds_since_last_frame(self) -> float:
        if not self._last_frame_time:
            return -1.0
        return time.time() - self._last_frame_time

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
        """读取帧并广播到所有订阅者。

        当 scrcpy-server 进程异常退出时，自动尝试重启会话：
        - 检测到 session 停止后等待约 1.2s（10 个迭代周期）
        - 然后尝试重启，最多 3 次
        - 每次重启使用递增延迟（1.5s → 3s → 6s）
        """
        consecutive_dead: int = 0
        self._session_restart_count = 0
        while not self._stopped:
            sess = _get_persistent_device(self.serial)
            if not sess or not sess.running:
                consecutive_dead += 1
                # 首次检测到 session 死亡时记录日志
                if consecutive_dead == 1:
                    uat_logger.warning(
                        "scrcpy relay 检测到会话停止 serial=%s (restart_count=%d/%d)",
                        self.serial,
                        self._session_restart_count,
                        self._MAX_SESSION_RESTARTS,
                    )
                # 等待 ~1.2 秒确认不是瞬态后，尝试重启
                if consecutive_dead >= 10:
                    if self._session_restart_count < self._MAX_SESSION_RESTARTS:
                        delay = 1.5 * (2 ** self._session_restart_count)
                        uat_logger.info(
                            "scrcpy relay 尝试重启会话 serial=%s (attempt %d/%d, delay=%.1fs)",
                            self.serial,
                            self._session_restart_count + 1,
                            self._MAX_SESSION_RESTARTS,
                            delay,
                        )
                        time.sleep(delay)
                        # 清理已死的 persistent session，确保能创建新的
                        self._clean_dead_session()
                        ok, err = ensure_scrcpy_device_session(self.serial)
                        if ok:
                            self._session_restart_count += 1
                            consecutive_dead = 0
                            uat_logger.info(
                                "scrcpy relay 会话重启成功 serial=%s (attempt %d)",
                                self.serial,
                                self._session_restart_count,
                            )
                            continue
                        else:
                            self._session_restart_count += 1
                            uat_logger.error(
                                "scrcpy relay 会话重启失败 serial=%s (attempt %d): %s",
                                self.serial,
                                self._session_restart_count,
                                err,
                            )
                            consecutive_dead = 0
                    else:
                        # 已达重启上限，保持等待但不再重试
                        uat_logger.error(
                            "scrcpy relay 已达重启上限 serial=%s，等待用户手动重连",
                            self.serial,
                        )
                time.sleep(0.12)
                continue
            # session 健康，重置计数器
            consecutive_dead = 0
            if self._session_restart_count > 0:
                self._session_restart_count = 0
                uat_logger.info("scrcpy relay 会话恢复正常 serial=%s", self.serial)
            packet = sess.read_packet()
            if not packet:
                time.sleep(0.02)
                continue
            self._last_frame_time = time.time()
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

    def _clean_dead_session(self) -> None:
        """清理已停止的 persistent session（避免 ensure_scrcpy_device_session 判定为已有 running 会话）。"""
        with _persistent_lock:
            sess = _persistent_sessions.get(self.serial)
            if sess and not sess.running:
                try:
                    sess.stop()
                except Exception:
                    pass
                del _persistent_sessions[self.serial]

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

    当 scrcpy-server 异常退出时，不立即断开 HTTP 流，而是等待 relay 自动重启会话
    （最多等待 30 秒），给 relay._reader_loop 中的自动重启机制留出恢复时间。
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
    idle_since: Optional[float] = None  # session 死亡时间戳
    max_idle_seconds = 30.0  # 等待 relay 重启的最大时长
    warned_idle = False
    try:
        while not relay._stopped:
            try:
                packet = pkt_queue.get(timeout=5.0)
            except queue.Empty:
                sess = _get_persistent_device(serial)
                if not sess or not sess.running:
                    now = time.time()
                    if idle_since is None:
                        idle_since = now
                        uat_logger.warning(
                            "scrcpy HTTP 检测到会话停止 serial=%s，等待 relay 自动重启 (max %ds)",
                            serial, int(max_idle_seconds),
                        )
                    elif now - idle_since > max_idle_seconds:
                        uat_logger.error(
                            "scrcpy HTTP 等待会话重启超时 serial=%s (%.1fs)，断开流",
                            serial, now - idle_since,
                        )
                        break
                    elif not warned_idle and now - idle_since > 10.0:
                        warned_idle = True
                        uat_logger.warning(
                            "scrcpy HTTP 会话仍不可用 serial=%s，继续等待 (已等 %.0fs)",
                            serial, now - idle_since,
                        )
                    # 使用短超时快速检查 session 是否恢复
                    try:
                        packet = pkt_queue.get(timeout=2.0)
                        # 收到了 packet，session 已恢复
                        idle_since = None
                        warned_idle = False
                        uat_logger.info("scrcpy HTTP 会话已恢复 serial=%s", serial)
                    except queue.Empty:
                        continue
                else:
                    continue
            if isinstance(packet, ScrcpyPacket) and not packet.payload:
                # 空载荷（可能是心跳或异常），跳过
                continue
            if not packet:
                # sentinel：relay 明确要求断开
                uat_logger.info("scrcpy HTTP 收到断开信令 serial=%s", serial)
                break
            # 收到有效帧，重置空闲状态
            if idle_since is not None:
                idle_since = None
                warned_idle = False
                uat_logger.info("scrcpy HTTP 会话恢复 serial=%s", serial)
            yield pack_scrcpy_frame(packet)
    except GeneratorExit:
        # Flask 客户端主动断开（正常情况），不记录为异常
        pass
    except Exception as exc:
        uat_logger.warning("scrcpy HTTP 流异常结束 serial=%s: %s", serial, exc)
    finally:
        relay.unsubscribe(sid)
        uat_logger.info("scrcpy HTTP 流结束 serial=%s", serial)


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
        "ws_url": scrcpy_bridge_url(_bridge_connect_host(host)),
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
