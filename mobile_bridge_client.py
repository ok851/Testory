# -*- coding: utf-8 -*-
"""Testory Android bridge_daemon 客户端（Hermes skill 脚本封装）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

_BRIDGE_ACTIONS = frozenset(
    {"dump", "tap", "tap_bounds", "tap_coords", "find", "scroll", "type", "wait", "screenshot", "quit"}
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def bridge_daemon_script_paths() -> List[Path]:
    """按优先级返回 bridge_daemon.py 路径。"""
    paths: List[Path] = []
    custom = (os.environ.get("TESTORY_BRIDGE_DAEMON") or "").strip()
    if custom:
        paths.append(Path(custom))
    try:
        from hermes_config import hermes_skills_dir

        paths.append(hermes_skills_dir() / "testory-android-mobile" / "scripts" / "bridge_daemon.py")
    except ImportError:
        pass
    paths.append(_project_root() / "skills" / "bundled" / "testory-android-mobile" / "scripts" / "bridge_daemon.py")
    return paths


def resolve_bridge_daemon_script() -> Optional[Path]:
    for p in bridge_daemon_script_paths():
        if p.is_file():
            return p
    return None


def bridge_conflicts_with_executor() -> bool:
    """MobileExecutor 已连接 Appium 时不宜并行 bridge daemon。"""
    try:
        from mobile_executor import get_mobile_executor

        ex = get_mobile_executor()
        if ex is not None and ex.is_connected:
            return True
    except Exception:
        pass
    return False


def bridge_command(action: str, args: Optional[Dict[str, Any]] = None, *, timeout_sec: float = 90.0) -> Dict[str, Any]:
    """
    调用 bridge_daemon.py 单次命令，返回解析后的 JSON dict。
    """
    action = (action or "").strip().lower()
    if action not in _BRIDGE_ACTIONS:
        return {"ok": False, "error": f"unsupported bridge action: {action}"}

    if bridge_conflicts_with_executor() and action != "quit":
        return {
            "ok": False,
            "error": "MobileExecutor Appium 会话已占用；请先 disconnect 再使用 bridge",
            "code": "bridge_conflict",
        }

    script = resolve_bridge_daemon_script()
    if script is None:
        return {"ok": False, "error": "bridge_daemon.py 未找到"}

    cmd = [sys.executable, str(script), action]
    if args:
        cmd.append(json.dumps(args, ensure_ascii=False))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(5.0, float(timeout_sec)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "bridge daemon timeout"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if not stdout:
        return {"ok": False, "error": stderr or f"bridge exit {proc.returncode}"}
    try:
        data = json.loads(stdout)
        return data if isinstance(data, dict) else {"ok": False, "error": "invalid json response"}
    except json.JSONDecodeError:
        return {"ok": False, "error": stdout[:500], "stderr": stderr[:300]}
