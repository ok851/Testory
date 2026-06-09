# -*- coding: utf-8 -*-
"""启动 Flask 时可选自动拉起 embedded_browser_gateway（AI 测试画布 / CDP）。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from embedded_browser_client import embedded_gateway_config, embedded_gateway_enabled
from subprocess_win import subprocess_creationflags_no_window

_BOOTED = False
_GATEWAY_PROC: Optional[subprocess.Popen] = None

try:
    from install_paths import helper_executable, resolve_install_root

    _ROOT = resolve_install_root()
except ImportError:
    _ROOT = Path(__file__).resolve().parent


def _embedded_gateway_cmd() -> list:
    for folder, mod in (
        ("TestoryBrowserRuntime", "browser_runtime"),
        ("TestoryEmbeddedGw", "embedded_browser_gateway"),
    ):
        try:
            exe = helper_executable(folder)
            if exe is not None and exe.is_file():
                return [str(exe)]
        except NameError:
            pass
        try:
            import importlib.util

            if importlib.util.find_spec(mod):
                return [sys.executable, "-m", mod]
        except ImportError:
            continue
    return [sys.executable, "-m", "browser_runtime"]


def _truthy(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def embedded_auto_start_gateway() -> bool:
    """本机 local 且已配置网关 URL/Secret 时默认自动启动；可用 EMBEDDED_BROWSER_AUTO_START_GATEWAY=0 关闭。"""
    if not embedded_gateway_enabled():
        return False
    profile = (os.environ.get("DEPLOYMENT_PROFILE") or "local").strip().lower()
    if profile in ("docker", "enterprise"):
        return _truthy("EMBEDDED_BROWSER_AUTO_START_GATEWAY", "0")
    return _truthy("EMBEDDED_BROWSER_AUTO_START_GATEWAY", "1")


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
        return open(log_dir / "embedded_gateway.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        return subprocess.DEVNULL


def _start_gateway_process() -> None:
    global _GATEWAY_PROC
    if _GATEWAY_PROC is not None and _GATEWAY_PROC.poll() is None:
        return
    env = os.environ.copy()
    log_fp = _gateway_log_handle()
    _GATEWAY_PROC = subprocess.Popen(
        _embedded_gateway_cmd(),
        cwd=str(_ROOT),
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=subprocess_creationflags_no_window(),
    )


def stop_embedded_gateway() -> None:
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


def bootstrap_embedded_browser_services(*, force: bool = False) -> dict:
    """
    在 app 加载 .env 后调用一次。
    返回 {"gateway_started": bool, "gateway_url": str, ...}
    """
    global _BOOTED
    if _BOOTED and not force:
        return {"skipped": True}
    _BOOTED = True

    base, _, _ = embedded_gateway_config()
    out: dict = {
        "gateway_url": base,
        "gateway_configured": embedded_gateway_enabled(),
        "gateway_started": False,
    }
    if not embedded_gateway_enabled():
        out["skipped"] = True
        out["reason"] = "not_configured"
        return out
    if not embedded_auto_start_gateway():
        out["skipped"] = True
        out["reason"] = "auto_start_disabled"
        return out

    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or int(os.environ.get("EMBEDDED_BROWSER_GATE_PORT", "8765") or 8765)
    if _port_listening(host, port):
        out["gateway_started"] = True
        out["already_running"] = True
        return out

    try:
        _start_gateway_process()
        for _ in range(40):
            if _port_listening(host, port):
                out["gateway_started"] = True
                break
            time.sleep(0.25)
    except Exception as e:
        out["error"] = str(e)
    return out
