# -*- coding: utf-8 -*-
"""Windows 下启动子进程时隐藏控制台窗口（供网关自动拉起等）。"""

from __future__ import annotations

import subprocess
import sys


def subprocess_creationflags_no_window() -> int:
    """供 subprocess.Popen(..., creationflags=...) 使用；非 Windows 返回 0。"""
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0x08000000)
