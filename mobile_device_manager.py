# -*- coding: utf-8 -*-
"""Android 设备发现与 Appium 健康检查。"""

from __future__ import annotations

import re
import subprocess
import threading
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
            devices.append(meta)
        return devices
    except Exception:
        return []


def check_appium_server() -> Tuple[bool, str]:
    """检查 Appium Server 是否可达。"""
    if not mobile_enabled():
        return False, "移动端测试未启用"
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


def pick_default_device() -> Optional[Dict[str, Any]]:
    """选择第一台已授权的真机。"""
    for dev in list_usb_devices():
        if dev.get("state") == "device":
            enriched = dict(dev)
            enriched.update(get_device_info(dev.get("udid") or ""))
            return enriched
    return None


def capture_screenshot_png(udid: str = "") -> Optional[bytes]:
    """通过 adb screencap 获取 PNG 字节（用于 canvas 投屏）。"""
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
