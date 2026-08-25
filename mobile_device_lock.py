# -*- coding: utf-8 -*-
"""设备级互斥锁（按设备 serial 分文件锁）。

同一台手机同一时间仅允许一个执行通道操作（PC 遥控 scrcpy/ADB 注入、
手机 APK job 队列、多会话并行），避免同设备双写冲突（R1）。

与 execution_lock（整机锁）构成两层互斥：
- 整机锁：防 Playwright / pywinauto 抢焦点（执行调度层，orchestrator 持有）
- 设备锁：防同设备多通道双写（移动端动作层，本模块）

用法：
    from mobile_device_lock import mobile_device_guard

    with mobile_device_guard("emulator-5554", owner="agent:abc", timeout_sec=60):
        inject_tap(...)

特性：
- 进程内单例注册表：同一 serial 复用同一锁实例（含可重入深度计数）
- 同线程重入直接放行（同一 agent 会话连续操作不阻塞自己）
- 跨线程 / 跨进程互斥（blocking 等待或 non-blocking 立即失败）
- 锁文件带 stale 检测（崩溃残留自动清理）
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

_LOCK_DIR_NAME = "data"
_LOCK_FILE_PREFIX = ".uat_mobile_dev_"
_DEFAULT_TIMEOUT_SEC = 120.0
# 设备动作一般分钟级，1 小时未动的锁文件视为崩溃残留
_STALE_SEC = 3600.0


class MobileDeviceLockError(RuntimeError):
    """无法获取设备级互斥锁。"""


def _lock_file_path(serial: str) -> Path:
    root = Path(__file__).resolve().parent
    data_dir = Path(os.environ.get("UAT_DATA_DIR") or (root / _LOCK_DIR_NAME))
    data_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((serial or "unknown").encode("utf-8")).hexdigest()[:16]
    return data_dir / f"{_LOCK_FILE_PREFIX}{digest}.lock"


def _read_lock_meta(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw) if raw.strip() else None
    except (OSError, json.JSONDecodeError):
        return None


def _is_stale(path: Path) -> bool:
    meta = _read_lock_meta(path)
    if meta and meta.get("acquired_at"):
        try:
            if time.time() - float(meta["acquired_at"]) > _STALE_SEC:
                return True
        except (TypeError, ValueError):
            pass
    try:
        return (time.time() - path.stat().st_mtime) > _STALE_SEC
    except OSError:
        return False


def _try_lock_file(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_file(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class DeviceLock:
    """单设备锁：文件锁 + 线程重入，语义对齐 execution_lock.LocalExecutionLock。"""

    def __init__(self, serial: str) -> None:
        self._serial = serial
        self._fd: Optional[int] = None
        self._path: Optional[Path] = None
        self._owner: str = ""
        self._holder_thread: Optional[int] = None
        self._reentrant_depth: int = 0

    def is_held(self) -> bool:
        return self._fd is not None

    def acquire(
        self,
        *,
        blocking: bool = True,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
        owner: str = "",
    ) -> bool:
        current_tid = threading.get_ident()
        if self.is_held():
            if self._holder_thread == current_tid:
                self._reentrant_depth += 1
                return True
            if not blocking:
                return False
            deadline = time.time() + max(0.0, timeout_sec)
            while self.is_held() and self._holder_thread != current_tid:
                if time.time() >= deadline:
                    return False
                time.sleep(0.2)

        path = _lock_file_path(self._serial)
        deadline = time.time() + max(0.0, timeout_sec)
        owner_label = owner or f"pid:{os.getpid()}"

        while True:
            if path.exists() and _is_stale(path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

            fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
            if _try_lock_file(fd):
                meta = {
                    "owner": owner_label,
                    "pid": os.getpid(),
                    "serial": self._serial,
                    "acquired_at": time.time(),
                }
                try:
                    os.ftruncate(fd, 0)
                    os.write(fd, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
                except OSError:
                    pass
                self._fd = fd
                self._path = path
                self._owner = owner_label
                self._holder_thread = current_tid
                self._reentrant_depth = 1
                uat_logger.info(
                    "🔒 [MOBILE_DEV_LOCK] 已获取设备锁 serial=%s owner=%s",
                    self._serial, owner_label,
                )
                return True

            os.close(fd)
            if not blocking:
                return False
            if time.time() >= deadline:
                return False
            time.sleep(0.2)

    def release(self) -> None:
        if not self.is_held():
            return
        current_tid = threading.get_ident()
        if self._holder_thread == current_tid and self._reentrant_depth > 1:
            self._reentrant_depth -= 1
            return
        fd = self._fd
        path = self._path
        owner = self._owner
        self._fd = None
        self._path = None
        self._owner = ""
        self._holder_thread = None
        self._reentrant_depth = 0
        if fd is not None:
            _unlock_file(fd)
            try:
                os.close(fd)
            except OSError:
                pass
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        uat_logger.info(
            "🔓 [MOBILE_DEV_LOCK] 已释放设备锁 serial=%s owner=%s",
            self._serial, owner,
        )

    def force_release(self) -> bool:
        """管理员强制清理：释放本实例并删除锁文件（不保证其他进程内核锁）。"""
        self.release()
        path = _lock_file_path(self._serial)
        try:
            if path.exists():
                path.unlink()
            return True
        except OSError:
            return False


class _DeviceLockRegistry:
    """按 serial 管理设备锁实例（进程内注册表，线程安全）。"""

    _registry_lock = threading.Lock()
    _instances: Dict[str, DeviceLock] = {}

    @classmethod
    def get(cls, serial: str) -> DeviceLock:
        key = str(serial or "unknown")
        with cls._registry_lock:
            inst = cls._instances.get(key)
            if inst is None:
                inst = cls._instances[key] = DeviceLock(key)
            return inst

    @classmethod
    def all_locks(cls) -> Dict[str, DeviceLock]:
        with cls._registry_lock:
            return dict(cls._instances)


def mobile_lock_acquire(
    serial: str,
    *,
    blocking: bool = True,
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    owner: str = "",
) -> bool:
    """获取指定设备的互斥锁。"""
    return _DeviceLockRegistry.get(serial).acquire(
        blocking=blocking, timeout_sec=timeout_sec, owner=owner
    )


def mobile_lock_release(serial: str) -> None:
    """释放指定设备的互斥锁（未持有则静默）。"""
    _DeviceLockRegistry.get(serial).release()


def mobile_lock_held(serial: str) -> bool:
    """查询指定设备锁是否被当前进程持有。"""
    return _DeviceLockRegistry.get(serial).is_held()


def mobile_lock_force_release(serial: str) -> bool:
    """强制清理指定设备的锁文件（管理员操作，慎用）。"""
    return _DeviceLockRegistry.get(serial).force_release()


def mobile_locks_status() -> Dict[str, Dict[str, Any]]:
    """当前进程内所有设备锁状态（调试/巡检用）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for serial, inst in _DeviceLockRegistry.all_locks().items():
        out[serial] = {
            "held": inst.is_held(),
            "owner": inst._owner,
            "holder_thread": inst._holder_thread,
            "reentrant_depth": inst._reentrant_depth,
        }
    return out


@contextmanager
def mobile_device_guard(
    serial: str,
    *,
    owner: str = "",
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    required: bool = True,
) -> Iterator[bool]:
    """设备级互斥上下文管理器。

    Args:
        serial: 设备标识（adb serial，如 emulator-5554）。
        owner: 锁持有者标识（如 "agent:<session_id>"）。
        timeout_sec: 等待获取锁的超时（秒）。
        required: True 且获取失败时抛 MobileDeviceLockError；False 时失败仅返回 False。

    Yields:
        bool: 是否成功获取锁（required=False 时可能为 False）。
    """
    ok = mobile_lock_acquire(
        serial,
        blocking=True,
        timeout_sec=timeout_sec,
        owner=owner or f"mobile_device_guard:{serial}",
    )
    if not ok and required:
        raise MobileDeviceLockError(
            f"设备 {serial} 正被其他执行通道占用（PC 遥控 / APK job），请稍后再试。"
        )
    try:
        yield ok
    finally:
        if ok:
            mobile_lock_release(serial)
