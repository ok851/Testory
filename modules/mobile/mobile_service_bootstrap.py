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

from modules.core.subprocess_win import subprocess_creationflags_no_window

try:
    from modules.core.install_paths import helper_executable, resolve_install_root

    _ROOT = resolve_install_root()
except ImportError:
    helper_executable = None  # type: ignore
    _ROOT = Path(__file__).resolve().parents[2]

_BOOTED = False
_GATEWAY_PROC: Optional[subprocess.Popen] = None
_EXPECTED_GATEWAY_BUILD = "20260616-no-auto-install"


def mobile_auto_start_gateway() -> bool:
    raw = (os.environ.get("MOBILE_AUTO_START_GATEWAY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _mobile_gateway_cmd() -> list:
    # 开发态始终用当前源码启动 Gateway，避免 runtime/TestoryMobileGw.exe 旧包自动装 APK
    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "mobile_automation_gateway"]
    try:
        if helper_executable is None:
            raise NameError
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


def _kill_listeners_on_port(port: int) -> bool:
    """终止占用 Gateway 端口的旧进程；返回端口是否已空闲。"""
    if sys.platform != "win32":
        return True
    freed = False
    for attempt in range(3):
        try:
            proc = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            pids = set()
            needle = f":{port}"
            for line in (proc.stdout or "").splitlines():
                if "LISTENING" not in line.upper() or needle not in line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                try:
                    pids.add(int(parts[-1]))
                except ValueError:
                    continue
            if not pids:
                return True
            my_pid = os.getpid()
            for pid in pids:
                if pid <= 0 or pid == my_pid:
                    continue
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                time.sleep(0.3)
            time.sleep(0.5)
        except Exception:
            pass
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
    env["MOBILE_GATEWAY_BUILD"] = _EXPECTED_GATEWAY_BUILD
    env["MOBILE_AGENT_GATE_PORT"] = os.environ.get("MOBILE_AGENT_GATE_PORT", "8777")
    env["MOBILE_AGENT_GATEWAY_SECRET"] = os.environ.get(
        "MOBILE_AGENT_GATEWAY_SECRET",
        os.environ.get("EMBEDDED_BROWSER_GATEWAY_SECRET", "hufirst-mobile-local"),
    )
    _GATEWAY_PROC = subprocess.Popen(
        _mobile_gateway_cmd(),
        cwd=str(_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess_creationflags_no_window(),
    )


def stop_mobile_gateway() -> None:
    global _GATEWAY_PROC
    if _GATEWAY_PROC is not None:
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


def ensure_mobile_gateway_ready(*, force_restart: bool = False) -> dict:
    """连接设备前调用：Gateway 已健康则直接返回；否则自动拉起。"""
    _ensure_mobile_env_defaults()
    url = (os.environ.get("MOBILE_AGENT_GATEWAY_URL") or "http://127.0.0.1:8777").rstrip("/")
    out: dict = {"ok": False, "gateway_url": url, "started": False}
    if not force_restart and _verify_gateway_health(url):
        out["ok"] = True
        return out
    boot = bootstrap_mobile_services(force=True)
    out.update({k: v for k, v in boot.items() if k != "skipped"})
    if boot.get("gateway_started") or _verify_gateway_health(url):
        out["ok"] = True
        out["started"] = bool(boot.get("gateway_started"))
        return out
    out["error"] = boot.get("error") or boot.get("gateway_error") or "Gateway 未能启动"
    return out


def bootstrap_mobile_services(*, force: bool = False) -> dict:
    """在 app 加载 .env 后调用一次。"""
    global _BOOTED
    from modules.mobile.mobile_env_config import mobile_enabled

    if not mobile_enabled():
        return {"skipped": True, "reason": "mobile_disabled"}
    if _BOOTED and not force:
        # 已引导过：若进程仍存活且健康，跳过；否则强制重启
        url = (os.environ.get("MOBILE_AGENT_GATEWAY_URL") or "").strip()
        proc_alive = _GATEWAY_PROC is not None and _GATEWAY_PROC.poll() is None
        if proc_alive and url and _verify_gateway_health(url):
            return {"skipped": True, "gateway_started": False, "gateway_url": url}
        force = True

    _ensure_mobile_env_defaults()
    out = {
        "gateway_started": False,
        "gateway_url": os.environ.get("MOBILE_AGENT_GATEWAY_URL", ""),
    }
    if not mobile_auto_start_gateway():
        _BOOTED = True
        return out

    parsed = urlparse(out["gateway_url"])
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8777

    stop_mobile_gateway()
    port_freed = _kill_listeners_on_port(port)
    if not port_freed:
        out["gateway_error"] = f"端口 {port} 被占用，无法释放。请手动执行: netstat -ano | findstr :{port}"
        out["port_blocked"] = True
        _BOOTED = True
        return out

    try:
        _start_gateway_process()
        for _ in range(40):
            if _port_listening(host, port):
                time.sleep(0.3)
                if _verify_gateway_health(out["gateway_url"]):
                    out["gateway_started"] = True
                    break
                else:
                    out["error"] = "Gateway 端口已监听但 /health 校验失败，可能非 Testory 服务"
                    _gateway_stderr_snippet(out)
                    break
            time.sleep(0.2)
        if not out["gateway_started"] and "error" not in out:
            out["error"] = "Gateway 进程未能在 8 秒内启动"
            _gateway_stderr_snippet(out)
        if out["gateway_started"]:
            _verify_gateway_auth(out)
    except Exception as e:
        out["error"] = str(e)
        _gateway_stderr_snippet(out)
    _BOOTED = True
    return out


def _verify_gateway_auth(out: dict) -> None:
    secret = os.environ.get("MOBILE_AGENT_GATEWAY_SECRET", "").strip()
    url = out["gateway_url"].rstrip("/") + "/internal/devices/scan"
    import json as _json
    import urllib.request as _ur

    try:
        data = _json.dumps({}).encode("utf-8")
        req = _ur.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Mobile-Agent-Secret": secret,
            },
            method="POST",
        )
        with _ur.urlopen(req, timeout=5.0) as resp:
            body = _json.loads(resp.read().decode("utf-8", errors="replace"))
            if body.get("success"):
                return
            out["auth_warning"] = "Gateway 鉴权响应异常"
    except _ur.HTTPError as e:
        if e.code == 401:
            out["auth_warning"] = (
                f"Gateway 鉴权失败 (secret={secret[:6]}...), "
                "请检查 MOBILE_AGENT_GATEWAY_SECRET 环境变量"
            )
        else:
            out["auth_warning"] = f"Gateway 返回 HTTP {e.code}"
    except Exception as e:
        out["auth_warning"] = f"Gateway 鉴权测试失败: {e}"


def _verify_gateway_health(gateway_url: str) -> bool:
    """通过 /health 端点验证 Gateway 是真正的 Testory M.A.G. 而非其他占用端口的服务。"""
    import json as _json
    import urllib.request as _ur

    try:
        req = _ur.Request(
            gateway_url.rstrip("/") + "/health",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with _ur.urlopen(req, timeout=3.0) as resp:
            body = _json.loads(resp.read().decode("utf-8", errors="replace"))
            return body.get("service") == "mobile-agent-gateway"
    except Exception:
        return False


def _gateway_stderr_snippet(out: dict) -> None:
    global _GATEWAY_PROC
    if _GATEWAY_PROC is None or _GATEWAY_PROC.stderr is None:
        return
    try:
        import select
        if sys.platform == "win32":
            ready, _, _ = select.select([_GATEWAY_PROC.stderr], [], [], 0.1)
            if ready:
                line = _GATEWAY_PROC.stderr.readline()
                if line:
                    out["gateway_stderr"] = line.decode("utf-8", errors="replace").strip()[:400]
        else:
            import fcntl
            fd = _GATEWAY_PROC.stderr.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            line = _GATEWAY_PROC.stderr.readline()
            if line:
                out["gateway_stderr"] = line.decode("utf-8", errors="replace").strip()[:400]
    except Exception:
        pass

