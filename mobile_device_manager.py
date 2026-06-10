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


def is_emulator_udid(udid: str) -> bool:
    return (udid or "").strip().startswith("emulator-")


def list_real_usb_devices() -> List[Dict[str, Any]]:
    """仅 USB/无线真机，不含 adb 枚举到的 emulator-*。"""
    return [d for d in list_usb_devices() if not is_emulator_udid(d.get("udid") or "")]


def pick_default_real_device() -> Optional[Dict[str, Any]]:
    """真机连接：仅选择已授权的真机（不含模拟器）。"""
    for dev in list_real_usb_devices():
        if dev.get("state") == "device":
            enriched = dict(dev)
            enriched.update(get_device_info(dev.get("udid") or ""))
            return enriched
    return None


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
    """选择第一台已授权的真机；模拟器优先模式下首选 emulator-*。"""
    devices = list_usb_devices()
    try:
        from mobile_env_config import emulator_mode_enabled

        if emulator_mode_enabled():
            for dev in devices:
                if dev.get("state") == "device" and (dev.get("udid") or "").startswith("emulator-"):
                    enriched = dict(dev)
                    enriched.update(get_device_info(dev.get("udid") or ""))
                    return enriched
    except Exception:
        pass
    for dev in devices:
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


def capture_screenshot_frame(udid: str = "") -> Tuple[Optional[bytes], str]:
    """
    获取投屏帧。默认 JPEG + 缩放以减小无线 adb 传输体积、提高实际帧率。
    Returns:
        (bytes, format) format 为 jpeg 或 png
    """
    from mobile_env_config import mirror_format, mirror_jpeg_quality, mirror_max_width

    png = capture_screenshot_png(udid)
    if not png:
        return None, "png"
    fmt = mirror_format()
    max_w = mirror_max_width()
    if fmt in ("jpeg", "jpg") or max_w > 0:
        try:
            import cv2
            import numpy as np

            arr = np.frombuffer(png, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return png, "png"
            if max_w > 0 and img.shape[1] > max_w:
                scale = max_w / float(img.shape[1])
                img = cv2.resize(
                    img,
                    (max_w, max(1, int(img.shape[0] * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            if fmt in ("jpeg", "jpg"):
                ok, buf = cv2.imencode(
                    ".jpg",
                    img,
                    [int(cv2.IMWRITE_JPEG_QUALITY), mirror_jpeg_quality()],
                )
                if ok:
                    return buf.tobytes(), "jpeg"
            else:
                ok, buf = cv2.imencode(".png", img)
                if ok:
                    return buf.tobytes(), "png"
        except Exception:
            pass
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


def adb_pair_wireless(host: str, pair_port: int, pairing_code: str) -> Tuple[bool, str]:
    """无线调试配对（adb pair host:pair_port code）。"""
    host = (host or "").strip()
    code = (pairing_code or "").strip()
    if not host or not code:
        return False, "需要手机 IP 与 6 位配对码"
    try:
        port = int(pair_port)
    except (TypeError, ValueError):
        return False, "配对端口无效"
    rc, out, err = _run_adb(["pair", f"{host}:{port}", code], timeout=45)
    merged = "\n".join(x for x in (out, err) if x).strip()
    if rc == 0 and "successfully paired" in merged.lower():
        return True, merged or "配对成功"
    if "already paired" in merged.lower():
        return True, merged
    return False, merged or err or "无线配对失败"


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
