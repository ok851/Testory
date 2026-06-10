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
_emulator_op_lock = threading.Lock()
_shutdown_hook_registered = False

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


def _gpu_modes_for_start(requested: str, *, no_window: bool = False) -> List[str]:
    from mobile_env_config import resolve_emulator_gpu

    primary = resolve_emulator_gpu(requested, no_window=no_window)
    fallbacks = (
        ("swiftshader_indirect", "angle_indirect", "host")
        if no_window and os.name == "nt"
        else ("swiftshader_indirect", "host", "angle_indirect")
    )
    modes: List[str] = []
    for m in (primary,) + fallbacks:
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


def _emulator_log_dir() -> Path:
    base = Path(os.environ.get("UAT_DATA_DIR") or Path(__file__).resolve().parent / "logs")
    d = base / "emulator"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _emulator_log_path(avd_name: str) -> Path:
    safe = re.sub(r"[^\w.-]+", "_", (avd_name or "avd").strip())[:48]
    return _emulator_log_dir() / f"{safe}-{int(time.time())}.log"


def _read_emulator_log_tail(log_path: Optional[Path], *, max_bytes: int = 12000) -> str:
    if not log_path or not log_path.is_file():
        return ""
    try:
        size = log_path.stat().st_size
        with log_path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _fatal_from_emulator_output(output: str) -> Optional[str]:
    text = output or ""
    for line in text.splitlines():
        s = line.strip()
        if "FATAL" in s or s.lower().startswith("error:"):
            parsed = _parse_emulator_fatal(text)
            if parsed and parsed != "模拟器进程异常退出":
                return parsed
            return s[:500]
    return None


def _kill_platform_emulator_processes() -> None:
    """结束无 adb 绑定的 headless qemu / emulator 残留（Windows 常见）。"""
    if os.name != "nt":
        return
    for image in (
        "qemu-system-x86_64-headless.exe",
        "qemu-system-x86_64.exe",
        "emulator.exe",
    ):
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", image],
                capture_output=True,
                timeout=20,
                check=False,
            )
        except Exception:
            pass


def _qemu_process_count() -> int:
    """当前 Windows 上 qemu 模拟器进程数量。"""
    if os.name != "nt":
        return 0
    count = 0
    for image in ("qemu-system-x86_64-headless.exe", "qemu-system-x86_64.exe"):
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            for line in (proc.stdout or "").splitlines():
                if image.split(".")[0] in line.lower():
                    count += 1
        except Exception:
            pass
    return count


def _emulator_slot_busy(adb: str, serial: str, env: Dict[str, str]) -> bool:
    """端口上已有模拟器在启动或运行（adb offline/device 或 qemu 进程存在）。"""
    state = _serial_adb_state(adb, serial, env=env)
    if state in ("device", "offline"):
        return True
    return _qemu_process_count() > 0


def _wait_existing_emulator_boot(
    avd_name: str,
    *,
    port: int = 5554,
    progress_cb: ProgressCallback = None,
    timeout: int = 120,
) -> Tuple[bool, str, Dict[str, Any]]:
    """已有实例在启动中：等待就绪，绝不另起新进程。"""
    serial = _serial_for_port(port)
    adb = _resolve_adb()
    env = _emulator_env()
    if progress_cb:
        progress_cb(40, "模拟器正在启动，请稍候…")
    if not _wait_serial_ready(adb, serial, env, timeout=min(60, timeout)):
        return False, "模拟器仍在启动中或异常，请先点「停止」后重试", {}
    if progress_cb:
        progress_cb(60, "等待 Android 系统就绪…")
    boot_ok, boot_msg = _wait_boot_completed(serial, timeout=timeout, progress_cb=progress_cb)
    if not boot_ok:
        return False, boot_msg, {}
    meta: Dict[str, Any] = {
        "serial": serial,
        "avd_name": avd_name,
        "port": port,
        "pid": 0,
        "reused": True,
        "headless": True,
    }
    record = {**meta, "started_at": time.time(), "proc": None}
    with _lock:
        _running[serial] = record
        if avd_name:
            _running[avd_name] = record
    return True, f"已连接运行中的模拟器（{serial}）", meta


def stop_all_emulators() -> Tuple[bool, str]:
    """停止所有模拟器（退出软件时调用）。"""
    for run in list_running_emulators():
        serial = (run.get("serial") or "").strip()
        avd = (run.get("avd_name") or "").strip()
        if serial or avd:
            stop_avd(serial=serial, avd_name=avd)
    adb = _resolve_adb()
    env = _emulator_env()
    for port in (5554, 5556, 5558):
        serial = _serial_for_port(port)
        if _serial_adb_state(adb, serial, env=env) in ("device", "offline"):
            _force_stop_serial(adb, serial, env)
    _kill_platform_emulator_processes()
    with _lock:
        _running.clear()
    try:
        from mobile_scrcpy_bridge import stop_all_bridge_sessions

        stop_all_bridge_sessions()
    except Exception:
        pass
    uat_logger.info("已清理所有 Android 模拟器进程")
    return True, "模拟器已全部停止"


_skip_cleanup_on_exit = False


def set_emulator_cleanup_on_exit(enabled: bool) -> None:
    """桌面版退出前可关闭自动清理（模拟器继续后台运行）。"""
    global _skip_cleanup_on_exit
    _skip_cleanup_on_exit = not enabled


def register_emulator_shutdown_hook() -> None:
    """进程退出时自动清理模拟器（避免 qemu 僵尸进程）。"""
    global _shutdown_hook_registered
    if _shutdown_hook_registered:
        return
    if (os.environ.get("MOBILE_EMULATOR_STOP_ON_EXIT") or "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return
    import atexit

    def _cleanup() -> None:
        if _skip_cleanup_on_exit:
            return
        stop_all_emulators()

    atexit.register(_cleanup)
    _shutdown_hook_registered = True


def acquire_emulator_op_lock() -> bool:
    return _emulator_op_lock.acquire(blocking=False)


def release_emulator_op_lock() -> None:
    try:
        _emulator_op_lock.release()
    except RuntimeError:
        pass


def _try_adb_reconnect(adb: str, serial: str, env: Dict[str, str]) -> bool:
    """adb 显示 offline 时尝试重连（不杀进程，启动阶段 offline 属正常）。"""
    try:
        subprocess.run(
            [adb, "-s", serial, "reconnect"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
        )
    except Exception:
        pass
    time.sleep(2)
    return _serial_adb_state(adb, serial, env=env) == "device"


def _recover_offline_emulator(adb: str, serial: str, env: Dict[str, str]) -> bool:
    """启动前清理 offline 残留；会先 reconnect，失败再 kill。"""
    if _try_adb_reconnect(adb, serial, env):
        return True
    _force_stop_serial(adb, serial, env)
    _kill_platform_emulator_processes()
    time.sleep(1)
    return _serial_adb_state(adb, serial, env=env) == "device"


def _ensure_emulator_slot_free(
    adb: str,
    serial: str,
    env: Dict[str, str],
    *,
    avd_name: str = "",
    port: int = 5554,
    progress_cb: ProgressCallback = None,
) -> None:
    """启动前释放端口/AVD 占用（含 offline 与无 adb 的 qemu 僵尸）。"""
    if progress_cb:
        progress_cb(10, "清理残留模拟器进程…")
    state = _serial_adb_state(adb, serial, env=env)
    if state in ("device", "offline"):
        _force_stop_serial(adb, serial, env)
        _wait_serial_gone(adb, serial, env, timeout=12)
    _cleanup_stale_emulators(adb, env, port=port, avd_name=avd_name, force=True)
    _kill_platform_emulator_processes()
    _wait_serial_gone(adb, serial, env, timeout=8)
    _reset_adb_server(adb, env)
    _ensure_adb_server(adb, env)
    time.sleep(2)


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


def _force_stop_serial(adb: str, serial: str, env: Dict[str, str]) -> None:
    """强制结束指定 emulator serial（adb emu kill + 清理内存记录）。"""
    serial = (serial or "").strip()
    if not serial.startswith("emulator-"):
        return
    try:
        subprocess.run(
            [adb, "-s", serial, "emu", "kill"],
            capture_output=True,
            timeout=20,
            check=False,
            env=env,
        )
    except Exception:
        pass
    with _lock:
        meta = _running.pop(serial, None)
        if meta:
            avd = (meta.get("avd_name") or "").strip()
            if avd:
                _running.pop(avd, None)
            proc = meta.get("proc")
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass


def _wait_serial_gone(
    adb: str,
    serial: str,
    env: Dict[str, str],
    *,
    timeout: int = 20,
) -> bool:
    """等待 adb 不再识别该 emulator serial。"""
    deadline = time.time() + max(3, int(timeout))
    while time.time() < deadline:
        state = _serial_adb_state(adb, serial, env=env)
        if state != "device":
            return True
        time.sleep(1)
    return _serial_adb_state(adb, serial, env=env) != "device"


def _cleanup_stale_emulators(
    adb: str,
    env: Dict[str, str],
    *,
    port: int = 5554,
    avd_name: str = "",
    force: bool = False,
) -> None:
    """结束残留的 emulator 进程，避免「同一 AVD 不能启动两次」。"""
    serial = _serial_for_port(port)
    if not force and _serial_adb_state(adb, serial, env=env) == "device":
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


def _wait_boot_completed(
    serial: str,
    timeout: int = 120,
    *,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str]:
    adb = _resolve_adb()
    env = _emulator_env()
    deadline = time.time() + timeout
    started = time.time()
    device_hits = 0
    while time.time() < deadline:
        if progress_cb:
            elapsed = max(0, int(time.time() - started))
            progress_cb(
                min(94, 70 + elapsed * 2),
                f"等待 Android 系统就绪…（{elapsed}s）",
            )
        for prop in ("sys.boot_completed", "dev.bootcomplete"):
            try:
                proc = subprocess.run(
                    [adb, "-s", serial, "shell", "getprop", prop],
                    capture_output=True,
                    text=True,
                    timeout=6,
                    check=False,
                    env=env,
                )
                if (proc.stdout or "").strip() == "1":
                    return True, "启动完成"
            except Exception:
                pass
        if _serial_adb_state(adb, serial, env=env) == "device":
            device_hits += 1
            if device_hits >= 2:
                return True, "启动完成（adb 已就绪）"
        else:
            device_hits = 0
        time.sleep(1.5)
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
    timeout: int = 120,
    progress_cb: ProgressCallback = None,
    log_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    deadline = time.time() + timeout
    last_state = ""
    offline_since: Optional[float] = None
    started = time.time()
    while time.time() < deadline:
        state = _serial_adb_state(adb, serial, env=env)
        last_state = state or last_state
        elapsed = max(0, int(time.time() - started))
        if progress_cb:
            detail = state or "未出现"
            progress_cb(
                min(74, 25 + elapsed // 2),
                f"等待 adb 识别 {serial}…（{detail}，{elapsed}s）",
            )
        log_tail = _read_emulator_log_tail(log_path)
        fatal = _fatal_from_emulator_output(log_tail)
        if fatal:
            return False, fatal
        if state == "device":
            return True, ""
        if state == "offline":
            if offline_since is None:
                offline_since = time.time()
            elif time.time() - offline_since >= 18:
                if progress_cb:
                    progress_cb(
                        min(68, 30 + elapsed // 2),
                        f"{serial} offline，尝试 adb 重连…（{elapsed}s）",
                    )
                if _try_adb_reconnect(adb, serial, env):
                    return True, ""
                offline_since = time.time()
        else:
            offline_since = None
        proc_dead = proc.poll() is not None
        if proc_dead:
            err = _fatal_from_emulator_output(log_tail) or _fatal_from_emulator_output(
                _read_process_output(proc)
            )
            if err:
                return False, err
            if not state:
                return False, (
                    "模拟器进程已退出且 adb 未识别设备。"
                    "请点「停止」清理任务管理器中的 qemu/emulator 后重试。"
                )
        time.sleep(2)
    grace_deadline = time.time() + 45
    while time.time() < grace_deadline:
        state = _serial_adb_state(adb, serial, env=env)
        elapsed = max(0, int(time.time() - started))
        if progress_cb:
            progress_cb(
                min(78, 74 + elapsed // 8),
                f"模拟器仍在启动，adb 状态 {state or '等待中'}…（{elapsed}s）",
            )
        log_tail = _read_emulator_log_tail(log_path)
        fatal = _fatal_from_emulator_output(log_tail)
        if fatal:
            return False, fatal
        if state == "device":
            return True, ""
        if state == "offline" and _try_adb_reconnect(adb, serial, env):
            return True, ""
        if proc.poll() is not None and not state:
            err = _fatal_from_emulator_output(log_tail)
            if err:
                return False, err
        time.sleep(3)
    if _serial_adb_state(adb, serial, env=env) == "device":
        return True, ""
    if proc.poll() is not None and not last_state:
        err = _fatal_from_emulator_output(_read_emulator_log_tail(log_path))
        if err:
            return False, err
    log_hint = ""
    tail = _read_emulator_log_tail(log_path, max_bytes=400)
    if tail.strip():
        log_hint = f" 最近日志：{tail.strip()[-240:]}"
    return False, (
        f"adb 未检测到 {serial}（等待 {timeout}s+，末状态 {last_state or '无'}）。"
        "请先点「停止」结束任务管理器中的 qemu/emulator 后重试。"
        f"{log_hint}"
    )


def _truthy_env(key: str, default: str = "1") -> bool:
    raw = (os.environ.get(key) or default).strip().lower()
    return raw not in ("0", "false", "no", "off")


def start_avd(
    avd_name: str,
    port: int = 5554,
    gpu: str = "host",
    no_window: bool = True,
    progress_cb: ProgressCallback = None,
    *,
    skip_preflight_cleanup: bool = False,
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
    if not _truthy_env("MOBILE_EMULATOR_ALLOW_WINDOW", "0"):
        no_window = True

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
    if not skip_preflight_cleanup:
        _ensure_emulator_slot_free(
            adb, serial, env, avd_name=avd_name, port=port, progress_cb=_progress
        )
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

    if _wait_serial_ready(adb, serial, env, timeout=8):
        _progress(70, "检测到模拟器已在运行，正在验证系统…")
        boot_ok, boot_msg = _wait_boot_completed(
            serial, timeout=30, progress_cb=_progress
        )
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
        _progress(72, "模拟器未就绪，正在清理并重新启动…")
        _force_stop_serial(adb, serial, env)
        _wait_serial_gone(adb, serial, env, timeout=15)
        _reset_adb_server(adb, env)

    online_timeout = 120 if no_window else 90
    _progress(15, "正在启动模拟器（无独立窗口）…")
    log_path = _emulator_log_path(avd_name)
    log_handle = None
    popen_kw: Dict[str, Any] = {
        "env": env,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if _platform_key() == "windows":
        popen_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0

    proc: Optional[subprocess.Popen[str]] = None
    last_err = ""
    for gpu_mode in _gpu_modes_for_start(gpu, no_window=no_window):
        cmd = [
            exe_or_msg,
            "-avd",
            avd_name,
            "-port",
            str(port),
            "-gpu",
            gpu_mode,
            "-no-boot-anim",
            "-no-audio",
        ]
        if no_window:
            cmd.extend(["-no-window"])
        try:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            if log_handle:
                try:
                    log_handle.close()
                except Exception:
                    pass
            log_handle = log_path.open("w", encoding="utf-8", errors="replace")
            popen_kw["stdout"] = log_handle
            popen_kw["stderr"] = subprocess.STDOUT
            proc = subprocess.Popen(cmd, **popen_kw)
            try:
                log_handle.close()
            except Exception:
                pass
            log_handle = None
        except Exception as exc:
            return False, f"启动模拟器失败：{exc}", {}

        _progress(25, f"等待 adb 识别 {serial}…")
        online_ok, online_msg = _wait_emulator_online(
            adb,
            serial,
            proc,
            env,
            timeout=online_timeout,
            progress_cb=progress_cb,
            log_path=log_path,
        )
        if online_ok:
            break
        last_err = online_msg
        fatal_lower = (online_msg or "").lower()
        if proc.poll() is not None and (
            "multiple emulators" in fatal_lower
            or "same avd" in fatal_lower
            or "后台进程" in (online_msg or "")
        ):
            return False, online_msg, {"log_path": str(log_path)}
        if proc.poll() is not None and "space" not in fatal_lower:
            continue
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        return False, last_err, {"log_path": str(log_path)}

    if proc is None:
        return False, last_err or "启动模拟器失败", {}

    _progress(75, "等待 Android 系统启动…")
    boot_ok, boot_msg = _wait_boot_completed(serial, progress_cb=_progress)
    if not boot_ok:
        try:
            proc.terminate()
        except Exception:
            pass
        extra = _fatal_from_emulator_output(_read_emulator_log_tail(log_path)) or _parse_emulator_fatal(
            _read_process_output(proc)
        )
        return False, boot_msg + (f" {extra}" if extra and extra not in boot_msg else ""), {
            "log_path": str(log_path),
        }

    meta = {
        "avd_name": avd_name,
        "serial": serial,
        "port": port,
        "pid": proc.pid,
        "started_at": time.time(),
        "proc": proc,
        "log_path": str(log_path),
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

    adb = _resolve_adb()
    env = _emulator_env()
    target_serial = serial or (meta or {}).get("serial") or ""
    if not target_serial:
        for port in (5554, 5556, 5558):
            cand = _serial_for_port(port)
            if _serial_adb_state(adb, cand, env=env) in ("device", "offline"):
                target_serial = cand
                break
    if target_serial.startswith("emulator-"):
        _force_stop_serial(adb, target_serial, env)
        try:
            subprocess.run(
                [adb, "-s", target_serial, "emu", "kill"],
                capture_output=True,
                timeout=15,
                check=False,
                env=env,
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
    _kill_platform_emulator_processes()
    with _lock:
        _running.clear()
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
    setup = {}
    try:
        from mobile_emulator_sdk_bundles import emulator_sdk_setup_status

        setup = emulator_sdk_setup_status()
    except Exception:
        pass
    return {
        "emulator_available": ok,
        "emulator_message": msg if ok else msg,
        "emulator_exe": msg if ok else "",
        "android_sdk_home": android_sdk_home(),
        "avds": avds,
        "running": list_running_emulators(),
        "presets": EMULATOR_AVD_PRESETS,
        "models": list_emulator_models(),
        "sdk_ready": bool(setup.get("sdk_ready")),
        "system_image_ready": bool(setup.get("system_image_ready")),
        "avd_ready": bool(setup.get("avd_ready")),
        "default_avd": setup.get("default_avd") or "",
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


PRESET_FRAME_IDS: Dict[str, str] = {
    "pixel_7": "pixel_7",
    "samsung_s23": "samsung_s23",
    "xiaomi_14": "xiaomi_14",
    "tablet_10": "ipad_mini",
}


def get_preset_by_id(preset_id: str) -> Optional[Dict[str, Any]]:
    pid = (preset_id or "").strip()
    for p in EMULATOR_AVD_PRESETS:
        if p.get("id") == pid:
            return dict(p)
    return None


def avd_exists(avd_name: str) -> bool:
    name = (avd_name or "").strip()
    if not name:
        return False
    return any((a.get("name") or "") == name for a in list_avds())


def frame_preset_for_model(preset_id: str) -> str:
    return PRESET_FRAME_IDS.get((preset_id or "").strip(), "generic_19_9")


def list_emulator_models() -> List[Dict[str, Any]]:
    """设备型号列表（供 UI 切换，对标微信/HBuilderX 型号选择器）。"""
    running = {r.get("avd_name"): r for r in list_running_emulators()}
    out: List[Dict[str, Any]] = []
    for p in EMULATOR_AVD_PRESETS:
        avd_name = (p.get("avd_name_hint") or "").strip()
        run = running.get(avd_name) or {}
        out.append({
            **dict(p),
            "avd_name": avd_name,
            "avd_exists": avd_exists(avd_name),
            "running": bool(run),
            "serial": run.get("serial") or "",
            "frame_preset_id": frame_preset_for_model(p.get("id") or ""),
        })
    return out


def provision_avd_for_preset(preset_id: str) -> Tuple[bool, str, str]:
    """创建 preset 对应 AVD（若不存在）。返回 (ok, avd_name, message)。"""
    preset = get_preset_by_id(preset_id)
    if not preset:
        return False, "", f"未知设备型号：{preset_id}"
    avd_name = (preset.get("avd_name_hint") or "").strip()
    if not avd_name:
        return False, "", "设备型号配置缺少 AVD 名称"
    if avd_exists(avd_name):
        return True, avd_name, f"虚拟手机「{avd_name}」已存在"
    try:
        from mobile_emulator_sdk_bundles import create_avd_for_preset

        create_avd_for_preset(preset)
        return True, avd_name, f"已创建虚拟手机「{avd_name}」"
    except Exception as exc:
        return False, avd_name, str(exc)


def wait_emulator_mirror_ready(serial: str, *, timeout: int = 90) -> Tuple[bool, str]:
    """投屏前等待模拟器 Android 系统完全就绪（参考微信开发者工具：先就绪再推流）。"""
    serial = (serial or "").strip()
    if not serial.startswith("emulator-"):
        return True, "ok"
    adb = _resolve_adb()
    env = _emulator_env()
    state = _serial_adb_state(adb, serial, env=env)
    if state == "offline":
        _try_adb_reconnect(adb, serial, env)
    if state not in ("device", "offline") and not _wait_serial_ready(adb, serial, env, timeout=min(30, timeout)):
        return False, f"模拟器 {serial} 未连接，请点「启动模拟器」"
    return _wait_boot_completed(serial, timeout=timeout)


def try_attach_running_emulator(
    avd_name: str,
    *,
    port: int = 5554,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    复用已运行的模拟器（不杀进程、不重启 AVD）。
    类似微信开发者工具 / HBuilderX：实例保活，仅等待 adb + 系统就绪。
    """
    avd_name = (avd_name or "").strip()
    serial = _serial_for_port(port)
    adb = _resolve_adb()
    env = _emulator_env()
    state = _serial_adb_state(adb, serial, env=env)
    if state not in ("device", "offline"):
        return False, "", {}
    if progress_cb:
        progress_cb(35, "检测到模拟器已在运行，正在连接…")
    if state == "offline":
        if not _try_adb_reconnect(adb, serial, env) and not _wait_serial_ready(
            adb, serial, env, timeout=25
        ):
            return False, "", {}
    if progress_cb:
        progress_cb(55, "等待 Android 系统就绪…")
    boot_ok, boot_msg = _wait_boot_completed(serial, timeout=60, progress_cb=progress_cb)
    if not boot_ok:
        return False, boot_msg, {}
    meta: Dict[str, Any] = {
        "serial": serial,
        "avd_name": avd_name,
        "port": port,
        "pid": 0,
        "reused": True,
        "headless": True,
    }
    record = {
        **meta,
        "started_at": time.time(),
        "proc": None,
    }
    with _lock:
        _running[serial] = record
        if avd_name:
            _running[avd_name] = record
    if progress_cb:
        progress_cb(95, "模拟器已就绪")
    return True, f"已复用运行中的模拟器（{serial}）", meta


def ensure_emulator_for_preset(
    preset_id: str,
    *,
    port: int = 5554,
    gpu: str = "host",
    no_window: bool = True,
    force_restart: bool = False,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    确保指定型号模拟器可用：优先复用已运行实例，必要时才冷启动。
    force_restart=True 时强制停止并重启（切换不同型号时使用）。
    """
    preset = get_preset_by_id(preset_id)
    if not preset:
        return False, f"未知设备型号：{preset_id}", {}
    label = preset.get("label") or preset_id
    avd_name = (preset.get("avd_name_hint") or "").strip()
    if not avd_name:
        return False, "设备型号配置缺少 AVD 名称", {}

    if progress_cb:
        progress_cb(8, f"检查 {label}…")

    if not force_restart:
        attached, msg, meta = try_attach_running_emulator(
            avd_name, port=port, progress_cb=progress_cb
        )
        if attached:
            meta = dict(meta or {})
            meta["preset_id"] = preset_id
            meta["frame_preset_id"] = frame_preset_for_model(preset_id)
            if progress_cb:
                progress_cb(100, "已连接")
            return True, msg, meta
        adb = _resolve_adb()
        env = _emulator_env()
        serial = _serial_for_port(port)
        wrong_avd_running = any(
            (run.get("avd_name") or "").strip()
            and (run.get("avd_name") or "").strip() != avd_name
            for run in list_running_emulators()
        )
        if _emulator_slot_busy(adb, serial, env) and not wrong_avd_running:
            ok_wait, msg_wait, meta_wait = _wait_existing_emulator_boot(
                avd_name, port=port, progress_cb=progress_cb, timeout=120
            )
            if ok_wait:
                meta_wait = dict(meta_wait or {})
                meta_wait["preset_id"] = preset_id
                meta_wait["frame_preset_id"] = frame_preset_for_model(preset_id)
                if progress_cb:
                    progress_cb(100, "已连接")
                return True, msg_wait, meta_wait
            return False, msg_wait or "模拟器启动中，请勿重复点击。可先点「停止」后重试", meta_wait or {}

    if not acquire_emulator_op_lock():
        return False, "已有模拟器启动任务进行中，请勿重复点击", {}
    try:
        if progress_cb:
            progress_cb(12, f"正在启动 {label}…")
        adb = _resolve_adb()
        env = _emulator_env()
        target_serial = _serial_for_port(port)
        for run in list_running_emulators():
            run_avd = (run.get("avd_name") or "").strip()
            run_serial = (run.get("serial") or "").strip()
            if run_serial or run_avd:
                stop_avd(serial=run_serial, avd_name=run_avd)
        _ensure_emulator_slot_free(
            adb, target_serial, env, avd_name=avd_name, port=port, progress_cb=progress_cb
        )
        if progress_cb:
            progress_cb(20, "正在准备虚拟手机…")
        ok, avd_name, prov_msg = provision_avd_for_preset(preset_id)
        if not ok:
            return False, prov_msg, {}
        if progress_cb:
            progress_cb(30, prov_msg)
        ok2, msg2, meta = start_avd(
            avd_name,
            port=port,
            gpu=gpu,
            no_window=no_window,
            progress_cb=progress_cb,
            skip_preflight_cleanup=True,
        )
        if not ok2:
            return False, msg2, meta or {}
        meta = dict(meta or {})
        meta["preset_id"] = preset_id
        meta["avd_name"] = avd_name
        meta["frame_preset_id"] = frame_preset_for_model(preset_id)
        return True, msg2, meta
    finally:
        release_emulator_op_lock()


def switch_emulator_model(
    preset_id: str,
    *,
    port: int = 5554,
    gpu: str = "host",
    no_window: bool = True,
    force_restart: bool = True,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """切换设备型号；force_restart=False 时优先复用已运行实例。"""
    return ensure_emulator_for_preset(
        preset_id,
        port=port,
        gpu=gpu,
        no_window=no_window,
        force_restart=force_restart,
        progress_cb=progress_cb,
    )


def emulator_diagnostics() -> Dict[str, Any]:
    """汇总模拟器环境诊断（供 UI 环境检查面板）。"""
    from mobile_env_config import mobile_enabled

    status = emulator_status()
    bridge: Dict[str, Any] = {}
    try:
        from mobile_scrcpy_bridge import bridge_health

        bridge = bridge_health()
    except Exception:
        bridge = {"ok": False, "message": "投屏桥接模块不可用"}

    extensions_root = ""
    try:
        from web_capture.plugin_market import software_extensions_root

        extensions_root = str(software_extensions_root())
    except Exception:
        pass

    java_ok = False
    java_path = ""
    java_hint = ""
    try:
        from mobile_emulator_sdk_bundles import _java_required_message, _resolve_java_exe

        java_path = _resolve_java_exe() or ""
        java_ok = bool(java_path)
        if not java_ok:
            java_hint = _java_required_message()
    except Exception as exc:
        java_hint = str(exc)

    checks: List[Dict[str, Any]] = []
    blocking: List[str] = []

    def _add(
        cid: str,
        label: str,
        ok: bool,
        detail: str,
        *,
        action: str = "",
        optional: bool = False,
    ) -> None:
        checks.append({
            "id": cid,
            "ok": bool(ok),
            "label": label,
            "detail": (detail or "").strip(),
            "action": action,
            "optional": bool(optional),
        })
        if not ok and not optional:
            blocking.append((detail or label).strip())

    enabled = mobile_enabled()
    _add(
        "mobile_module",
        "移动端模块",
        enabled,
        "已启用" if enabled else "未启用（请在 .env 设置 ENABLE_MOBILE=1）",
        action="" if enabled else "docs",
    )

    sdk_ok = bool(status.get("sdk_ready"))
    _add(
        "sdk",
        "SDK 组件",
        sdk_ok,
        status.get("android_sdk_home") or (
            "未安装，请前往插件市场安装「Android 模拟器 SDK（命令行）」"
            if not sdk_ok
            else "就绪"
        ),
        action="" if sdk_ok else "plugin_market",
    )

    avd_ok = bool(status.get("avd_ready"))
    _add(
        "avd",
        "虚拟手机 (AVD)",
        avd_ok,
        (
            f"已就绪（默认：{status.get('default_avd') or '—'}）"
            if avd_ok
            else "未创建虚拟手机，启动模拟器时将自动创建"
        ),
        action="" if avd_ok else "launch",
    )

    _add(
        "java",
        "Java 运行环境",
        java_ok,
        java_path or java_hint or "未检测到 Java 11+（可安装 runtime/jre 或 adoptium.net）",
        action="" if java_ok else "install_java",
    )

    if os.name == "nt":
        hv_ok = status.get("hypervisor_ok")
        hv_detail = "已启用" if hv_ok else (status.get("setup_hint") or "Hypervisor 未就绪")
        _add(
            "hypervisor",
            "硬件加速 (Hypervisor)",
            bool(hv_ok) if hv_ok is not None else True,
            hv_detail,
            action="" if hv_ok else "hypervisor_install",
        )

    bridge_ok = bool(bridge.get("ok") or bridge.get("scrcpy_server_ready"))
    _add(
        "bridge",
        "scrcpy 投屏桥",
        bridge_ok,
        bridge.get("message") or ("就绪" if bridge_ok else "未就绪（可选：插件市场安装 scrcpy）"),
        action="" if bridge_ok else "plugin_market",
        optional=True,
    )

    emu_ok = bool(status.get("emulator_available"))
    if sdk_ok:
        _add(
            "emulator",
            "模拟器程序",
            emu_ok,
            status.get("emulator_message") or ("就绪" if emu_ok else "未找到 emulator"),
            action="" if emu_ok else "plugin_market",
        )

    blocking_reason = blocking[0] if blocking else ""
    setup_hint = (status.get("setup_hint") or "").strip()
    if not blocking_reason and setup_hint and status.get("hypervisor_ok") is False:
        blocking_reason = setup_hint.split("\n")[0]

    return {
        "checks": checks,
        "ready": not bool(blocking),
        "blocking_reasons": blocking,
        "blocking_reason": blocking_reason,
        "extensions_root": extensions_root,
        "java_ok": java_ok,
        "java_path": java_path,
        **status,
        "bridge": bridge,
    }
