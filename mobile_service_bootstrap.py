# -*- coding: utf-8 -*-
"""启动 Flask / 桌面壳时自动拉起 mobile_automation_gateway。"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from subprocess_win import subprocess_creationflags_no_window

_BOOTED = False
_GATEWAY_PROC: Optional[subprocess.Popen] = None

try:
    from install_paths import helper_executable, resolve_install_root

    _ROOT = resolve_install_root()
except ImportError:
    _ROOT = Path(__file__).resolve().parent


def mobile_auto_start_gateway() -> bool:
    raw = (os.environ.get("MOBILE_AUTO_START_GATEWAY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _mobile_gateway_cmd() -> list:
    try:
        exe = helper_executable("TestoryMobileGw")
        if exe is not None and exe.is_file():
            return [str(exe)]
    except NameError:
        pass
    return [sys.executable, "-m", "mobile_automation_gateway"]


def _port_listening(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_mobile_env_defaults() -> None:
    if not os.environ.get("MOBILE_AGENT_GATEWAY_URL", "").strip():
        port = os.environ.get("MOBILE_AGENT_GATE_PORT", "8777")
        os.environ["MOBILE_AGENT_GATEWAY_URL"] = f"http://127.0.0.1:{port}"
    if not os.environ.get("MOBILE_AGENT_GATEWAY_SECRET", "").strip():
        emb = (os.environ.get("EMBEDDED_BROWSER_GATEWAY_SECRET") or "").strip()
        os.environ["MOBILE_AGENT_GATEWAY_SECRET"] = emb or "hufirst-mobile-local"
    if not os.environ.get("MOBILE_DRIVER", "").strip():
        os.environ["MOBILE_DRIVER"] = "plugin"


def _start_gateway_process() -> None:
    global _GATEWAY_PROC
    if _GATEWAY_PROC is not None and _GATEWAY_PROC.poll() is None:
        return
    env = os.environ.copy()
    env["MOBILE_GATEWAY_INPROCESS"] = "1"
    _GATEWAY_PROC = subprocess.Popen(
        _mobile_gateway_cmd(),
        cwd=str(_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess_creationflags_no_window(),
    )


def stop_mobile_gateway() -> None:
    global _GATEWAY_PROC, _BOOTED
    if _GATEWAY_PROC is None:
        return
    try:
        if _GATEWAY_PROC.poll() is None:
            _GATEWAY_PROC.terminate()
            try:
                _GATEWAY_PROC.wait(timeout=8.0)
            except subprocess.TimeoutExpired:
                _GATEWAY_PROC.kill()
    except Exception:
        pass
    _GATEWAY_PROC = None
    _BOOTED = False


def bootstrap_mobile_services(*, force: bool = False) -> dict:
    """在 app 加载 .env 后调用一次。"""
    global _BOOTED
    from mobile_env_config import mobile_enabled

    if not mobile_enabled():
        return {"skipped": True, "reason": "mobile_disabled"}
    if _BOOTED and not force:
        return {"skipped": True}
    _BOOTED = True

    _ensure_mobile_env_defaults()
    out = {
        "gateway_started": False,
        "gateway_url": os.environ.get("MOBILE_AGENT_GATEWAY_URL", ""),
    }
    if not mobile_auto_start_gateway():
        return out

    parsed = urlparse(out["gateway_url"])
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8777
    if _port_listening(host, port):
        out["gateway_started"] = True
        out["already_running"] = True
        return out

    try:
        _start_gateway_process()
        for _ in range(30):
            if _port_listening(host, port):
                out["gateway_started"] = True
                break
            time.sleep(0.2)
    except Exception as e:
        out["error"] = str(e)
    return out
