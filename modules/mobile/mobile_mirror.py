# -*- coding: utf-8 -*-
"""
Android 设备投屏会话管理。

投屏方案：在用户本地电脑启动手机画面投屏（scrcpy -s <serial> 弹出独立窗口）。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from modules.mobile.mobile_device_manager import set_connected_udid
from modules.mobile.mobile_env_config import scrcpy_available, scrcpy_path

try:
    from uat_logger import uat_logger
except ImportError:
    import logging
    uat_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_sessions: Dict[str, Dict[str, Any]] = {}

# 手机画面投屏进程（按 serial 维护）
_external_lock = threading.Lock()
_external_procs: Dict[str, subprocess.Popen] = {}


def _run_cmd(cmd: list, timeout: int = 5) -> subprocess.CompletedProcess:
    """执行命令行命令并返回结果。"""
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def _is_process_alive(proc: Optional[subprocess.Popen]) -> bool:
    """判断进程是否存活。"""
    if proc is None:
        return False
    try:
        return proc.poll() is None
    except Exception:
        return False


def _get_scrcpy_pids() -> List[int]:
    """获取当前所有 scrcpy 进程的 PID 列表。"""
    if os.name != "nt":
        return []
    pids = []
    try:
        result = _run_cmd(["tasklist", "/FI", "IMAGENAME eq scrcpy.exe", "/FO", "CSV", "/NH"])
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if line and '"scrcpy.exe"' in line:
                parts = line.split('","')
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1])
                        pids.append(pid)
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass
    return pids


def _kill_pid_tree(pid: int) -> bool:
    """杀掉指定 PID 的进程及其子进程树。"""
    if os.name != "nt" or pid <= 0:
        return False
    try:
        result = _run_cmd(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=8)
        return result.returncode == 0
    except Exception:
        return False


def _cleanup_stale_processes() -> None:
    """清理所有残留的 scrcpy 进程及其子进程。

    关键：使用 /T 标志杀掉整个进程树（包括 adb 子进程），
    确保端口被释放。
    """
    if os.name != "nt":
        return

    pids = _get_scrcpy_pids()
    for pid in pids:
        _kill_pid_tree(pid)

    # 等待端口释放
    if pids:
        time.sleep(0.3)


def start_scrcpy_mirror(udid: str) -> Dict[str, Any]:
    """注册投屏会话。"""
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
    """停止投屏会话与 scrcpy 子进程。"""
    with _lock:
        sess = _sessions.pop(session_id, None)
    if not sess:
        return
    sess["active"] = False
    proc = sess.get("scrcpy_proc")
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    set_connected_udid(None)


def get_mirror_session(session_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _sessions.get(session_id)


def launch_external_scrcpy(udid: str) -> Dict[str, Any]:
    """在用户本地电脑启动手机画面投屏窗口。

    关键流程：
    1. 清理所有残留 scrcpy 进程（包括子进程）
    2. 使用 DETACHED_PROCESS 启动新进程
    3. 验证进程是否存活
    """
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

    # 第一步：清理记录中的旧进程
    with _external_lock:
        old_proc = _external_procs.pop(serial, None)

    if old_proc is not None and _is_process_alive(old_proc):
        _kill_pid_tree(old_proc.pid)

    # 第二步：清理所有残留的 scrcpy 进程（关键！杀掉进程树释放端口）
    _cleanup_stale_processes()

    # 第三步：启动 scrcpy
    cmd = [exe, "-s", serial]
    proc = None
    try:
        kwargs: Dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            # DETACHED_PROCESS：子进程不继承父进程的控制台
            kwargs["creationflags"] = 0x00000008

        proc = subprocess.Popen(cmd, **kwargs)
    except FileNotFoundError as exc:
        return {"success": False, "error": f"投屏启动失败: {exc}"}
    except Exception as exc:
        return {"success": False, "error": f"投屏启动失败: {exc}"}

    # 第四步：注册进程
    with _external_lock:
        _external_procs[serial] = proc

    # 第五步：验证进程是否存活
    time.sleep(0.5)

    if not _is_process_alive(proc):
        with _external_lock:
            _external_procs.pop(serial, None)
        return {
            "success": False,
            "error": "手机画面投屏启动失败：scrcpy 进程启动后立即退出。请确认设备已正确连接并授权 USB 调试。",
        }

    uat_logger.info("scrcpy 已启动 serial=%s pid=%s", serial, proc.pid)

    return {
        "success": True,
        "pid": proc.pid,
        "message": "已启动手机画面投屏",
    }


def stop_external_scrcpy(udid: str) -> Dict[str, Any]:
    """关闭指定设备的手机画面投屏窗口。"""
    serial = (udid or "").strip()
    if not serial:
        return {"success": False, "error": "缺少设备 serial"}

    with _external_lock:
        proc = _external_procs.pop(serial, None)

    # 有记录
    if proc is not None:
        if _is_process_alive(proc):
            # 进程存活，杀掉进程树
            if os.name == "nt":
                _kill_pid_tree(proc.pid)
            else:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            return {"success": True, "message": "已关闭手机画面投屏"}
        else:
            # 进程已退出（用户手动关闭），清理记录即可
            return {"success": True, "message": "手机画面投屏已关闭"}

    # 无记录，清理所有残留
    _cleanup_stale_processes()
    return {"success": True, "message": "无运行中的投屏窗口", "already_stopped": True}


def external_scrcpy_status(udid: str) -> Dict[str, Any]:
    """返回手机画面投屏运行状态。"""
    serial = (udid or "").strip()
    if not serial:
        return {"running": False, "available": scrcpy_available()}

    with _external_lock:
        proc = _external_procs.get(serial)
        if proc is not None:
            if not _is_process_alive(proc):
                _external_procs.pop(serial, None)
                proc = None

    running = proc is not None and _is_process_alive(proc)

    return {
        "running": running,
        "available": scrcpy_available(),
        "pid": proc.pid if running else None,
    }


def disconnect_all_mirrors() -> None:
    with _external_lock:
        serials = list(_external_procs.keys())
    for s in serials:
        stop_external_scrcpy(s)
    # 彻底清理所有残留
    _cleanup_stale_processes()
    with _lock:
        ids = list(_sessions.keys())
    for sid in ids:
        stop_mirror(sid)
