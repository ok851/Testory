# -*- coding: utf-8 -*-
"""
本机混排/桌面执行互斥锁（文件锁）。

同一 Windows 机器同一时间仅允许一个用例/调度/数据驱动任务占用自动化资源，
避免 Playwright 与 pywinauto 争抢焦点。
"""

from __future__ import annotations

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
_LOCK_FILE_NAME = ".uat_execution.lock"
_DEFAULT_TIMEOUT_SEC = 120.0
_STALE_SEC = 6 * 3600.0


class ExecutionLockError(RuntimeError):
    """无法获取本机执行锁。"""


def lock_file_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("UAT_DATA_DIR") or (root / _LOCK_DIR_NAME))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _LOCK_FILE_NAME


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


def _try_acquire_file(fd: int) -> bool:
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


def _release_file(fd: int) -> None:
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


class LocalExecutionLock:
    """进程内单例：持有文件描述符直至 release。"""

    def __init__(self) -> None:
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
                time.sleep(0.25)

        path = lock_file_path()
        deadline = time.time() + max(0.0, timeout_sec)
        owner_label = owner or f"pid:{os.getpid()}"

        while True:
            if path.exists() and _is_stale(path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

            fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
            if _try_acquire_file(fd):
                meta = {
                    "owner": owner_label,
                    "pid": os.getpid(),
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
                uat_logger.info("🔒 [UAT_LOCK] 已获取本机执行锁 owner=%s", owner_label)
                return True

            os.close(fd)
            if not blocking:
                return False
            if time.time() >= deadline:
                return False
            time.sleep(0.25)

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
            _release_file(fd)
            try:
                os.close(fd)
            except OSError:
                pass
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        uat_logger.info("🔓 [UAT_LOCK] 已释放本机执行锁 owner=%s", owner)

    def force_release(self) -> bool:
        """管理员强制清理锁文件（不保证释放其他进程已持有的内核锁）。"""
        self.release()
        path = lock_file_path()
        try:
            if path.exists():
                path.unlink()
            return True
        except OSError:
            return False


_lock_singleton = LocalExecutionLock()


def acquire(
    *,
    blocking: bool = True,
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    owner: str = "",
) -> bool:
    return _lock_singleton.acquire(
        blocking=blocking, timeout_sec=timeout_sec, owner=owner
    )


def release() -> None:
    _lock_singleton.release()


def force_release() -> bool:
    return _lock_singleton.force_release()


def is_held() -> bool:
    return _lock_singleton.is_held()


@contextmanager
def execution_guard(
    *,
    owner: str = "",
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    required: bool = True,
) -> Iterator[bool]:
    """
    上下文管理器。required=True 且获取失败时抛出 ExecutionLockError。
    返回是否成功获取（required=False 时失败不抛错）。
    """
    ok = acquire(blocking=True, timeout_sec=timeout_sec, owner=owner)
    if not ok and required:
        raise ExecutionLockError(
            "本机已有自动化任务在执行（Playwright/桌面），请稍后再试。"
        )
    try:
        yield ok
    finally:
        if ok:
            release()
