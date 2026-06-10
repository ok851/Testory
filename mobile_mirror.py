# -*- coding: utf-8 -*-
"""
Android 设备投屏：scrcpy 外窗 + adb screencap WebSocket 帧流。
"""

from __future__ import annotations

import asyncio
import base64
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, Optional

from mobile_device_manager import capture_screenshot_frame, set_connected_udid
from mobile_env_config import mirror_fps, scrcpy_path

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_sessions: Dict[str, Dict[str, Any]] = {}


def start_scrcpy_mirror(udid: str) -> Dict[str, Any]:
    """
    启动 scrcpy 独立窗口，并注册 adb screencap 帧流会话。

    Returns:
        {session_id, mirror_ws_path, scrcpy_started}
    """
    udid = (udid or "").strip()
    session_id = str(uuid.uuid4())
    scrcpy_proc: Optional[subprocess.Popen] = None
    scrcpy_started = False
    # 模拟器永不弹出 scrcpy.exe 外窗（画面走 scrcpy_ws 或平台内 screencap 画布）
    if not udid.startswith("emulator-"):
        scrcpy_exe = scrcpy_path()
        cmd = [scrcpy_exe]
        if udid:
            cmd.extend(["-s", udid])
        scrcpy_fps = max(15, min(60, mirror_fps()))
        cmd.extend(["--no-audio", "--max-size", "720", "--max-fps", str(scrcpy_fps)])
        try:
            scrcpy_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
            )
            scrcpy_started = True
            uat_logger.info("scrcpy 已启动: udid=%s pid=%s", udid or "(default)", scrcpy_proc.pid)
        except FileNotFoundError:
            uat_logger.warning("未找到 scrcpy（SCRCPY_PATH=%s），仅使用 adb 截图投屏", scrcpy_exe)
        except Exception as exc:
            uat_logger.warning("启动 scrcpy 失败: %s", exc)

    with _lock:
        _sessions[session_id] = {
            "udid": udid,
            "scrcpy_proc": scrcpy_proc,
            "active": True,
            "started_at": time.time(),
        }
    set_connected_udid(udid)
    return {
        "session_id": session_id,
        "mirror_ws_path": f"/api/mobile/mirror/stream?session_id={session_id}",
        "scrcpy_started": scrcpy_started,
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


async def stream_mirror_frames(websocket: Any, session_id: str) -> None:
    """
    WebSocket 帧流：周期性 adb screencap，以 JSON {type, data(base64)} 推送。
    """
    sess = get_mirror_session(session_id)
    if not sess:
        await websocket.send('{"type":"error","message":"invalid session"}')
        return
    udid = sess.get("udid") or ""
    interval = 1.0 / max(1, mirror_fps())
    try:
        await websocket.send('{"type":"ready","message":"mirror stream started"}')
        while sess.get("active"):
            png, fmt = await asyncio.to_thread(capture_screenshot_frame, udid)
            if png:
                b64 = base64.b64encode(png).decode("ascii")
                await websocket.send(f'{{"type":"frame","format":"{fmt}","data":"{b64}"}}')
            await asyncio.sleep(interval)
            sess = get_mirror_session(session_id)
            if not sess:
                break
    except Exception as exc:
        uat_logger.debug("mirror stream ended: %s", exc)
    finally:
        try:
            await websocket.send('{"type":"closed"}')
        except Exception:
            pass


def disconnect_all_mirrors() -> None:
    with _lock:
        ids = list(_sessions.keys())
    for sid in ids:
        stop_mirror(sid)
