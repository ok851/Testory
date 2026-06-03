# -*- coding: utf-8 -*-
"""Android SDK Emulator (AVD) 生命周期管理 — 模拟器优先模式。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ProgressCallback = Optional[Callable[[int, str], None]]

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


def _resolve_adb() -> str:
    root = _sdk_root()
    if root:
        try:
            from mobile_emulator_sdk_bundles import resolve_adb_in_sdk

            sdk_adb = resolve_adb_in_sdk(root)
            if sdk_adb:
                return sdk_adb
        except Exception:
            pass
    return adb_path()


def _emulator_env() -> Dict[str, str]:
    env = dict(os.environ)
    root = _sdk_root()
    if not root:
        return env
    sdk = str(root.resolve())
    env["ANDROID_SDK_ROOT"] = sdk
    env["ANDROID_HOME"] = sdk
    prefix = os.pathsep.join(
        [
            str(root / "emulator"),
            str(root / "platform-tools"),
            str(root / "cmdline-tools" / "latest" / "bin"),
        ]
    )
    env["PATH"] = prefix + os.pathsep + env.get("PATH", "")
    return env


def _avd_block_dir(avd_name: str) -> Optional[Path]:
    home = (os.environ.get("ANDROID_AVD_HOME") or "").strip()
    if home:
        base = Path(home)
    else:
        base = Path(os.path.expanduser("~")) / ".android" / "avd"
    block = base / f"{avd_name}.avd"
    return block if block.is_dir() else None


def _required_avd_disk_bytes(avd_name: str) -> int:
    block = _avd_block_dir(avd_name)
    need = 6 * 1024 * 1024 * 1024
    if block and (block / "config.ini").is_file():
        try:
            for line in (block / "config.ini").read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.startswith("disk.dataPartition.size"):
                    continue
                raw = line.split("=", 1)[-1].strip()
                if raw.isdigit():
                    need = int(raw)
                elif raw.endswith("M"):
                    need = int(float(raw[:-1]) * 1024 * 1024)
                elif raw.endswith("G"):
                    need = int(float(raw[:-1]) * 1024 * 1024 * 1024)
                break
        except (OSError, ValueError):
            pass
    return need + 2 * 1024 * 1024 * 1024


def _disk_space_preflight(avd_name: str) -> Optional[str]:
    block = _avd_block_dir(avd_name)
    if not block:
        return None
    try:
        usage = shutil.disk_usage(block)
    except OSError:
        return None
    need = _required_avd_disk_bytes(avd_name)
    if usage.free >= need:
        return None
    drive = block.drive or "系统盘"
    return (
        f"磁盘空间不足：{drive} 可用约 {usage.free / (1024 ** 3):.1f} GB，"
        f"启动「{avd_name}」约需 {need / (1024 ** 3):.1f} GB。"
        "请清理磁盘，或将 ANDROID_AVD_HOME 设置到其他盘（.env / 系统环境变量）后重启本软件。"
    )


def _parse_emulator_fatal(output: str) -> str:
    text = output or ""
    for line in text.splitlines():
        s = line.strip()
        if "FATAL" in s or s.lower().startswith("error:"):
            s = re.sub(r"^(INFO|WARNING|ERROR|FATAL)\s*\|\s*", "", s)
            if "Not enough space" in s or "not enough space" in s.lower():
                return (
                    "创建虚拟手机数据分区时磁盘空间不足。"
                    "请清理 C 盘（或 AVD 所在盘）至少 8GB，或设置 ANDROID_AVD_HOME 到大容量磁盘。"
                )
            if "multiple emulators" in s.lower() or "same AVD" in s:
                return (
                    "已有同一虚拟手机的后台进程未退出。"
                    "请点「停止」或关闭任务管理器中的 emulator/qemu 后重试。"
                )
            return s[:500]
    tail = text.strip()[-400:]
    return tail if tail else "模拟器进程异常退出"


def _gpu_modes_for_start(requested: str) -> List[str]:
    modes: List[str] = []
    for m in ((requested or "").strip(), "swiftshader_indirect", "host", "angle_indirect"):
        if m and m not in modes:
            modes.append(m)
    return modes


def _read_process_output(proc: subprocess.Popen[str]) -> str:
    try:
        if proc.stdout:
            return proc.stdout.read() or ""
    except Exception:
        pass
    return ""


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
            "未检测到 Android 模拟器。推荐：用户菜单 → 插件市场 → 安装「Android 模拟器 SDK（命令行）」"
            "（需 JDK 11+，首次约 2–4GB）。或安装 Android Studio 后在 .env 设置 ANDROID_HOME。"
            "详见本页「如何安装 Android SDK / 模拟器？」"
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


def _prune_dead_running_records() -> None:
    with _lock:
        dead_keys: List[str] = []
        seen_meta: set[int] = set()
        for key, meta in list(_running.items()):
            proc = meta.get("proc")
            if proc is None:
                continue
            pid = id(proc)
            if proc.poll() is not None:
                if pid not in seen_meta:
                    seen_meta.add(pid)
                dead_keys.append(key)
        for key in dead_keys:
            _running.pop(key, None)


def _cleanup_stale_emulators(
    adb: str,
    env: Dict[str, str],
    *,
    port: int = 5554,
    avd_name: str = "",
) -> None:
    """结束残留的 emulator 进程，避免「同一 AVD 不能启动两次」。"""
    serial = _serial_for_port(port)
    if _serial_adb_state(adb, serial, env=env) == "device":
        return
    targets: List[str] = []
    try:
        listed = subprocess.run(
            [adb, "devices"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
        )
        for line in (listed.stdout or "").splitlines():
            parts = line.split()
            if parts and parts[0].startswith("emulator-"):
                targets.append(parts[0])
    except Exception:
        targets = []
    if serial not in targets:
        targets.append(serial)
    for udid in targets:
        try:
            subprocess.run(
                [adb, "-s", udid, "emu", "kill"],
                capture_output=True,
                timeout=20,
                check=False,
                env=env,
            )
        except Exception:
            pass
    time.sleep(2)
    _prune_dead_running_records()
    with _lock:
        _running.pop(serial, None)
        if (avd_name or "").strip():
            _running.pop((avd_name or "").strip(), None)


def _wait_boot_completed(serial: str, timeout: int = 120) -> Tuple[bool, str]:
    adb = _resolve_adb()
    env = _emulator_env()
    deadline = time.time() + timeout
    device_hits = 0
    while time.time() < deadline:
        try:
            proc = subprocess.run(
                [adb, "-s", serial, "shell", "getprop", "sys.boot_completed"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                env=env,
            )
            if (proc.stdout or "").strip() == "1":
                return True, "启动完成"
        except Exception:
            pass
        if _serial_adb_state(adb, serial, env=env) == "device":
            device_hits += 1
            if device_hits >= 4:
                return True, "启动完成（adb 已就绪）"
        else:
            device_hits = 0
        time.sleep(2)
    return False, f"等待 Android 系统就绪超时（{timeout}s），可点「停止」后重试"


def _ensure_adb_server(adb: str, env: Dict[str, str]) -> None:
    """启动 adb 服务（勿随意 kill-server，否则会断开正在启动的模拟器）。"""
    try:
        subprocess.run(
            [adb, "start-server"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
    except Exception:
        pass


def _reset_adb_server(adb: str, env: Dict[str, str]) -> None:
    """仅在清理残留模拟器后重启 adb。"""
    for args in (["kill-server"], ["start-server"]):
        try:
            subprocess.run(
                [adb, *args],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=env,
            )
        except Exception:
            pass


def _serial_adb_state(adb: str, serial: str, *, env: Optional[Dict[str, str]] = None) -> str:
    try:
        listed = subprocess.run(
            [adb, "devices"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        for line in (listed.stdout or "").splitlines():
            if line.strip().startswith(serial):
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1].strip()
    except Exception:
        pass
    return ""


def _wait_serial_ready(
    adb: str,
    serial: str,
    env: Dict[str, str],
    *,
    timeout: int = 180,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _serial_adb_state(adb, serial, env=env)
        if state == "device":
            return True
        if state == "offline":
            time.sleep(2)
            continue
        time.sleep(2)
    try:
        subprocess.run(
            [adb, "-s", serial, "wait-for-device"],
            capture_output=True,
            timeout=max(30, int(deadline - time.time())),
            check=False,
            env=env,
        )
    except Exception:
        return False
    return _serial_adb_state(adb, serial, env=env) == "device"


def _wait_emulator_online(
    adb: str,
    serial: str,
    proc: subprocess.Popen[str],
    env: Dict[str, str],
    *,
    timeout: int = 180,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str]:
    deadline = time.time() + timeout
    last_state = ""
    while time.time() < deadline:
        state = _serial_adb_state(adb, serial, env=env)
        last_state = state or last_state
        if progress_cb and state:
            elapsed = int(time.time() - (deadline - timeout))
            progress_cb(
                min(70, 25 + elapsed // 4),
                f"adb 状态 {state or '等待中'}，继续等待 {serial}…",
            )
        if state == "device":
            return True, ""
        if state == "offline":
            remain = max(20, int(deadline - time.time()))
            if _wait_serial_ready(adb, serial, env, timeout=remain):
                return True, ""
        proc_dead = proc.poll() is not None
        if proc_dead and not state:
            return False, _parse_emulator_fatal(_read_process_output(proc))
        time.sleep(2)
    grace_deadline = time.time() + 120
    while time.time() < grace_deadline:
        state = _serial_adb_state(adb, serial, env=env)
        if progress_cb:
            progress_cb(72, f"模拟器仍在启动，adb 状态 {state or '等待中'}…")
        if state == "device":
            return True, ""
        if state == "offline" and _wait_serial_ready(
            adb, serial, env, timeout=max(30, int(grace_deadline - time.time()))
        ):
            return True, ""
        time.sleep(3)
    if _serial_adb_state(adb, serial, env=env) == "device":
        return True, ""
    if proc.poll() is not None and not last_state:
        return False, _parse_emulator_fatal(_read_process_output(proc))
    return False, (
        f"adb 未检测到 {serial}（等待 {timeout}s+，末状态 {last_state or '无'}）。"
        "无头模式启动较慢，请稍候再点「启动」；若仍失败，先点「停止」结束 qemu 后重试。"
    )


def start_avd(
    avd_name: str,
    port: int = 5554,
    gpu: str = "host",
    no_window: bool = True,
    progress_cb: ProgressCallback = None,
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

    disk_err = _disk_space_preflight(avd_name)
    if disk_err:
        return False, disk_err, {}

    env = _emulator_env()
    accel_err = _accel_check_message(exe_or_msg, env)
    if accel_err:
        return False, accel_err, {}

    serial = _serial_for_port(port)

    def _progress(pct: int, label: str) -> None:
        if progress_cb:
            progress_cb(int(pct), label)

    adb = _resolve_adb()
    _progress(8, "检查模拟器环境…")
    stale_before = _serial_adb_state(adb, serial, env=env)
    _cleanup_stale_emulators(adb, env, port=port, avd_name=avd_name)
    if stale_before != "device":
        _reset_adb_server(adb, env)
    else:
        _ensure_adb_server(adb, env)

    with _lock:
        for meta in _running.values():
            if not isinstance(meta, dict):
                continue
            if meta.get("avd_name") != avd_name and meta.get("serial") != serial:
                continue
            proc = meta.get("proc")
            if proc is not None and proc.poll() is None:
                return False, f"AVD「{avd_name}」或端口 {port} 已在运行", {}

    if _wait_serial_ready(adb, serial, env, timeout=12):
        _progress(70, "检测到模拟器已在运行，正在验证系统…")
        boot_ok, boot_msg = _wait_boot_completed(serial, timeout=45)
        if boot_ok:
            meta = {
                "avd_name": avd_name,
                "serial": serial,
                "port": port,
                "pid": 0,
                "started_at": time.time(),
                "proc": None,
                "headless": no_window,
            }
            with _lock:
                _running[serial] = meta
                _running[avd_name] = meta
            return True, f"模拟器 {avd_name} 已在运行（{serial}）", {
                "serial": serial,
                "avd_name": avd_name,
                "port": port,
                "pid": 0,
                "reused": True,
            }
        return False, boot_msg, {}

    online_timeout = 240 if no_window else 180
    _progress(15, "正在启动模拟器（无独立窗口）…")
    popen_kw: Dict[str, Any] = {
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if _platform_key() == "windows":
        popen_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0

    proc: Optional[subprocess.Popen[str]] = None
    last_err = ""
    for gpu_mode in _gpu_modes_for_start(gpu):
        cmd = [
            exe_or_msg,
            "-avd",
            avd_name,
            "-port",
            str(port),
            "-gpu",
            gpu_mode,
            "-no-snapshot-load",
            "-no-boot-anim",
        ]
        if no_window:
            cmd.append("-no-window")
        try:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            proc = subprocess.Popen(cmd, **popen_kw)
        except Exception as exc:
            return False, f"启动模拟器失败：{exc}", {}

        _progress(25, f"等待 adb 识别 {serial}…")
        online_ok, online_msg = _wait_emulator_online(
            adb, serial, proc, env, timeout=online_timeout, progress_cb=progress_cb
        )
        if online_ok:
            break
        last_err = online_msg
        if proc.poll() is not None:
            fatal = (online_msg or "").lower()
            if "multiple emulators" in fatal or "same avd" in fatal:
                return False, online_msg, {}
            if "space" not in fatal:
                continue
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        return False, last_err, {}

    if proc is None:
        return False, last_err or "启动模拟器失败", {}

    _progress(75, "等待 Android 系统启动…")
    boot_ok, boot_msg = _wait_boot_completed(serial)
    if not boot_ok:
        try:
            proc.terminate()
        except Exception:
            pass
        extra = _parse_emulator_fatal(_read_process_output(proc))
        return False, boot_msg + (f" {extra}" if extra and extra not in boot_msg else ""), {}

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
    mode = "无窗口，画面在平台右侧画布" if no_window else "独立窗口"
    return True, f"模拟器 {avd_name} 已启动（{serial}，{mode}）", {
        "serial": serial,
        "avd_name": avd_name,
        "port": port,
        "pid": proc.pid,
        "headless": no_window,
    }


def _platform_key() -> str:
    if os.name == "nt":
        return "windows"
    return "linux"


def _hypervisor_driver_installer(sdk_root: Optional[Path]) -> Optional[Path]:
    if not sdk_root:
        return None
    bat = sdk_root / "extras" / "google" / "Android_Emulator_Hypervisor_Driver" / "silent_install.bat"
    return bat if bat.is_file() else None


def _hypervisor_service_running() -> bool:
    if os.name != "nt":
        return True
    try:
        proc = subprocess.run(
            ["sc", "query", "aehd"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return "RUNNING" in (proc.stdout or "")
    except Exception:
        return False


def _accel_check_message(exe: str, env: Dict[str, str]) -> Optional[str]:
    """Windows x86 模拟器需硬件加速；未安装时返回用户可操作说明。"""
    if os.name != "nt":
        return None
    if _hypervisor_service_running():
        return None
    try:
        proc = subprocess.run(
            [exe, "-accel-check"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        return None
    if "not installed" not in out.lower() and proc.returncode in (0,):
        return None
    installer = _hypervisor_driver_installer(_sdk_root())
    lines = [
        "本机未启用 Android 模拟器硬件加速（Hypervisor），x86 模拟器无法启动。",
    ]
    if installer:
        lines.append(
            f"请以管理员身份打开命令提示符，执行：\n{installer}\n"
            "看到 STATE: RUNNING 后重启电脑，再回到本页点击「启动模拟器」。"
        )
    else:
        lines.append(
            "请在插件市场对「Android 模拟器 SDK」点「创建虚拟手机」，"
            "以下载 Hypervisor 驱动后再按提示安装。"
        )
    lines.append(
        "或在 Windows「启用或关闭 Windows 功能」中勾选「虚拟机平台」「Windows 超管理器平台」后重启。"
    )
    return "\n".join(lines)


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
                [_resolve_adb(), "-s", target_serial, "emu", "kill"],
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
    avds = list_avds()
    hint = ""
    if ok and not avds:
        hint = "未检测到虚拟手机。请在插件市场重新安装「Android 模拟器 SDK」并等待安装完成。"
    disk_warn = ""
    accel_warn = ""
    for avd in avds[:1]:
        err = _disk_space_preflight(avd.get("name") or "")
        if err:
            disk_warn = err
            break
    if ok:
        env = _emulator_env()
        accel_warn = _accel_check_message(msg, env) or ""
    return {
        "emulator_available": ok,
        "emulator_message": msg if ok else msg,
        "emulator_exe": msg if ok else "",
        "android_sdk_home": android_sdk_home(),
        "avds": avds,
        "running": list_running_emulators(),
        "presets": EMULATOR_AVD_PRESETS,
        "setup_hint": accel_warn or disk_warn or hint,
        "disk_space_ok": not bool(disk_warn),
        "hypervisor_ok": not bool(accel_warn),
        "hypervisor_installer": str(_hypervisor_driver_installer(_sdk_root()) or ""),
        "create_hint": (
            "插件市场安装「Android 模拟器 SDK（命令行）」将自动创建 Testory_Pixel7；"
            "或 Android Studio → Device Manager 手动创建"
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
