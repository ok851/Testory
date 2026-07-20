# -*- coding: utf-8 -*-
"""启动 Flask 时自动补齐桌面 .env 并可选拉起 desktop_automation_gateway。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from desktop_env_config import (
    deployment_profile,
    desktop_auto_start_gateway,
    desktop_execution_mode,
    is_local_deployment,
)
from subprocess_win import subprocess_creationflags_no_window

_BOOTED = False
_GATEWAY_PROC: Optional[subprocess.Popen] = None

try:
    from install_paths import helper_executable, resolve_install_root

    _ROOT = resolve_install_root()
except ImportError:
    _ROOT = Path(__file__).resolve().parent


def _desktop_gateway_cmd() -> list:
    try:
        exe = helper_executable("TestoryDesktopGw")
        if exe is not None and exe.is_file():
            return [str(exe)]
    except NameError:
        pass
    return [sys.executable, "-m", "desktop_automation_gateway"]


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


def _ensure_desktop_env_defaults(*, force: bool = False) -> None:
    """未配置时写入进程内默认值。

    force=True：即使 local+inprocess 也写入 URL/SECRET，供 Hermes 经 gateway 操控桌面。
    """
    if (
        not force
        and is_local_deployment()
        and desktop_execution_mode() == "inprocess"
    ):
        # 仍补齐 secret/url，避免 Hermes 侧空鉴权；但不强制改执行模式
        pass
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


def ensure_desktop_gateway_for_agent() -> dict:
    """为 Hermes/智能体保证桌面 gateway 可调用（补 env + 必要时拉起进程）。"""
    out: dict = {"ok": False, "gateway_url": "", "started": False}
    if sys.platform != "win32":
        out["reason"] = "non-windows"
        return out
    _ensure_desktop_env_defaults(force=True)
    url = os.environ.get("DESKTOP_AGENT_GATEWAY_URL", "").strip()
    out["gateway_url"] = url
    parsed = urlparse(url or "http://127.0.0.1:8766")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or int(os.environ.get("DESKTOP_AGENT_GATE_PORT", "8766") or "8766")

    def _auth_probe() -> Tuple[bool, str]:
        try:
            from desktop_agent_client import desktop_agent_json

            payload, err = desktop_agent_json(
                "POST", "/internal/session", body={}, timeout_sec=2.0
            )
            if err and "401" in str(err).lower():
                return False, "unauthorized"
            if err and payload is None:
                # 无 /internal 可达或连接失败
                return False, str(err)[:80]
            return True, "ok"
        except Exception as e:
            return False, str(e)[:80]

    if _port_listening(host, port):
        ok_auth, auth_detail = _auth_probe()
        if ok_auth:
            out["ok"] = True
            out["already_running"] = True
            return out
        # 端口在听但密钥不一致：停掉本进程拉起的旧 gateway 再重启
        out["auth_mismatch"] = auth_detail
        try:
            stop_desktop_gateway()
        except Exception:
            pass
        time.sleep(0.3)

    try:
        _start_gateway_process()
        for _ in range(40):
            if _port_listening(host, port):
                ok_auth, auth_detail = _auth_probe()
                if ok_auth:
                    out["ok"] = True
                    out["started"] = True
                    return out
                out["auth_mismatch"] = auth_detail
                break
            time.sleep(0.15)
        if not out.get("ok"):
            out["error"] = out.get("auth_mismatch") or "gateway_start_timeout"
    except Exception as e:
        out["error"] = str(e)[:160]
    return out


def _start_gateway_process() -> None:
    global _GATEWAY_PROC
    if _GATEWAY_PROC is not None and _GATEWAY_PROC.poll() is None:
        return
    env = os.environ.copy()
    env["DESKTOP_GATEWAY_INPROCESS"] = "1"
    _GATEWAY_PROC = subprocess.Popen(
        _desktop_gateway_cmd(),
        cwd=str(_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess_creationflags_no_window(),
    )


def stop_desktop_gateway() -> None:
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
