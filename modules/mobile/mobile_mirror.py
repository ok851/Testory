# -*- coding: utf-8 -*-
"""
Android 设备投屏会话管理。

投屏方案：在用户本地电脑启动手机画面投屏（scrcpy -s <serial> 弹出独立窗口）。
与内嵌 bridge（adb shell scrcpy-server）互斥：外置窗口运行时禁止 warm/bridge 抢占，
否则会杀设备端 server 导致 scrcpy 窗口自关。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from modules.mobile.mobile_device_manager import set_connected_udid
from modules.mobile.mobile_env_config import scrcpy_available, scrcpy_path

try:
    from uat_logger import uat_logger
except ImportError:
    import logging
    uat_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_sessions: Dict[str, Dict[str, Any]] = {}

_external_lock = threading.Lock()
# serial → 已确认的 scrcpy.exe PID 集合（可多个；启动后以 tasklist 为准）
_external_pids: Dict[str, Set[int]] = {}


def _run_cmd(cmd: list, timeout: int = 8) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def _get_scrcpy_pids() -> List[int]:
    """当前所有 scrcpy.exe PID。"""
    if os.name != "nt":
        return []
    pids: List[int] = []
    try:
        result = _run_cmd(["tasklist", "/FI", "IMAGENAME eq scrcpy.exe", "/FO", "CSV", "/NH"])
        raw = result.stdout or b""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if not line or "scrcpy.exe" not in line.lower():
                continue
            # CSV: "scrcpy.exe","1234","..."
            parts = line.strip('"').split('","')
            if len(parts) >= 2:
                try:
                    pids.append(int(parts[1]))
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass
    return pids


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    return pid in _get_scrcpy_pids()


def _kill_pid_tree(pid: int) -> bool:
    if os.name != "nt" or pid <= 0:
        return False
    try:
        result = _run_cmd(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def _kill_all_scrcpy_exe() -> List[int]:
    """强制结束本机全部 scrcpy.exe（用户点「关闭手机画面」时必须清干净）。"""
    killed: List[int] = []
    for pid in list(_get_scrcpy_pids()):
        if _kill_pid_tree(pid):
            killed.append(pid)
    # 再扫一遍残留
    time.sleep(0.25)
    for pid in list(_get_scrcpy_pids()):
        if _kill_pid_tree(pid):
            killed.append(pid)
    return killed


def _noconsole_vbs_path(exe: str) -> str:
    """官方 Windows 包自带 scrcpy-noconsole.vbs（隐藏 cmd 黑框）。"""
    d = os.path.dirname(exe or "") or ""
    vbs = os.path.join(d, "scrcpy-noconsole.vbs")
    return vbs if os.path.isfile(vbs) else ""


def _stop_bridge_session(serial: str) -> None:
    try:
        from modules.mobile.mobile_scrcpy_bridge import stop_scrcpy_device_session

        stop_scrcpy_device_session(serial)
    except Exception as exc:
        uat_logger.debug("stop bridge before external scrcpy: %s", exc)


def _kill_device_scrcpy_server(serial: str) -> None:
    """清理手机端残留 scrcpy-server，避免下次外置窗口起不来。"""
    serial = (serial or "").strip()
    if not serial:
        return
    try:
        from modules.mobile.mobile_scrcpy_bridge import _kill_stale_scrcpy_servers

        _kill_stale_scrcpy_servers(serial)
    except Exception:
        try:
            from modules.mobile.mobile_env_config import adb_path

            subprocess.run(
                [adb_path(), "-s", serial, "shell", "pkill", "-f", "com.genymobile.scrcpy.Server"],
                capture_output=True,
                timeout=8,
                check=False,
            )
        except Exception:
            pass


def stop_external_before_bridge(serial: str) -> None:
    """仅在明确需要切换到内嵌 bridge 时调用。

    注意：共享屏幕 / agent 预热不应调用此函数去「抢」外置窗口。
    """
    serial = (serial or "").strip()
    if not serial:
        return
    stop_external_scrcpy(serial, force_kill_all=True)


def is_external_scrcpy_running(serial: str = "") -> bool:
    return bool(external_scrcpy_status(serial).get("running"))


def start_scrcpy_mirror(udid: str) -> Dict[str, Any]:
    udid = (udid or "").strip()
    session_id = str(uuid.uuid4())
    with _lock:
        _sessions[session_id] = {
            "udid": udid,
            "scrcpy_proc": None,
            "active": True,
            "started_at": time.time(),
        }
    set_connected_udid(udid)
    return {
        "session_id": session_id,
        "mirror_ws_path": f"/api/mobile/mirror/stream?session_id={session_id}",
        "scrcpy_started": False,
    }


def stop_mirror(session_id: str) -> None:
    with _lock:
        sess = _sessions.pop(session_id, None)
    if not sess:
        return
    sess["active"] = False
    set_connected_udid(None)


def get_mirror_session(session_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _sessions.get(session_id)


def launch_external_scrcpy(udid: str) -> Dict[str, Any]:
    """启动外置投屏：优先 scrcpy-noconsole.vbs，不弹重复终端黑框。"""
    serial = (udid or "").strip()
    if not serial:
        return {"success": False, "error": "缺少设备 serial，无法启动投屏"}
    if not scrcpy_available():
        return {
            "success": False,
            "error": "未安装投屏插件，请在插件市场安装「scrcpy 投屏 + 反控」后重试",
        }
    exe = scrcpy_path()
    if not exe or not os.path.isfile(exe):
        return {"success": False, "error": f"未找到 scrcpy 可执行文件: {exe or 'scrcpy'}"}

    scrcpy_dir = os.path.dirname(exe) or os.getcwd()

    # 1) 停内嵌 bridge + 清干净旧 scrcpy.exe + 清设备 server
    _stop_bridge_session(serial)
    _kill_all_scrcpy_exe()
    _kill_device_scrcpy_server(serial)
    with _external_lock:
        _external_pids.clear()
    time.sleep(0.5)

    before = set(_get_scrcpy_pids())

    # 2) 官方无控制台启动方式
    vbs = _noconsole_vbs_path(exe)
    launched_via = ""
    try:
        if vbs and os.name == "nt":
            # wscript //B //Nologo scrcpy-noconsole.vbs -s SERIAL
            # vbs 内部：Run "cmd /c scrcpy.exe ...", 0, false → 窗口样式 0=隐藏
            cmd = [
                "wscript.exe",
                "//B",
                "//Nologo",
                vbs,
                "-s",
                serial,
            ]
            subprocess.Popen(
                cmd,
                cwd=scrcpy_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0x08000000),
            )
            launched_via = "scrcpy-noconsole.vbs"
        else:
            # 非 Windows / 无 vbs：CREATE_NO_WINDOW 直启
            flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
            if os.name == "nt":
                flags |= 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
            kwargs: Dict[str, Any] = {
                "cwd": scrcpy_dir,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }
            if flags:
                kwargs["creationflags"] = flags
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen([exe, "-s", serial], **kwargs)
            launched_via = "scrcpy.exe"
    except Exception as exc:
        return {"success": False, "error": f"投屏启动失败: {exc}"}

    # 3) 等待 scrcpy.exe 出现（vbs/cmd 是瞬时进程，不能记它们的 PID）
    new_pids: List[int] = []
    for _ in range(20):  # ~6s
        time.sleep(0.3)
        now = set(_get_scrcpy_pids())
        born = sorted(now - before)
        if born:
            new_pids = born
            break
        if now and not before:
            new_pids = sorted(now)
            break

    if not new_pids:
        return {
            "success": False,
            "error": (
                "手机画面投屏启动失败：未检测到 scrcpy.exe。"
                "请确认设备已连接、已解锁并授权 USB 调试后重试。"
            ),
            "launcher": launched_via,
        }

    with _external_lock:
        _external_pids[serial] = set(new_pids)

    uat_logger.info(
        "scrcpy 外置窗口已启动 serial=%s pids=%s via=%s",
        serial,
        new_pids,
        launched_via,
    )
    return {
        "success": True,
        "pid": new_pids[0],
        "pids": new_pids,
        "message": "已启动手机画面投屏",
        "mode": "external_scrcpy_exe",
        "launcher": launched_via,
    }


def stop_external_scrcpy(udid: str = "", *, force_kill_all: bool = True) -> Dict[str, Any]:
    """关闭外置投屏。默认杀掉本机全部 scrcpy.exe，避免「点了关闭但进程还在」。"""
    serial = (udid or "").strip()

    with _external_lock:
        tracked = set()
        if serial and serial in _external_pids:
            tracked |= set(_external_pids.pop(serial, set()) or set())
        if force_kill_all:
            # 清空全部跟踪（单机常见只有一台手机）
            for s in list(_external_pids.keys()):
                tracked |= set(_external_pids.pop(s, set()) or set())

    killed: List[int] = []
    for pid in tracked:
        if _kill_pid_tree(pid):
            killed.append(pid)

    if force_kill_all or not killed:
        killed.extend(_kill_all_scrcpy_exe())

    if serial:
        _kill_device_scrcpy_server(serial)

    still = _get_scrcpy_pids()
    ok = len(still) == 0
    return {
        "success": True,
        "message": "已关闭手机画面投屏" if ok else "已尝试关闭，仍有残留进程",
        "killed_pids": sorted(set(killed)),
        "remaining_pids": still,
        "already_stopped": not killed and ok,
    }


def external_scrcpy_status(udid: str = "") -> Dict[str, Any]:
    """以 tasklist 中的 scrcpy.exe 为准判断是否在跑。"""
    serial = (udid or "").strip()
    available = scrcpy_available()
    live = _get_scrcpy_pids()

    with _external_lock:
        tracked = set()
        if serial:
            tracked = set(_external_pids.get(serial) or set())
            # 清理已死 PID
            alive_tracked = {p for p in tracked if p in live}
            if alive_tracked:
                _external_pids[serial] = alive_tracked
            elif serial in _external_pids:
                _external_pids.pop(serial, None)
            tracked = alive_tracked
        else:
            for s, ps in list(_external_pids.items()):
                alive_tracked = {p for p in (ps or set()) if p in live}
                if alive_tracked:
                    _external_pids[s] = alive_tracked
                else:
                    _external_pids.pop(s, None)

    running = bool(live)
    pid = None
    source = "none"
    if tracked:
        pid = sorted(tracked)[0]
        source = "tracked_pid"
    elif live:
        pid = live[0]
        source = "tasklist"
        if serial:
            with _external_lock:
                _external_pids[serial] = set(live)

    return {
        "running": running,
        "available": available,
        "pid": pid,
        "pids": live,
        "source": source,
    }


def disconnect_all_mirrors() -> None:
    stop_external_scrcpy("", force_kill_all=True)
    with _lock:
        ids = list(_sessions.keys())
    for sid in ids:
        stop_mirror(sid)
