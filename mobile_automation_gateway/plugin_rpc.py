# -*- coding: utf-8 -*-
"""插件 JSON-RPC 客户端（经 adb forward 访问设备本地 HTTP 服务）。v4增强版。"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from mobile_env_config import adb_path

# v2 正式包名；保留 v1 作为端口探测回退
_PACKAGE = "com.testory.assistant.v2"
_PACKAGE_FALLBACKS = ("com.testory.assistant.v2", "com.testory.assistant")
_RPC_ID = 0
_RPC_LOCK = threading.Lock()

uat_logger = logging.getLogger("testory.automation")

# udid -> { host_port, device_port, last_ping }
_forward_state: Dict[str, Dict[str, Any]] = {}
_forward_lock = threading.Lock()


def _candidate_packages() -> Tuple[str, ...]:
    return _PACKAGE_FALLBACKS


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
    last_err = ""
    for pkg in _candidate_packages():
        for shell_cmd in (
            f"cat /sdcard/Android/data/{pkg}/files/plugin_port.txt 2>/dev/null",
            f"cat /storage/emulated/0/Android/data/{pkg}/files/plugin_port.txt 2>/dev/null",
        ):
            rc, out, err = _run_adb(_adb_cmd(udid) + ["shell", shell_cmd], timeout=10)
            if rc == 0 and out.strip().isdigit():
                return int(out.strip()), ""
            last_err = err or last_err
        rc, out, err = _run_adb(
            _adb_cmd(udid)
            + [
                "shell",
                "run-as",
                pkg,
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
        last_err = err or last_err
    hp = get_forward_port(udid)
    if hp:
        try:
            res = _rpc_call(udid, "getPort", {}, host_port=hp, timeout=3.0)
            port = int(res.get("port") or 0)
            if port > 0:
                return port, ""
        except Exception:
            pass
    return None, last_err or "无法读取插件端口，请确认已开启无障碍服务"


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


def start_recording(
    udid: str, *, screenshot_per_step: bool = True, agent_mode: bool = False
) -> Dict[str, Any]:
    """通知设备端开始录制。agent_mode 参数已废弃，设备端始终本地录制。"""
    return _rpc_call(
        udid,
        "startRecording",
        {"screenshotPerStep": screenshot_per_step, "agentMode": agent_mode},
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
    try:
        res = _rpc_call(udid, "getPageSource", {}, timeout=10.0)
        # 统一暴露 xml / page_source 字段
        if isinstance(res, dict):
            xml = res.get("xml") or res.get("page_source") or ""
            if xml and not res.get("page_source"):
                res = dict(res)
                res["page_source"] = xml
        return res
    except Exception as exc:
        # RPC 不可用时降级 uiautomator dump，保证至少能拿到应用内控件树
        dump_path = "/sdcard/testory_uidump.xml"
        rc, out, err = _run_adb(
            _adb_cmd(udid) + ["shell", "uiautomator", "dump", dump_path],
            timeout=20,
        )
        if rc != 0:
            raise RuntimeError(f"getPageSource 失败: {exc}; uiautomator dump 失败: {err or out}") from exc
        rc2, xml, err2 = _run_adb(
            _adb_cmd(udid) + ["shell", "cat", dump_path],
            timeout=15,
        )
        if rc2 != 0 or not (xml or "").strip():
            raise RuntimeError(f"getPageSource 失败: {exc}; 读取 dump 失败: {err2}") from exc
        return {
            "ok": True,
            "source": "uiautomator_dump",
            "xml": xml,
            "page_source": xml,
            "node_count": xml.count("<node"),
            "fallback_error": str(exc),
        }


def pick_at_point(udid: str, x: int, y: int) -> Dict[str, Any]:
    """设备端坐标命中最优无障碍节点。"""
    return _rpc_call(
        udid,
        "pickAtPoint",
        {"x": int(x), "y": int(y)},
        timeout=8.0,
    )


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
    # v2：无障碍服务内嵌 JSON-RPC；广播/settings 不会直接拉起，优先探测已写端口
    for pkg in _candidate_packages():
        for component in (
            f"{pkg}/com.testory.assistant.v2.service.accessibility.AssistantAccessibilityService",
            f"{pkg}/.PluginForegroundService",
            f"{pkg}/com.testory.assistant.PluginForegroundService",
        ):
            _run_adb(
                _adb_cmd(udid)
                + [
                    "shell",
                    "am",
                    "startservice",
                    "-n",
                    component,
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


# ========================================================================
# v4 增强：回放坐标解析与策略选择
# ========================================================================

def _step_mobile_spec(step: Dict[str, Any]) -> Dict[str, Any]:
    spec = step.get("mobile_spec")
    if isinstance(spec, dict):
        return spec
    if isinstance(spec, str) and spec.strip():
        try:
            parsed = json.loads(spec)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
    return {}


def _resolve_tap_coords(step: Dict[str, Any], spec: Dict[str, Any]) -> Tuple[int, int]:
    """
    解析点击坐标 — v4增强版，实现5级fallback。

    v4 修复根因1: 原实现在 mobile_spec 缺少 viewport_coord.x/y 时直接返回 (0, 0)，
    导致回放时点击屏幕左上角无效位置。现增加多级降级和诊断日志。
    Fallback优先级:
      Level 1: viewport_coord 绝对坐标 (x/y)
      Level 2: viewport_coord 相对坐标 (rx/ry) 换算到当前分辨率
      Level 3: spec 顶层直写字段 (spec.x/spec.y)
      Level 4: selector_value JSON 解析（兼容旧格式）
      Level 5: 节点内相对坐标 (node_rx/node_ry)
    最终兜底: 日志警告 + 返回 (0, 0) 不再静默
    """
    x = y = 0
    st = (step.get("selector_type") or "").strip()
    sv = (step.get("selector_value") or "").strip()

    # ---- Level 1: viewport_coord 绝对坐标 ----
    vc = spec.get("viewport_coord") if isinstance(spec.get("viewport_coord"), dict) else {}
    if vc:
        vx = vc.get("x")
        vy = vc.get("y")
        if vx is not None and vy is not None:
            try:
                x = int(vx)
                y = int(vy)
                if x != 0 or y != 0:
                    return x, y
            except (TypeError, ValueError):
                pass

    # ---- Level 2: viewport_coord 相对坐标换算 ----
    if vc:
        rx = vc.get("rx")
        ry = vc.get("ry")
        sw = int(spec.get("screen_width") or 1080)
        sh = int(spec.get("screen_height") or 1920)
        if rx is not None and ry is not None and sw > 0 and sh > 0:
            try:
                x = int(round(float(rx) * sw))
                y = int(round(float(ry) * sh))
                if x != 0 or y != 0:
                    return x, y
            except (TypeError, ValueError):
                pass

    # ---- Level 3: spec 顶层直写字段 ----
    sx = spec.get("x")
    sy = spec.get("y")
    if sx is not None and sy is not None:
        try:
            x = int(sx)
            y = int(sy)
            if x != 0 or y != 0:
                return x, y
        except (TypeError, ValueError):
            pass

    # ---- Level 4: selector_value JSON 解析（兼容旧格式）----
    if st == "viewport_coord" and sv:
        try:
            coord = json.loads(sv)
            lx = coord.get("x")
            ly = coord.get("y")
            if lx is not None and ly is not None:
                x = int(lx)
                y = int(ly)
                if x != 0 or y != 0:
                    return x, y
            crx = coord.get("rx")
            cry = coord.get("ry")
            csw = coord.get("screen_width") or spec.get("screen_width") or 1080
            csh = coord.get("screen_height") or spec.get("screen_height") or 1920
            if crx is not None and cry is not None:
                x = int(round(float(crx) * int(csw)))
                y = int(round(float(cry) * int(csh)))
                if x != 0 or y != 0:
                    return x, y
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # ---- Level 5: 节点内相对坐标 (SoloPi 风格) ----
    nrx = spec.get("node_rx")
    nry = spec.get("node_ry")
    if nrx is not None and nry is not None:
        bounds = spec.get("bounds")
        if isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
            try:
                left, top, right, bottom = [int(v) for v in bounds[:4]]
                bw, bh = right - left, bottom - top
                if bw > 0 and bh > 0:
                    x = left + int(round(float(nrx) * bw))
                    y = top + int(round(float(nry) * bh))
                    if x != 0 or y != 0:
                        return x, y
            except (TypeError, ValueError):
                pass

    # ---- 最终兜底: 记录警告日志 ----
    uat_logger.warning(
        "[v4] 回放步骤坐标解析全部失败，返回(0,0)。step=%s | spec_keys=%s | st=%s",
        step.get("description", "")[:60], list(spec.keys())[:8], st,
    )
    return 0, 0


def _pick_replay_strategy(
    selector_type: str,
    selector_value: str,
    x: int,
    y: int,
) -> str:
    """
    根据selector稳定性和坐标有效性选择回放策略。

    v4 新增: 解决根因2 —— replay_step() 同时传selector和坐标给设备端时优先级不明的问题。

    Returns:
        "selector_primary" — 稳定ID类selector优先（id/resource-id/android_uiautomator）
        "coord_primary"     — 有效坐标且不稳定text类selector时坐标优先
        "hybrid"             — 默认混合策略（两者都传给设备端协调）
        "coord_only"         — 仅坐标（当selector无效但坐标有效时）
    """
    has_valid_coord = x > 0 and y > 0
    has_valid_selector = bool(selector_type and selector_value.strip())

    if not has_valid_selector and has_valid_coord:
        return "coord_only"

    # ID/资源ID类 selector 最稳定 → 优先使用元素定位
    stable_types = {"id", "resource-id", "android_uiautomator"}
    if selector_type in stable_types and has_valid_selector:
        return "selector_primary"

    # text/accessibility_id 类 selector 可能重复或不精确
    # 如果有可靠坐标 → 优先坐标
    unstable_types = {"accessibility_id", "text"}
    if selector_type in unstable_types and has_valid_coord:
        return "coord_primary"

    # xpath 类中等稳定性，有坐标时也倾向混合
    if selector_type == "xpath" and has_valid_coord:
        return "hybrid"

    return "hybrid"


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
        udit,
        "longPress",
        {
            "selectorType": selector_type,
            "selectorValue": selector_value,
            "x": x,
            "y": y,
        },
        timeout=15.0,
    )


def replay_step(udit: str, step: Dict[str, Any], *, step_index: int = 0) -> Dict[str, Any]:
    """
    将用例步骤映射为插件 tap/swipe/input RPC（v5增强版）。

    v5 关键修复：回放显示完成但实际未执行动作。
      - 新增执行前坐标有效性验证（拒绝(0,0)静默发送）
      - 新增执行结果诊断日志（记录实际发送的参数）
      - open_app 不再静默跳过，改为显式执行并返回状态
    """
    action = (step.get("action") or "").strip().lower()
    desc = (step.get("description") or "")[:60]
    spec = _step_mobile_spec(step)

    # ---- 通用诊断信息 ----
    def _make_result(status: str, **extra) -> Dict[str, Any]:
        r = {"status": status, "action": action, "step_index": step_index}
        r.update(extra)
        return r

    # ---- open_app: v5修复——不再静默跳过 ----
    if action == "open_app":
        skip_reason = ""
        if should_skip_open_app_step(step):
            skip_reason = "(launcher/system)"
            # 即使是启动器步骤也尝试用ADB启动，确保应用确实打开
            try:
                from mobile_adb_control import adb_launch_app
                pkg = str(step.get("input_value") or spec.get("app_package") or spec.get("appPackage") or "")
                if pkg and pkg not in ("", "com.android.launcher", "com.google.android.apps.nexuslauncher"):
                    info = adb_launch_app(udit, pkg, wait_foreground=True, timeout_sec=8.0)
                    return _make_result("success",
                        message=f"已启动 {info.get('app_label') or pkg}",
                        app_package=pkg,
                        _skip_reason=skip_reason,
                    )
            except Exception as exc:
                return _make_result("error", error=f"启动应用失败: {exc}")
            return _make_result("success", message="已跳过系统自动切换", _skip_reason=skip_reason)
        
        # 正常 open_app 执行
        try:
            from mobile_adb_control import adb_launch_app
            pkg = str(step.get("input_value") or spec.get("app_package") or spec.get("appPackage") or "")
            activity = str(spec.get("app_activity") or spec.get("appActivity") or "") or None
            info = adb_launch_app(udit, pkg, activity, wait_foreground=True, timeout_sec=10.0)
            return _make_result("success", message=f"已启动 {info.get('app_label') or pkg}", app_package=pkg)
        except Exception as exc:
            return _make_result("error", error=str(exc))

    # ---- tap / click ----
    if action in ("tap", "click"):
        x, y = _resolve_tap_coords(step, spec)
        st = (step.get("selector_type") or "").strip()
        sv = (step.get("selector_value") or "").strip()

        # v5 Fix: 坐标有效性前置校验
        uat_logger.info(
            "[v5-replay] Step%d %s | coords=(%d,%d) | selector=%s=%s | spec_keys=%s",
            step_index, desc, x, y, st, sv[:40] if sv else "-", list(spec.keys())[:8],
        )

        strategy = _pick_replay_strategy(st, sv, x, y)

        # v5: 如果坐标为(0,0)且无有效selector，立即报错而非静默发送
        if x == 0 and y == 0 and not st:
            uat_logger.warning(
                "[v5-replay] Step%d %s 无有效坐标和selector，拒绝执行",
                step_index, desc,
            )
            return _make_result("error",
                error="无法解析有效的点击坐标或定位符",
                _diagnostic={
                    "strategy": strategy,
                    "resolved_coords": (x, y),
                    "spec_keys": list(spec.keys()),
                    "viewport_coord": spec.get("viewport_coord"),
                    "spec_dump": {k: str(v)[:80] for k, v in spec.items() if k != "operation_node"},
                },
            )

        try:
            if strategy == "coord_only":
                res = plugin_tap(udit, x=x, y=y)
            elif strategy == "coord_primary":
                res = plugin_tap(udit, selector_type=st, selector_value=sv, x=x, y=y)
            elif strategy == "selector_primary":
                res = plugin_tap(udit, selector_type=st, selector_value=sv, x=x, y=y)
            else:  # hybrid
                res = plugin_tap(udit, selector_type=st, selector_value=sv, x=x, y=y)
        except Exception as rpc_exc:
            uat_logger.error("[v5-replay] Step%d %s RPC异常: %s", step_index, desc, rpc_exc)
            return _make_result("error",
                error=f"设备RPC调用失败: {rpc_exc}",
                _diagnostic={"strategy": strategy, "resolved_coords": (x, y)},
            )

        ok = bool(res.get("ok"))
        status = "success" if ok else "error"
        
        uat_logger.info(
            "[v5-replay] Step%d %s 结果: %s | 设备返回: %s",
            step_index, desc, status, str(res)[:120],
        )

        result = _make_result(status, **res)
        if not ok:
            result["_diagnostic"] = {
                "strategy": strategy,
                "resolved_coords": (x, y),
                "selector_used": f"{st}={sv}" if st else "(none)",
                "device_response": {k: str(v)[:100] for k, v in res.items()},
            }
        return result

    # ---- long_press ----
    if action in ("long_press", "long-press"):
        x, y = _resolve_tap_coords(step, spec)
        st = (step.get("selector_type") or "").strip()
        sv = (step.get("selector_value") or "").strip()
        strategy = _pick_replay_strategy(st, sv, x, y)
        if x == 0 and y == 0 and not st:
            return _make_result("error", error="无法解析有效的长按坐标或定位符")
        try:
            res = plugin_long_press(
                udit,
                selector_type=st if strategy != "coord_only" else "",
                selector_value=sv if strategy != "coord_only" else "",
                x=x, y=y,
            )
        except Exception as rpc_exc:
            return _make_result("error", error=f"设备RPC调用失败: {rpc_exc}")
        ok = bool(res.get("ok"))
        result = _make_result("success" if ok else "error", **res)
        if not ok:
            result["_diagnostic"] = {"strategy": strategy, "resolved_coords": (x, y)}
        return result

    # ---- swipe ----
    if action == "swipe":
        x1, y1, x2, y2 = _resolve_swipe_coords(spec)
        if x1 == x2 and y1 == y2:
            return _make_result("error", error="滑动坐标无效（起止点相同）")
        try:
            res = plugin_swipe(udit, x1=x1, y1=y1, x2=x2, y2=y2)
        except Exception as rpc_exc:
            return _make_result("error", error=f"设备RPC调用失败: {rpc_exc}")
        return _make_result("success" if res.get("ok") else "error", **res)

    # ---- input ----
    if action in ("input_text", "input", "type"):
        text = str(step.get("input_value") or "")
        if not text.strip():
            return _make_result("error", error="输入内容为空")
        try:
            res = plugin_input(
                udit, text=text,
                selector_type=(step.get("selector_type") or ""),
                selector_value=(step.get("selector_value") or ""),
            )
        except Exception as rpc_exc:
            return _make_result("error", error=f"设备RPC调用失败: {rpc_exc}")
        return _make_result("success" if res.get("ok") else "error", **res)

    # ---- press_home / press_back ----
    if action in ("press_home", "home"):
        try:
            from mobile_adb_control import adb_press_home
            adb_press_home(udit)
            return _make_result("success", message="按下Home键")
        except Exception as exc:
            return _make_result("error", error=f"Home键操作失败: {exc}")

    if action in ("press_back", "back"):
        try:
            from mobile_adb_control import adb_press_back
            adb_press_back(udit)
            return _make_result("success", message="按下Back键")
        except Exception as exc:
            return _make_result("error", error=f"Back键操作失败: {exc}")

    # ---- wait ----
    if action == "wait":
        import time as _time
        try:
            sec = float(step.get("input_value") or step.get("wait_ms") or 1)
            if step.get("wait_ms") is not None:
                sec = sec / 1000.0
        except (TypeError, ValueError):
            sec = 1.0
        _time.sleep(min(max(sec, 0), 120))
        return _make_result("success", message=f"等待 {sec:.1f}s")

    return _make_result("error", error=f"不支持的步骤类型: {action}")


def should_skip_open_app_step(step: Dict[str, Any]) -> bool:
    """判断是否应跳过 open_app 步骤（启动器/系统自动切换场景）。"""
    pkg = str(step.get("input_value") or "")
    spec = _step_mobile_spec(step)
    app_pkg = str(spec.get("app_package") or spec.get("appPackage") or "") or pkg
    # 跳过 Android 启动器
    skip_prefixes = (
        "com.android.launcher",
        "com.google.android.apps.nexuslauncher",
        "com.huawei.android.launcher",
        "com.miui.home",
        "com.oppo.launcher",
        "com.vivo.launcher",
        "com.sec.android.app.launcher",
    )
    return any(app_pkg.startswith(p) for p in skip_prefixes)
