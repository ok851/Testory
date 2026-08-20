# -*- coding: utf-8 -*-
"""Android 设备发现与 Appium 健康检查。"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from mobile_env_config import adb_path, appium_server_url, mobile_enabled

_lock = threading.Lock()
_connected_udid: Optional[str] = None


def check_adb_available() -> Tuple[bool, str]:
    """检查 adb 是否可用。"""
    if not mobile_enabled():
        return False, "移动端测试未启用（ENABLE_MOBILE=0）"
    try:
        proc = subprocess.run(
            [adb_path(), "version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            return False, f"adb 不可用：{err or '未知错误'}"
        return True, (proc.stdout or "").splitlines()[0] if proc.stdout else "adb ok"
    except FileNotFoundError:
        return False, f"找不到 adb 命令，请安装 Android SDK Platform-Tools 并配置 ADB_PATH 或 PATH"
    except subprocess.TimeoutExpired:
        return False, "adb version 命令超时"
    except Exception as exc:
        return False, f"adb 检查失败：{exc}"


def list_usb_devices() -> List[Dict[str, Any]]:
    """
    枚举 USB 连接的 Android 设备（adb devices -l）。

    Returns:
        设备列表，每项含 udid, state, model, product, device 等字段。
    """
    ok, msg = check_adb_available()
    if not ok:
        return []
    try:
        proc = subprocess.run(
            [adb_path(), "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        lines = (proc.stdout or "").strip().splitlines()
        devices: List[Dict[str, Any]] = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            udid = parts[0]
            state = parts[1]
            meta: Dict[str, str] = {"udid": udid, "state": state}
            tail = " ".join(parts[2:])
            for key in ("model", "product", "device", "transport_id"):
                m = re.search(rf"\b{key}:(\S+)", tail)
                if m:
                    meta[key] = m.group(1)
            meta["display_name"] = meta.get("model") or meta.get("device") or udid
            meta["is_emulator"] = is_emulator_udid(udid)
            devices.append(meta)
        return devices
    except Exception:
        return []


_EMULATOR_LOCAL_RE = re.compile(r"^127\.0\.0\.1:\d+$", re.I)
_EMULATOR_LOCALHOST_RE = re.compile(r"^localhost:\d+$", re.I)


def is_emulator_udid(udid: str) -> bool:
    """识别 adb 模拟器 serial（含雷电/夜神等 127.0.0.1:port）。"""
    u = (udid or "").strip()
    if u.startswith("emulator-"):
        return True
    if _EMULATOR_LOCAL_RE.match(u) or _EMULATOR_LOCALHOST_RE.match(u):
        return True
    return False


def list_real_usb_devices() -> List[Dict[str, Any]]:
    """仅 USB/无线真机，不含 adb 枚举到的 emulator-*。"""
    return [d for d in list_usb_devices() if not is_emulator_udid(d.get("udid") or "")]


_WIRELESS_UDID_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d+$")
_MDNS_STUB_RE = re.compile(r"_adb-tls|_tcp", re.I)


def device_serial_deny_prefixes() -> List[str]:
    raw = (os.environ.get("MOBILE_DEVICE_SERIAL_DENY_PREFIXES") or "PQ").strip()
    if not raw:
        return []
    return [p.strip().upper() for p in raw.split(",") if p.strip()]


def is_deny_prefix_serial(udid: str) -> bool:
    u = (udid or "").strip().upper()
    if not u:
        return False
    for prefix in device_serial_deny_prefixes():
        if u.startswith(prefix.upper()):
            return True
    return False


def is_wireless_udid(udid: str) -> bool:
    return bool(_WIRELESS_UDID_RE.match((udid or "").strip()))


def is_mdns_stub_serial(udid: str) -> bool:
    return bool(_MDNS_STUB_RE.search(udid or ""))


def device_state_label(state: str) -> str:
    s = (state or "").strip().lower()
    labels = {
        "device": "已授权",
        "unauthorized": "需授权",
        "offline": "离线",
        "no permissions": "无权限",
    }
    return labels.get(s, state or "未知")


def format_connect_error(dev: Optional[Dict[str, Any]] = None) -> str:
    """按 adb state 返回可操作的连接错误文案。"""
    if not dev:
        return (
            "未发现已授权设备。请启动模拟器、USB 连接真机或在无线调试中配对后重试。"
        )
    state = (dev.get("state") or "").strip().lower()
    udid = dev.get("udid") or dev.get("display_name") or ""
    if state == "unauthorized":
        return (
            f"设备 {udid} 尚未授权 USB 调试。请在手机上点击「允许 USB 调试」"
            "并勾选「始终允许此计算机」，然后刷新设备列表再连接。"
        )
    if state == "offline":
        return (
            f"设备 {udid} 处于离线状态。请检查 USB 线/无线连接，"
            "或在开发者选项中重新开启无线调试后刷新列表。"
        )
    if state == "no permissions":
        return (
            f"设备 {udid} 无 adb 权限。请确认已开启开发者选项与 USB 调试，"
            "必要时在手机上撤销授权后重新插拔 USB。"
        )
    if state != "device":
        return (
            f"设备 {udid} 当前状态为「{device_state_label(state)}」，"
            "无法连接。请处理后再试。"
        )
    return "未发现已授权设备。请启动模拟器、USB 连接真机或在无线调试中配对后重试。"


def score_device_priority(dev: Dict[str, Any]) -> int:
    """
    设备默认选中优先级（分数越高越优先）。
    USB 真机 serial > 无线 IP:port > deny 前缀（如 PQ）残留。
    """
    udid = (dev.get("udid") or "").strip()
    state = (dev.get("state") or "").strip().lower()
    score = 0
    if state == "device":
        score += 1000
    elif state == "unauthorized":
        score += 200
    elif state == "offline":
        score += 50
    if is_emulator_udid(udid):
        score -= 500
    if is_deny_prefix_serial(udid):
        score -= 800
    if is_mdns_stub_serial(udid):
        score -= 900
    if is_wireless_udid(udid):
        score += 100
    elif udid and not is_emulator_udid(udid):
        score += 300
    return score


def sort_devices_for_ui(devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    indexed = list(enumerate(devices))
    indexed.sort(key=lambda item: (-score_device_priority(item[1]), item[0]))
    return [dev for _, dev in indexed]


def list_devices_for_ui(tab: str = "real") -> List[Dict[str, Any]]:
    """按 UI Tab 返回设备列表（已排序）。"""
    tab_key = (tab or "real").strip().lower()
    if tab_key == "emulator":
        return sort_devices_for_ui(list_emulators())
    return sort_devices_for_ui(list_real_usb_devices())


def pick_best_authorized_device(
    devices: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """在已授权设备中按优先级选择默认真机。"""
    pool = devices if devices is not None else list_real_usb_devices()
    authorized = [d for d in pool if d.get("state") == "device" and not is_deny_prefix_serial(d.get("udid") or "")]
    if not authorized:
        return None
    best = sort_devices_for_ui(authorized)[0]
    enriched = dict(best)
    enriched.update(get_device_info(best.get("udid") or ""))
    return enriched


def pick_default_real_device() -> Optional[Dict[str, Any]]:
    """真机连接：按优先级选择已授权真机（跳过 deny 前缀残留）。"""
    return pick_best_authorized_device()


def should_prune_device(dev: Dict[str, Any]) -> bool:
    """判断是否应清理的幽灵/残留 adb 设备。"""
    udid = (dev.get("udid") or "").strip()
    state = (dev.get("state") or "").strip().lower()
    if not udid:
        return False
    if state == "offline":
        return True
    if is_mdns_stub_serial(udid):
        return True
    if is_deny_prefix_serial(udid):
        return True
    return False


def prune_stale_adb_devices() -> Dict[str, Any]:
    """
    断开离线、deny 前缀、mDNS 残留等幽灵 adb 设备。
    Returns: {pruned: [...], errors: [...], devices: 当前列表}
    """
    pruned: List[Dict[str, str]] = []
    errors: List[str] = []
    for dev in list_usb_devices():
        if not should_prune_device(dev):
            continue
        udid = dev.get("udid") or ""
        ok, msg = adb_disconnect_device(udid)
        entry = {"udid": udid, "state": dev.get("state") or "", "message": msg}
        if ok:
            pruned.append(entry)
        else:
            errors.append(f"{udid}: {msg}")
    return {
        "pruned": pruned,
        "errors": errors,
        "devices": list_usb_devices(),
        "real_devices": list_real_usb_devices(),
        "emulators": list_emulators(),
    }


def collect_device_warnings(devices: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """汇总设备列表中的提示信息。"""
    pool = devices if devices is not None else list_real_usb_devices()
    warnings: List[str] = []
    unauthorized = [d for d in pool if d.get("state") == "unauthorized"]
    if unauthorized:
        warnings.append(
            f"有 {len(unauthorized)} 台设备待授权 USB 调试，连接前请在手机上点击「允许」。"
        )
    deny = [d for d in pool if is_deny_prefix_serial(d.get("udid") or "")]
    if deny:
        warnings.append(
            f"检测到 {len(deny)} 台可疑残留设备（如 PQ 开头），可点刷新清理。"
        )
    offline = [d for d in pool if d.get("state") == "offline"]
    if offline:
        warnings.append(f"有 {len(offline)} 台设备处于离线状态。")
    return warnings


def check_appium_server(*, try_auto_start: bool = False) -> Tuple[bool, str]:
    """检查 Appium Server 是否可达；try_auto_start=True 时尝试自动拉起。"""
    if not mobile_enabled():
        return False, "移动端测试未启用"
    if try_auto_start:
        pass  # Appium 自动启动已移除
    base = appium_server_url().rstrip("/")
    for path in ("/status", "/wd/hub/status"):
        try:
            resp = requests.get(f"{base}{path}", timeout=5)
            if resp.status_code == 200:
                return True, "Appium Server 可用"
        except requests.RequestException:
            continue
    return (
        False,
        f"无法连接 Appium Server（{base}）。请先启动 appium 并确认 APPIUM_SERVER_URL 配置正确。",
    )


def check_mobile_health() -> Dict[str, Any]:
    """综合健康检查，供 /api/mobile/health 使用。"""
    adb_ok, adb_msg = check_adb_available()
    appium_ok, appium_msg = check_appium_server()
    devices = list_usb_devices() if adb_ok else []
    authorized = [d for d in devices if d.get("state") == "device"]
    return {
        "adb_ok": adb_ok,
        "adb_message": adb_msg,
        "appium_ok": appium_ok,
        "appium_message": appium_msg,
        "device_count": len(devices),
        "authorized_device_count": len(authorized),
        "devices": devices,
        "connected_udid": get_connected_udid(),
    }


def set_connected_udid(udid: Optional[str]) -> None:
    global _connected_udid
    with _lock:
        _connected_udid = (udid or "").strip() or None


def get_connected_udid() -> Optional[str]:
    with _lock:
        return _connected_udid


def adb_run(udid: str, *shell_args: str, timeout: int = 20) -> Tuple[int, str, str]:
    """执行 adb shell 子命令。"""
    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    cmd.extend(["shell", *shell_args])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:
        return 1, "", str(exc)


def get_device_info(udid: str = "") -> Dict[str, Any]:
    """
    读取设备分辨率、系统版本、品牌等（用于虚拟屏坐标映射与自动配置）。
    """
    info: Dict[str, Any] = {
        "udid": udid,
        "width": 1080,
        "height": 1920,
        "density": 420,
        "android_release": "",
        "brand": "",
        "model": "",
        "foreground_package": "",
    }
    code, out, _ = adb_run(udid, "wm", "size")
    if code == 0 and out:
        from mobile_adb_control import parse_wm_size

        w, h = parse_wm_size(out)
        info["width"], info["height"] = w, h
        info["wm_size_raw"] = out
    code, out, _ = adb_run(udid, "wm", "density")
    if code == 0 and out:
        m = re.search(r"(\d+)", out)
        if m:
            info["density"] = int(m.group(1))
    code, out, _ = adb_run(udid, "getprop", "ro.build.version.release")
    if code == 0:
        info["android_release"] = out
    code, out, _ = adb_run(udid, "getprop", "ro.product.brand")
    if code == 0:
        info["brand"] = out
    code, out, _ = adb_run(udid, "getprop", "ro.product.model")
    if code == 0:
        info["model"] = out
    fg = get_foreground_app(udid)
    if fg:
        info["foreground_package"] = fg.get("package") or ""
        info["foreground_activity"] = fg.get("activity") or ""
    return info


def get_foreground_app(udid: str = "") -> Optional[Dict[str, str]]:
    """解析当前前台 Activity（dumpsys window）。"""
    code, out, _ = adb_run(udid, "dumpsys", "window", timeout=25)
    if code != 0 or not out:
        return None
    for line in out.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            m = re.search(r"([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.$]+)", line)
            if m:
                return {"package": m.group(1), "activity": m.group(2), "raw": line.strip()}
    return None


def list_user_apps(udid: str = "", limit: int = 80) -> List[Dict[str, str]]:
    """
    列出用户可启动应用（简化版，供下拉选择；无需用户手填包名）。
    """
    code, out, _ = adb_run(
        udid,
        "pm",
        "list",
        "packages",
        "-3",
        timeout=30,
    )
    if code != 0:
        return []
    pkgs = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.append(line.split(":", 1)[-1].strip())
    pkgs.sort()
    result: List[Dict[str, str]] = []
    for pkg in pkgs[: max(1, limit)]:
        label = pkg.split(".")[-1]
        result.append({"package": pkg, "label": label})
    return result


def list_emulators() -> List[Dict[str, Any]]:
    """adb 枚举到的模拟器（emulator-* 或 127.0.0.1:port）。"""
    out: List[Dict[str, Any]] = []
    for dev in list_usb_devices():
        udid = dev.get("udid") or ""
        if not is_emulator_udid(udid):
            continue
        enriched = dict(dev)
        enriched["is_emulator"] = True
        if dev.get("state") == "device":
            enriched.update(get_device_info(udid))
        out.append(enriched)
    return out


def pick_default_emulator() -> Optional[Dict[str, Any]]:
    """选择第一台已授权的模拟器。"""
    for dev in list_emulators():
        if dev.get("state") == "device":
            return dev
    return None


def pick_default_device() -> Optional[Dict[str, Any]]:
    """选择第一台已授权设备（真机或模拟器）。"""
    real = pick_default_real_device()
    if real:
        return real
    return pick_default_emulator()


def capture_screenshot_png(udid: str = "") -> Optional[bytes]:
    """通过 adb screencap 获取 PNG 字节（供 AI 视觉定位、步骤诊断等使用，非投屏降级）。"""
    ok, _ = check_adb_available()
    if not ok:
        return None
    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    cmd.extend(["exec-out", "screencap", "-p"])
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
        if proc.returncode != 0 or not proc.stdout:
            return None
        return proc.stdout
    except Exception:
        return None


def capture_screenshot_frame(udid: str = "") -> Tuple[Optional[bytes], str]:
    """获取设备截图帧（PNG，供步骤诊断等使用）。"""
    png = capture_screenshot_png(udid)
    if not png:
        return None, "png"
    return png, "png"


def _run_adb(args: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            [adb_path(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        return 1, "", "adb 命令超时"
    except Exception as exc:
        return 1, "", str(exc)


_PROTOCOL_FAULT_RE = re.compile(
    r"protocol fault|couldn't read status message",
    re.IGNORECASE,
)


def restart_adb_server() -> Tuple[bool, str]:
    """重启 adb 服务，清除无线配对时的陈旧 TLS/会话状态。"""
    rc1, _, err1 = _run_adb(["kill-server"], timeout=15)
    if rc1 != 0 and err1 and "cannot connect" not in err1.lower():
        return False, err1 or "adb kill-server 失败"
    time.sleep(0.35)
    rc2, out2, err2 = _run_adb(["start-server"], timeout=20)
    merged = "\n".join(x for x in (out2, err2) if x).strip()
    if rc2 != 0:
        return False, merged or err2 or "adb start-server 失败"
    time.sleep(0.25)
    return True, merged or "adb 已重启"


def _is_protocol_fault_message(msg: str) -> bool:
    return bool(msg and _PROTOCOL_FAULT_RE.search(msg))


def _format_pair_protocol_fault_hint() -> str:
    return (
        "常见原因：① 端口填错——须使用「使用配对码配对设备」弹窗里的端口，"
        "不要用无线调试主界面的「IP 地址和端口」；② 配对码/端口已过期，请重新打开配对弹窗；"
        "③ 手机需同时开启 USB 调试与无线调试；④ 关闭手机/电脑 VPN；"
        "⑤ Wi-Fi 设置中关闭「随机 MAC 地址」；⑥ 开发者选项中「撤销 USB 调试授权」后重试。"
    )


def adb_pair_wireless(
    host: str,
    pair_port: int,
    pairing_code: str,
    *,
    restart_server: bool = True,
    max_attempts: int = 2,
) -> Tuple[bool, str]:
    """无线调试配对（adb pair host:pair_port code）。"""
    host = (host or "").strip()
    code = (pairing_code or "").strip()
    if not host or not code:
        return False, "需要手机 IP 与 6 位配对码"
    try:
        port = int(pair_port)
    except (TypeError, ValueError):
        return False, "配对端口无效"

    if restart_server:
        ok_prep, prep_msg = restart_adb_server()
        if not ok_prep:
            return False, f"重启 adb 失败：{prep_msg}"

    last_msg = ""
    for attempt in range(max(1, max_attempts)):
        rc, out, err = _run_adb(["pair", f"{host}:{port}", code], timeout=60)
        merged = "\n".join(x for x in (out, err) if x).strip()
        last_msg = merged or err or "无线配对失败"
        lower = last_msg.lower()
        if rc == 0 and "successfully paired" in lower:
            return True, merged or "配对成功"
        if "already paired" in lower:
            return True, merged
        if attempt + 1 < max_attempts and _is_protocol_fault_message(last_msg):
            restart_adb_server()
            time.sleep(0.4)
            continue
        break

    if _is_protocol_fault_message(last_msg):
        return False, f"{last_msg}。{_format_pair_protocol_fault_hint()}"
    return False, last_msg


def discover_wireless_connect_ports(host: str) -> List[int]:
    """配对成功后通过 adb mdns services 发现设备调试端口（与配对端口可能不同）。"""
    host = (host or "").strip()
    if not host:
        return []
    rc, out, err = _run_adb(["mdns", "services"], timeout=25)
    merged = "\n".join(x for x in (out, err) if x)
    if rc != 0 and not merged.strip():
        return []
    ports: List[int] = []
    seen = set()
    host_pattern = re.compile(rf"{re.escape(host)}:(\d+)")
    any_ip_pattern = re.compile(r":(\d{4,5})\b")
    for line in merged.splitlines():
        if "adb-tls-connect" not in line.lower() and host not in line:
            continue
        found = list(host_pattern.finditer(line))
        if not found and "adb-tls-connect" in line.lower():
            found = list(any_ip_pattern.finditer(line))
        for match in found:
            port = int(match.group(1))
            if 1024 <= port <= 65535 and port not in seen:
                seen.add(port)
                ports.append(port)
    return ports


def wireless_pair_and_connect(
    host: str,
    port: int,
    pairing_code: str,
) -> Tuple[bool, str, str, str]:
    """
    无线配对并连接。port 为配对弹窗端口；连接端口通过 mdns 自动发现。
    Returns: (ok, message, udid, stage)
    """
    host = (host or "").strip()
    code = (pairing_code or "").strip()
    try:
        pair_port = int(port)
    except (TypeError, ValueError):
        return False, "端口无效", "", "validate"

    paired = False
    pair_msg = ""
    if code:
        ok_pair, pair_msg = adb_pair_wireless(host, pair_port, code)
        if ok_pair:
            paired = True
        elif _is_protocol_fault_message(pair_msg):
            # 用户可能填了主界面连接端口（或已配对），尝试直接 connect
            ok_direct, direct_msg, udid = adb_connect_wireless(host, pair_port)
            if ok_direct:
                return True, f"已连接（跳过配对）：{direct_msg}", udid, "connect"
            return False, pair_msg, "", "pair"
        else:
            return False, pair_msg, "", "pair"

    # 配对成功后优先用 mdns 发现的调试端口，勿再用配对端口 connect
    connect_candidates: List[int] = []
    if paired:
        time.sleep(0.6)
        connect_candidates.extend(discover_wireless_connect_ports(host))
    if pair_port not in connect_candidates:
        connect_candidates.append(pair_port)

    last_connect_msg = pair_msg or ""
    for connect_port in connect_candidates:
        ok_conn, conn_msg, udid = adb_connect_wireless(host, connect_port)
        last_connect_msg = conn_msg
        if ok_conn:
            stage = "connect"
            if paired and connect_port != pair_port:
                conn_msg = f"配对成功，已连接调试端口 {connect_port}"
            return True, conn_msg, udid, stage

    if paired:
        hint = (
            "配对已成功，但自动连接失败。请在手机「无线调试」主界面查看「IP 地址和端口」，"
            "仅填写该端口后再次点击连接（无需重新配对）。"
        )
        return False, f"{last_connect_msg}。{hint}", f"{host}:{pair_port}", "connect"
    return False, last_connect_msg or pair_msg, "", "connect"


def adb_connect_wireless(host: str, connect_port: int) -> Tuple[bool, str, str]:
    """
    无线调试连接（adb connect host:port）。
    Returns:
        (ok, message, udid)
    """
    host = (host or "").strip()
    try:
        port = int(connect_port)
    except (TypeError, ValueError):
        return False, "调试端口无效", ""
    if not host:
        return False, "需要手机 IP", ""
    rc, out, err = _run_adb(["connect", f"{host}:{port}"], timeout=30)
    merged = "\n".join(x for x in (out, err) if x).strip()
    udid = f"{host}:{port}"
    lower = merged.lower()
    if rc == 0 and ("connected to" in lower or "already connected" in lower):
        return True, merged or "已连接", udid
    return False, merged or err or "无线连接失败", udid


def adb_disconnect_device(udid: str) -> Tuple[bool, str]:
    """断开 adb 连接（主要用于无线设备 host:port）。"""
    udid = (udid or "").strip()
    if not udid:
        return True, ""
    rc, out, err = _run_adb(["disconnect", udid], timeout=15)
    merged = "\n".join(x for x in (out, err) if x).strip()
    lower = merged.lower()
    if rc == 0 or "disconnected" in lower or "not connected" in lower:
        return True, merged or "已断开 adb"
    return False, merged or err or "adb 断开失败"
