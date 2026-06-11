# -*- coding: utf-8 -*-
"""Android 设备 logcat 采集（运行失败诊断）。"""

from __future__ import annotations

import subprocess
from typing import Optional

from mobile_env_config import adb_path


def capture_logcat(udid: str = "", *, max_lines: int = 200) -> str:
    """抓取最近 logcat 文本（默认 200 行）。"""
    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    cmd.extend(["logcat", "-d", "-t", str(max(20, min(2000, int(max_lines))))])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
        )
        out = (proc.stdout or "").strip()
        if proc.returncode != 0 and proc.stderr:
            out = (out + "\n" + proc.stderr).strip()
        return out[:50000]
    except Exception as exc:
        return f"logcat capture failed: {exc}"
