# -*- coding: utf-8 -*-
"""启动 Flask 时可选自动拉起 Hermes Gateway（内嵌 AI Agent）。"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from hermes_config import ensure_hermes_home
from hermes_gateway_client import HermesGatewayClient
from subprocess_win import subprocess_creationflags_no_window

_BOOTED = False
_GATEWAY_PROC: Optional[subprocess.Popen] = None

try:
    from install_paths import helper_executable, resolve_install_root

    _ROOT = resolve_install_root()
except ImportError:
    _ROOT = Path(__file__).resolve().parent


def _truthy(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def hermes_gateway_enabled() -> bool:
    return HermesGatewayClient().is_configured()


def hermes_auto_start_gateway() -> bool:
    if not hermes_gateway_enabled():
        return False
    profile = (os.environ.get("DEPLOYMENT_PROFILE") or "local").strip().lower()
    if profile in ("docker", "enterprise"):
        return _truthy("HERMES_AUTO_START_GATEWAY", "0")
    return _truthy("HERMES_AUTO_START_GATEWAY", "1")


def _hermes_gateway_cmd() -> list:
    try:
        exe = helper_executable("TestoryHermesGw")
        if exe is not None and exe.is_file():
            return [str(exe)]
    except NameError:
        pass
    scripts_dir = Path(sys.executable).resolve().parent
    for name in ("hermes.exe", "hermes"):
        candidate = scripts_dir / name
        if candidate.is_file():
            return [str(candidate), "gateway"]
    import shutil

    hermes_bin = shutil.which("hermes")
    if hermes_bin:
        return [hermes_bin, "gateway"]
    return [
        sys.executable,
        "-c",
        "import sys; sys.argv=['hermes','gateway']; from hermes_cli.main import main; main()",
    ]


def _port_listening(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _gateway_log_handle():
    try:
        base = (os.environ.get("UAT_DATA_DIR") or "").strip()
        if not base:
            base = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Testory")
        log_dir = Path(base) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return open(log_dir / "hermes_gateway.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        return subprocess.DEVNULL


def _inject_hermes_env(env: dict) -> None:
    home = ensure_hermes_home()
    env["HERMES_HOME"] = str(home.resolve())
    env_path = home / ".env"
    if env_path.is_file():
        try:
            from dotenv import dotenv_values

            for k, v in dotenv_values(env_path).items():
                if k and v is not None and k not in env:
                    env[k] = str(v)
        except ImportError:
            pass
    if not env.get("HERMES_API_SERVER_KEY"):
        env["HERMES_API_SERVER_KEY"] = env.get("API_SERVER_KEY", "")
    if not env.get("HERMES_GATEWAY_URL"):
        env["HERMES_GATEWAY_URL"] = "http://127.0.0.1:8642"


def _start_gateway_process() -> None:
    global _GATEWAY_PROC
    if _GATEWAY_PROC is not None and _GATEWAY_PROC.poll() is None:
        return
    env = os.environ.copy()
    _inject_hermes_env(env)
    log_fp = _gateway_log_handle()
    _GATEWAY_PROC = subprocess.Popen(
        _hermes_gateway_cmd(),
        cwd=str(_ROOT),
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=subprocess_creationflags_no_window(),
    )


def bootstrap_hermes_services(*, force: bool = False) -> dict:
    global _BOOTED
    if _BOOTED and not force:
        return {"skipped": True}
    _BOOTED = True

    client = HermesGatewayClient()
    out: dict = {
        "hermes_url": client.base_url,
        "hermes_configured": client.is_configured(),
        "hermes_started": False,
    }
    if not client.is_configured():
        out["skipped"] = True
        out["reason"] = "not_configured"
        return out
    if not hermes_auto_start_gateway():
        out["skipped"] = True
        out["reason"] = "auto_start_disabled"
        return out

    ensure_hermes_home()
    parsed = urlparse(client.base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8642
    if client.health_check():
        out["hermes_started"] = True
        out["already_running"] = True
        return out

    try:
        _start_gateway_process()
        for _ in range(60):
            if client.health_check(timeout_sec=1.0):
                out["hermes_started"] = True
                break
            time.sleep(0.5)
    except Exception as e:
        out["error"] = str(e)
    return out
