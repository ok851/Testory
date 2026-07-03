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


def pause_recording(udid: str) -> Dict[str, Any]:
    return _rpc_call(udid, "pauseRecording", {}, timeout=10.0)


def resume_recording(udid: str) -> Dict[str, Any]:
    return _rpc_call(udid, "resumeRecording", {}, timeout=10.0)


def poll_steps(udid: str, *, limit: int = 20) -> Dict[str, Any]:
    res = _rpc_call(udid, "pollSteps", {"limit": max(1, limit)}, timeout=5.0)
    steps = res.get("steps") or []
    return {
        "steps": steps if isinstance(steps, list) else [],
        "recording_active": bool(res.get("recording_active", True)),
    }


def get_page_source(udid: str) -> Dict[str, Any]:
    return _rpc_call(udid, "getPageSource", {}, timeout=10.0)


def take_screenshot(udid: str) -> Tuple[bytes, Dict[str, Any]]:
    res = _rpc_call(udid, "takeScreenshot", {}, timeout=15.0)
    b64 = res.get("image_base64") or res.get("data") or ""
    fmt = (res.get("format") or "jpeg").lower()
    if not b64:
        raise RuntimeError("插件未返回截图数据")
    return base64.b64decode(b64), {
        "format": fmt,
        "width": int(res.get("width") or 0),
        "height": int(res.get("height") or 0),
    }


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
    """尝试唤醒插件 HTTP 隧道；绝不启动 MainActivity，避免连接/安装后自动弹出 App。"""
    _run_adb(
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
    time.sleep(0.5)
    return ensure_plugin_tunnel(udid)


def seconds_since_ping(udid: str) -> float:
    with _forward_lock:
        st = _forward_state.get(udid)
        if not st:
            return 9999.0
        return time.time() - float(st.get("last_ping") or 0)


def dismiss_dialogs(udid: str) -> Dict[str, Any]:
    """尝试点击常见系统弹窗按钮。"""
    try:
        return _rpc_call(udid, "dismissDialogs", {}, timeout=8.0)
    except Exception as exc:
        try:
            # 根据设备屏幕尺寸动态计算弹窗按钮位置（屏幕中心偏下）
            from mobile_adb_control import adb_get_screen_size
            sw, sh = adb_get_screen_size(udid)
            x = sw // 2
            y = int(sh * 0.625)
            return plugin_tap(udid, x=x, y=y)
        except Exception:
            return {"ok": False, "error": str(exc)}


def _step_mobile_spec(step: Dict[str, Any]) -> Dict[str, Any]:
    spec = step.get("mobile_spec")
    if isinstance(spec, dict):
        return spec
    if isinstance(spec, str) and spec.strip():
        try:
            parsed = json.loads(spec)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _resolve_tap_coords(step: Dict[str, Any], spec: Dict[str, Any]) -> Tuple[int, int]:
    """解析点击坐标：viewport_coord > rx/ry > node_rx/node_ry + bounds。"""
    x = y = 0
    st = (step.get("selector_type") or "").strip()
    sv = (step.get("selector_value") or "").strip()
    vc = spec.get("viewport_coord") if isinstance(spec.get("viewport_coord"), dict) else {}
    x = int(vc.get("x") or spec.get("x") or 0)
    y = int(vc.get("y") or spec.get("y") or 0)
    if st == "viewport_coord" and sv:
        try:
            coord = json.loads(sv)
            x = int(coord.get("x") or x)
            y = int(coord.get("y") or y)
            rx = coord.get("rx")
            ry = coord.get("ry")
            sw = int(spec.get("screen_width") or 0)
            sh = int(spec.get("screen_height") or 0)
            if rx is not None and ry is not None and sw > 0 and sh > 0:
                x = int(round(float(rx) * sw))
                y = int(round(float(ry) * sh))
        except Exception:
            pass
    if (x <= 0 and y <= 0) and vc:
        rx = vc.get("rx")
        ry = vc.get("ry")
        sw = int(spec.get("screen_width") or 0)
        sh = int(spec.get("screen_height") or 0)
        if rx is not None and ry is not None and sw > 0 and sh > 0:
            x = int(round(float(rx) * sw))
            y = int(round(float(ry) * sh))
    # SoloPi: 节点内相对坐标
    if (x <= 0 and y <= 0) and spec.get("node_rx") is not None and spec.get("node_ry") is not None:
        bounds = spec.get("bounds")
        if isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
            left, top, right, bottom = [int(v) for v in bounds[:4]]
            bw, bh = right - left, bottom - top
            if bw > 0 and bh > 0:
                x = left + int(round(float(spec["node_rx"]) * bw))
                y = top + int(round(float(spec["node_ry"]) * bh))
    return x, y


def _resolve_swipe_coords(spec: Dict[str, Any]) -> Tuple[int, int, int, int]:
    sw = int(spec.get("screen_width") or 0)
    sh = int(spec.get("screen_height") or 0)
    x1 = int(spec.get("x1") or 0)
    y1 = int(spec.get("y1") or 0)
    x2 = int(spec.get("x2") or x1)
    y2 = int(spec.get("y2") or y1)
    if sw > 0 and sh > 0:
        if spec.get("rx1") is not None:
            x1 = int(round(float(spec["rx1"]) * sw))
        if spec.get("ry1") is not None:
            y1 = int(round(float(spec["ry1"]) * sh))
        if spec.get("rx2") is not None:
            x2 = int(round(float(spec["rx2"]) * sw))
        if spec.get("ry2") is not None:
            y2 = int(round(float(spec["ry2"]) * sh))
    return x1, y1, x2, y2


def plugin_long_press(
    udid: str,
    *,
    selector_type: str = "",
    selector_value: str = "",
    x: int = 0,
    y: int = 0,
) -> Dict[str, Any]:
    return _rpc_call(
        udid,
        "longPress",
        {
            "selectorType": selector_type,
            "selectorValue": selector_value,
            "x": x,
            "y": y,
        },
        timeout=15.0,
    )


def replay_step(udid: str, step: Dict[str, Any], *, step_index: int = 0) -> Dict[str, Any]:
    """将用例步骤映射为插件 tap/swipe/input RPC。"""
    action = (step.get("action") or "").strip().lower()
    spec = _step_mobile_spec(step)

    if action in ("tap", "click"):
        x, y = _resolve_tap_coords(step, spec)
        st = (step.get("selector_type") or "").strip()
        sv = (step.get("selector_value") or "").strip()
        res = plugin_tap(udid, selector_type=st, selector_value=sv, x=x, y=y)
        return {"status": "success" if res.get("ok") else "error", **res}
    if action in ("long_press", "long-press"):
        x, y = _resolve_tap_coords(step, spec)
        st = (step.get("selector_type") or "").strip()
        sv = (step.get("selector_value") or "").strip()
        res = plugin_long_press(udid, selector_type=st, selector_value=sv, x=x, y=y)
        return {"status": "success" if res.get("ok") else "error", **res}
    if action == "swipe":
        x1, y1, x2, y2 = _resolve_swipe_coords(spec)
        if x1 == x2 and y1 == y2:
            return {"status": "error", "error": "滑动坐标无效（起止点相同）"}
        res = plugin_swipe(udid, x1=x1, y1=y1, x2=x2, y2=y2)
        return {"status": "success" if res.get("ok") else "error", **res}
    if action in ("input_text", "input", "type"):
        res = plugin_input(
            udid,
            text=str(step.get("input_value") or ""),
            selector_type=(step.get("selector_type") or ""),
            selector_value=(step.get("selector_value") or ""),
        )
        return {"status": "success" if res.get("ok") else "error", **res}
    if action == "open_app":
        if should_skip_open_app_step(step):
            return {"status": "success", "message": "已跳过启动器/系统自动切换步骤"}
        try:
            from mobile_adb_control import adb_launch_app

            spec = _step_mobile_spec(step)
            pkg = str(step.get("input_value") or spec.get("app_package") or spec.get("appPackage") or "")
            activity = str(spec.get("app_activity") or spec.get("appActivity") or "") or None
            info = adb_launch_app(udid, pkg, activity, wait_foreground=True, timeout_sec=10.0)
            return {"status": "success", "message": f"已启动 {info.get('app_label') or pkg}"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
    if action in ("press_home", "home"):
        from mobile_adb_control import adb_press_home

        adb_press_home(udid)
        return {"status": "success"}
    if action in ("press_back", "back"):
        from mobile_adb_control import adb_press_back

        adb_press_back(udid)
        return {"status": "success"}
    if action == "wait":
        import time as _time

        try:
            sec = float(step.get("input_value") or step.get("wait_ms") or 1)
            # 统一约定：input_value 单位为秒，wait_ms 单位为毫秒
            if step.get("wait_ms") is not None:
                sec = sec / 1000.0
        except (TypeError, ValueError):
            sec = 1.0
        _time.sleep(min(max(sec, 0), 120))
        return {"status": "success"}
    return {"status": "error", "error": f"不支持的步骤类型: {action}"}
