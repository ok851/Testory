# -*- coding: utf-8 -*-
"""启动 Flask 时自动补齐桌面 .env 并可选拉起 desktop_automation_gateway。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from desktop_env_config import (
    deployment_profile,
    desktop_auto_start_gateway,
    desktop_execution_mode,
    is_local_deployment,
)

_BOOTED = False
_GATEWAY_PROC: Optional[subprocess.Popen] = None

_ROOT = Path(__file__).resolve().parent


def _port_listening(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_local_profile_defaults() -> None:
    if not os.environ.get("DEPLOYMENT_PROFILE", "").strip():
        os.environ["DEPLOYMENT_PROFILE"] = "local"
    if not os.environ.get("DESKTOP_EXECUTION_MODE", "").strip():
        os.environ["DESKTOP_EXECUTION_MODE"] = "inprocess"


def _ensure_desktop_env_defaults() -> None:
    """未配置时写入进程内默认值（enterprise / gateway 模式）。"""
    if is_local_deployment() and desktop_execution_mode() == "inprocess":
        return
    if not os.environ.get("DESKTOP_AGENT_GATEWAY_URL", "").strip():
        port = os.environ.get("DESKTOP_AGENT_GATE_PORT", "8766")
        os.environ["DESKTOP_AGENT_GATEWAY_URL"] = f"http://127.0.0.1:{port}"
    if not os.environ.get("DESKTOP_AGENT_GATEWAY_SECRET", "").strip():
        emb = (os.environ.get("EMBEDDED_BROWSER_GATEWAY_SECRET") or "").strip()
        os.environ["DESKTOP_AGENT_GATEWAY_SECRET"] = emb or "hufirst-desktop-local"
    if sys.platform == "win32" and not os.environ.get("DESKTOP_APP_ALIASES", "").strip():
        os.environ["DESKTOP_APP_ALIASES"] = '{"default":"notepad.exe"}'
    if sys.platform == "win32" and not os.environ.get("DESKTOP_DEFAULT_ATTACH_TITLE_RE", "").strip():
        os.environ["DESKTOP_DEFAULT_ATTACH_TITLE_RE"] = ".*记事本.*|.*Notepad.*"


def _start_gateway_process() -> None:
    global _GATEWAY_PROC
    if _GATEWAY_PROC is not None and _GATEWAY_PROC.poll() is None:
        return
    env = os.environ.copy()
    env["DESKTOP_GATEWAY_INPROCESS"] = "1"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    _GATEWAY_PROC = subprocess.Popen(
        [sys.executable, "-m", "desktop_automation_gateway"],
        cwd=str(_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def bootstrap_desktop_services(*, force: bool = False) -> dict:
    """
    在 app 加载 .env 后调用一次。
    返回 {"gateway_started": bool, "gateway_url": str, ...}
    """
    global _BOOTED
    if _BOOTED and not force:
        return {"skipped": True}
    _BOOTED = True

    if sys.platform != "win32":
        return {"skipped": True, "reason": "non-windows"}

    try:
        from desktop_app_catalog import ensure_catalog_built_async

        ensure_catalog_built_async()
    except Exception:
        pass

    _ensure_local_profile_defaults()
    out = {
        "deployment_profile": deployment_profile(),
        "desktop_execution_mode": desktop_execution_mode(),
        "gateway_started": False,
        "gateway_url": os.environ.get("DESKTOP_AGENT_GATEWAY_URL", ""),
    }

    if is_local_deployment() and desktop_execution_mode() == "inprocess":
        out["skipped_gateway"] = True
        try:
            from desktop_app_catalog import ensure_catalog_built_async

            ensure_catalog_built_async()
            out["catalog_scan_scheduled"] = True
        except Exception:
            pass
        return out

    _ensure_desktop_env_defaults()
    out["gateway_url"] = os.environ.get("DESKTOP_AGENT_GATEWAY_URL", "")

    if not desktop_auto_start_gateway():
        return out

    parsed = urlparse(out["gateway_url"])
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8766
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
