# -*- coding: utf-8 -*-
"""桌面版单实例锁，避免重复启动占用端口。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def _lock_path() -> Path:
    base = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if not base:
        base = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Testory")
    return Path(base) / "instance.lock"


def acquire_instance_lock() -> Optional[object]:
    """成功返回锁文件句柄（进程退出时自动释放）；已被占用则返回 None。"""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import msvcrt

        try:
            fp = open(path, "a+b")
            fp.seek(0)
            fp.write(b"\0")
            fp.flush()
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
            fp.seek(0)
            fp.truncate()
            fp.write(str(os.getpid()).encode("ascii"))
            fp.flush()
            return fp
        except OSError:
            try:
                fp.close()
            except Exception:
                pass
            return None
    try:
        import fcntl

        fp = open(path, "w", encoding="utf-8")
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fp.write(str(os.getpid()))
        fp.flush()
        return fp
    except OSError:
        return None


def instance_lock_message() -> str:
    return (
        "Testory 已在运行中。\n\n"
        "请关闭其他 Testory 窗口后再试，或在任务管理器中结束 TestoryBackend / Testory 进程。"
    )
