# -*- coding: utf-8 -*-
"""Android SDK Emulator (AVD) 生命周期管理 — 模拟器优先模式。"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mobile_env_config import adb_path, android_sdk_home

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_running: Dict[str, Dict[str, Any]] = {}

# 推荐 AVD 设备定义（与 UI 外框 preset 对应，供文档/创建参考）
EMULATOR_AVD_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "pixel_7",
        "label": "Google Pixel 7",
        "device_id": "pixel_7",
        "system_image": "system-images;android-34;google_apis;x86_64",
        "avd_name_hint": "Testory_Pixel7",
    },
    {
        "id": "samsung_s23",
        "label": "Samsung Galaxy S23",
        "device_id": "pixel_6",
        "system_image": "system-images;android-34;google_apis;x86_64",
        "avd_name_hint": "Testory_GalaxyS23",
    },
    {
        "id": "xiaomi_14",
        "label": "Xiaomi 14 (通用)",
        "device_id": "pixel_6",
        "system_image": "system-images;android-34;google_apis;x86_64",
        "avd_name_hint": "Testory_Xiaomi14",
    },
    {
        "id": "tablet_10",
        "label": "Android 平板 10\"",
        "device_id": "pixel_tablet",
        "system_image": "system-images;android-34;google_apis;x86_64",
        "avd_name_hint": "Testory_Tablet10",
    },
]


def _sdk_root() -> Optional[Path]:
    raw = android_sdk_home()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def emulator_exe() -> Optional[str]:
    root = _sdk_root()
    if not root:
        return None
    for name in ("emulator.exe", "emulator"):
        cand = root / "emulator" / name
        if cand.is_file():
            return str(cand)
    return None


def avdmanager_exe() -> Optional[str]:
    root = _sdk_root()
    if not root:
        return None
    for sub in ("cmdline-tools/latest/bin/avdmanager.bat", "cmdline-tools/latest/bin/avdmanager"):
        cand = root / sub
        if cand.is_file():
            return str(cand)
    legacy = root / "tools" / "bin" / "avdmanager.bat"
    if legacy.is_file():
        return str(legacy)
    return None


def sdkmanager_exe() -> Optional[str]:
    root = _sdk_root()
    if not root:
        return None
    for sub in ("cmdline-tools/latest/bin/sdkmanager.bat", "cmdline-tools/latest/bin/sdkmanager"):
        cand = root / sub
        if cand.is_file():
            return str(cand)
    return None


def emulator_available() -> Tuple[bool, str]:
    exe = emulator_exe()
    if not exe:
        return False, (
            "未检测到 Android 模拟器。可按下面步骤配置（不必会写代码）："
            "① 安装 Android Studio；② 打开 Device Manager 创建一个虚拟手机；"
            "③ 在 .env 设置 ANDROID_HOME（默认 "
            "C:\\Users\\你的用户名\\AppData\\Local\\Android\\Sdk）。"
            "详见 .env.example 中「Android SDK / 模拟器」说明。"
        )
    return True, exe


def list_avds() -> List[Dict[str, str]]:
    ok, msg = emulator_available()
    if not ok:
        return []
    try:
        proc = subprocess.run(
            [msg, "-list-avds"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        names = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        return [{"name": n, "label": n} for n in names]
    except Exception:
        return []


def list_running_emulators() -> List[Dict[str, Any]]:
    from mobile_device_manager import list_usb_devices

    out: List[Dict[str, Any]] = []
    for dev in list_usb_devices():
        udid = dev.get("udid") or ""
        if udid.startswith("emulator-"):
            item = dict(dev)
            item["is_emulator"] = True
            with _lock:
                meta = _running.get(udid) or _running.get(item.get("avd_name") or "")
            if meta:
                item["avd_name"] = meta.get("avd_name")
                item["started_at"] = meta.get("started_at")
            out.append(item)
    return out


def _serial_for_port(port: int) -> str:
    return f"emulator-{port}"


def _wait_boot_completed(serial: str, timeout: int = 180) -> Tuple[bool, str]:
    adb = adb_path()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            proc = subprocess.run(
                [adb, "-s", serial, "shell", "getprop", "sys.boot_completed"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if (proc.stdout or "").strip() == "1":
                return True, "启动完成"
        except Exception:
            pass
        time.sleep(2)
    return False, f"等待模拟器启动超时（{timeout}s）"


def start_avd(
    avd_name: str,
    port: int = 5554,
    gpu: str = "host",
    no_window: bool = False,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    启动 AVD 子进程并等待 adb 就绪。

    Returns:
        (ok, message, {serial, avd_name, pid, port})
    """
    ok, exe_or_msg = emulator_available()
    if not ok:
        return False, exe_or_msg, {}
    avd_name = (avd_name or "").strip()
    if not avd_name:
        return False, "需要 AVD 名称", {}

    serial = _serial_for_port(port)
    with _lock:
        if serial in _running or any(r.get("avd_name") == avd_name for r in _running.values()):
            return False, f"AVD「{avd_name}」或端口 {port} 已在运行", {}

    cmd = [
        exe_or_msg,
        "-avd",
        avd_name,
        "-port",
        str(port),
        "-gpu",
        (gpu or "host").strip() or "host",
        "-no-snapshot-load",
        "-no-boot-anim",
    ]
    if no_window:
        cmd.append("-no-window")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
        )
    except Exception as exc:
        return False, f"启动模拟器失败：{exc}", {}

    adb = adb_path()
    try:
        subprocess.run(
            [adb, "wait-for-device"],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        try:
            proc.terminate()
        except Exception:
            pass
        return False, "adb wait-for-device 超时", {}

    boot_ok, boot_msg = _wait_boot_completed(serial)
    if not boot_ok:
        try:
            proc.terminate()
        except Exception:
            pass
        return False, boot_msg, {}

    meta = {
        "avd_name": avd_name,
        "serial": serial,
        "port": port,
        "pid": proc.pid,
        "started_at": time.time(),
        "proc": proc,
    }
    with _lock:
        _running[serial] = meta
        _running[avd_name] = meta

    uat_logger.info("AVD 已启动: %s serial=%s pid=%s", avd_name, serial, proc.pid)
    return True, f"模拟器 {avd_name} 已启动（{serial}）", {
        "serial": serial,
        "avd_name": avd_name,
        "port": port,
        "pid": proc.pid,
    }


def stop_avd(serial: str = "", avd_name: str = "") -> Tuple[bool, str]:
    """停止模拟器（adb emu kill 或终止子进程）。"""
    serial = (serial or "").strip()
    avd_name = (avd_name or "").strip()
    meta: Optional[Dict[str, Any]] = None
    with _lock:
        if serial and serial in _running:
            meta = _running.pop(serial)
        elif avd_name and avd_name in _running:
            meta = _running.pop(avd_name)
        if meta:
            for k in list(_running.keys()):
                if _running.get(k) is meta:
                    _running.pop(k, None)

    target_serial = serial or (meta or {}).get("serial") or ""
    if target_serial.startswith("emulator-"):
        try:
            subprocess.run(
                [adb_path(), "-s", target_serial, "emu", "kill"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except Exception:
            pass

    proc = (meta or {}).get("proc")
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return True, "模拟器已停止"


def is_emulator_serial(serial: str) -> bool:
    return (serial or "").strip().startswith("emulator-")


def emulator_status() -> Dict[str, Any]:
    ok, msg = emulator_available()
    return {
        "emulator_available": ok,
        "emulator_message": msg if ok else msg,
        "emulator_exe": msg if ok else "",
        "android_sdk_home": android_sdk_home(),
        "avds": list_avds(),
        "running": list_running_emulators(),
        "presets": EMULATOR_AVD_PRESETS,
        "create_hint": (
            "Android Studio → Device Manager → Create Device，或命令行："
            "avdmanager create avd -n Testory_Pixel7 -k "
            "\"system-images;android-34;google_apis;x86_64\" -d pixel_7"
        ),
    }


def parse_avd_create_command(preset_id: str) -> Optional[str]:
    for p in EMULATOR_AVD_PRESETS:
        if p.get("id") == preset_id:
            name = p.get("avd_name_hint") or "Testory_Device"
            img = p.get("system_image") or ""
            dev = p.get("device_id") or "pixel_6"
            return (
                f'avdmanager create avd -n {name} -k "{img}" -d {dev} '
                f'--force'
            )
    return None
