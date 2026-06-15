# -*- coding: utf-8 -*-
"""插件 JSON-RPC 客户端（经 adb forward 访问设备本地 HTTP 服务）。"""
from __future__ import annotations

import base64
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from mobile_env_config import adb_path

_PACKAGE = "com.testory.assistant"
_RPC_ID = 0
_RPC_LOCK = threading.Lock()

# udid -> { host_port, device_port, last_ping }
_forward_state: Dict[str, Dict[str, Any]] = {}
_forward_lock = threading.Lock()


def _next_id() -> int:
    global _RPC_ID
    with _RPC_LOCK:
        _RPC_ID += 1
        return _RPC_ID


def _adb_cmd(udid: str = "") -> List[str]:
    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    return cmd


def _run_adb(args: List[str], *, timeout: int = 30) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:
        return 1, "", str(exc)


def clear_forward(udid: str) -> None:
    with _forward_lock:
        st = _forward_state.pop(udid, None)
    if not st:
        return
    host_port = st.get("host_port")
    if host_port:
        _run_adb(_adb_cmd(udid) + ["forward", "--remove", f"tcp:{host_port}"])


def setup_forward(udid: str, device_port: int, *, host_port: Optional[int] = None) -> Tuple[bool, int, str]:
    """建立 adb forward，返回 (ok, host_port, message)。"""
    clear_forward(udid)
    hp = int(host_port or 0)
    if hp <= 0:
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        hp = int(sock.getsockname()[1])
        sock.close()
    rc, out, err = _run_adb(
        _adb_cmd(udid) + ["forward", f"tcp:{hp}", f"tcp:{device_port}"]
    )
    if rc != 0:
        return False, hp, err or out or "adb forward 失败"
    with _forward_lock:
        _forward_state[udid] = {
            "host_port": hp,
            "device_port": device_port,
            "last_ping": time.time(),
        }
    return True, hp, f"forward tcp:{hp} -> device:{device_port}"


def get_forward_port(udid: str) -> Optional[int]:
    with _forward_lock:
        st = _forward_state.get(udid)
        return int(st["host_port"]) if st else None


def discover_plugin_port(udid: str) -> Tuple[Optional[int], str]:
    """读取插件 HTTP 端口（external files / shared_prefs / getPort RPC）。"""
    for shell_cmd in (
        f"cat /sdcard/Android/data/{_PACKAGE}/files/plugin_port.txt 2>/dev/null",
        f"cat /storage/emulated/0/Android/data/{_PACKAGE}/files/plugin_port.txt 2>/dev/null",
    ):
        rc, out, _ = _run_adb(_adb_cmd(udid) + ["shell", shell_cmd], timeout=10)
        if rc == 0 and out.strip().isdigit():
            return int(out.strip()), ""
    rc, out, err = _run_adb(
        _adb_cmd(udid)
        + [
            "shell",
            "run-as",
            _PACKAGE,
            "cat",
            "shared_prefs/plugin_server.xml",
        ],
        timeout=15,
    )
    if rc == 0 and out:
        import re

        m = re.search(r'name="port"[^>]*value="(\d+)"', out)
        if not m:
            m = re.search(r'value="(\d+)"[^>]*name="port"', out)
        if m:
            return int(m.group(1)), ""
    hp = get_forward_port(udid)
    if hp:
        try:
            res = _rpc_call(udid, "getPort", {}, host_port=hp, timeout=3.0)
            port = int(res.get("port") or 0)
            if port > 0:
                return port, ""
        except Exception:
            pass
    return None, err or "无法读取插件端口，请确认已开启无障碍服务"


def ensure_plugin_tunnel(udid: str) -> Tuple[bool, str]:
    """确保 adb forward 已建立；必要时读取插件端口并 forward。"""
    port, msg = discover_plugin_port(udid)
    if not port:
        # 尝试 RPC getPort（若已有 forward）
        hp = get_forward_port(udid)
        if hp:
            try:
                res = _rpc_call(udid, "getPort", {}, host_port=hp, timeout=3.0)
                port = int(res.get("port") or 0)
            except Exception:
                pass
    if not port or port <= 0:
        return False, msg or "插件 HTTP 服务未启动，请开启无障碍服务"
    ok, hp, fwd_msg = setup_forward(udid, port)
    if not ok:
        return False, fwd_msg
    return True, fwd_msg


def _rpc_call(
    udid: str,
    method: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    host_port: Optional[int] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    hp = host_port or get_forward_port(udid)
    if not hp:
        ok, msg = ensure_plugin_tunnel(udid)
        if not ok:
            raise RuntimeError(msg)
        hp = get_forward_port(udid)
    if not hp:
        raise RuntimeError("adb forward 未就绪")
    payload = {
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": method,
        "params": params or {},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{hp}/",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            body = json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(err_body or str(e)) from e
    except Exception as e:
        raise RuntimeError(str(e)) from e
    if body.get("error"):
        err = body["error"]
        if isinstance(err, dict):
            raise RuntimeError(err.get("message") or str(err))
        raise RuntimeError(str(err))
    result = body.get("result")
    if isinstance(result, dict):
        with _forward_lock:
            st = _forward_state.get(udid)
            if st:
                st["last_ping"] = time.time()
    return result if isinstance(result, dict) else {"value": result}


def ping_plugin(udid: str) -> Tuple[bool, str]:
    try:
        _rpc_call(udid, "ping", {}, timeout=3.0)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def plugin_status(udid: str) -> Dict[str, Any]:
    try:
        return _rpc_call(udid, "getStatus", {}, timeout=5.0)
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


def start_recording(udid: str, *, screenshot_per_step: bool = True) -> Dict[str, Any]:
    return _rpc_call(
        udid,
        "startRecording",
        {"screenshotPerStep": screenshot_per_step},
        timeout=10.0,
    )


def stop_recording(udid: str) -> Dict[str, Any]:
    return _rpc_call(udid, "stopRecording", {}, timeout=10.0)


def poll_steps(udid: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    res = _rpc_call(udid, "pollSteps", {"limit": max(1, limit)}, timeout=5.0)
    steps = res.get("steps") or []
    return steps if isinstance(steps, list) else []


def get_page_source(udid: str) -> Dict[str, Any]:
    return _rpc_call(udid, "getPageSource", {}, timeout=10.0)


def take_screenshot(udid: str) -> Tuple[bytes, str]:
    res = _rpc_call(udid, "takeScreenshot", {}, timeout=15.0)
    b64 = res.get("image_base64") or res.get("data") or ""
    fmt = (res.get("format") or "jpeg").lower()
    if not b64:
        raise RuntimeError("插件未返回截图数据")
    return base64.b64decode(b64), fmt


def plugin_tap(udid: str, *, selector_type: str = "", selector_value: str = "", x: int = 0, y: int = 0) -> Dict[str, Any]:
    return _rpc_call(
        udid,
        "tap",
        {
            "selectorType": selector_type,
            "selectorValue": selector_value,
            "x": x,
            "y": y,
        },
        timeout=15.0,
    )


def plugin_swipe(
    udid: str,
    *,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> Dict[str, Any]:
    return _rpc_call(
        udid,
        "swipe",
        {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        timeout=15.0,
    )


def plugin_input(
    udid: str,
    *,
    text: str,
    selector_type: str = "",
    selector_value: str = "",
) -> Dict[str, Any]:
    return _rpc_call(
        udid,
        "input",
        {
            "text": text,
            "selectorType": selector_type,
            "selectorValue": selector_value,
        },
        timeout=15.0,
    )


def restart_plugin_service(udid: str) -> Tuple[bool, str]:
    rc, out, err = _run_adb(
        _adb_cmd(udid)
        + [
            "shell",
            "am",
            "startservice",
            "-n",
            f"{_PACKAGE}/.PluginForegroundService",
        ],
        timeout=15,
    )
    if rc != 0:
        rc2, _, err2 = _run_adb(
            _adb_cmd(udid)
            + ["shell", "am", "start", "-n", f"{_PACKAGE}/.MainActivity"],
            timeout=15,
        )
        if rc2 != 0:
            return False, err or err2 or "无法重启插件服务"
    time.sleep(1.0)
    return ensure_plugin_tunnel(udid)


def seconds_since_ping(udid: str) -> float:
    with _forward_lock:
        st = _forward_state.get(udid)
        if not st:
            return 9999.0
        return time.time() - float(st.get("last_ping") or 0)
