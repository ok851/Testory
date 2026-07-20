# -*- coding: utf-8 -*-
"""启动 Flask 时可选自动拉起 Hermes Gateway（内嵌 AI Agent）。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from hermes_config import ensure_hermes_home, hermes_home_dir
from hermes_gateway_client import HermesGatewayClient, _norm
from subprocess_win import subprocess_creationflags_no_window

_BOOTED = False
_GATEWAY_PROC: Optional[subprocess.Popen] = None
_STARTING = False
_STOPPING = False
_START_ERROR = ""
_START_FINISHED = False
_START_BEGAN_AT = 0.0  # time.monotonic() when _STARTING became True
_STOP_BEGAN_AT = 0.0  # time.monotonic() when _STOPPING became True
_START_STALE_SEC = 50.0  # status watchdog: force-fail stuck "starting"
_STOP_STALE_SEC = 18.0  # status watchdog: force-clear stuck "stopping"
# 每次 stop / 新一轮 start 递增，用于取消仍在跑的异步启动线程
_LIFECYCLE_EPOCH = 0
_LIFECYCLE_LOCK = threading.RLock()
# 当前已加载进 Hermes 进程的上游模型指纹；与平台 active 不一致时需重启
_LOADED_LLM_FP = ""

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
    """默认不自动启动；需用户在 AI 页点击「启动智能体」，或显式 HERMES_AUTO_START_GATEWAY=1。"""
    if not hermes_gateway_enabled():
        return False
    return _truthy("HERMES_AUTO_START_GATEWAY", "0")


def _hermes_gateway_cmd(*, replace: bool = True) -> list:
    """构建前台 gateway 命令。使用 `gateway run`，避免误走 service start/detach。"""
    try:
        exe = helper_executable("TestoryHermesGw")
        if exe is not None and exe.is_file():
            return [str(exe)]
    except NameError:
        pass
    scripts_dir = Path(sys.executable).resolve().parent
    run_args = ["gateway", "run"]
    if replace:
        run_args.append("--replace")
    for name in ("hermes.exe", "hermes"):
        candidate = scripts_dir / name
        if candidate.is_file():
            return [str(candidate), *run_args]
    import shutil

    hermes_bin = shutil.which("hermes")
    if hermes_bin:
        return [hermes_bin, *run_args]
    return [
        sys.executable,
        "-c",
        "import sys; sys.argv=['hermes','gateway','run','--replace']; from hermes_cli.main import main; main()",
    ]


def _gateway_listen_endpoint() -> tuple[str, int]:
    parsed = urlparse(HermesGatewayClient().base_url)
    return parsed.hostname or "127.0.0.1", parsed.port or 8642


def _port_listening(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pids_listening_on_port(port: int) -> List[int]:
    """返回正在 LISTEN 指定本地端口的 PID 列表。"""
    pids: Set[int] = set()
    if sys.platform == "win32":
        # 优先 netstat（快）；PowerShell 作为兜底且缩短超时
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            needle = f":{int(port)}"
            for line in (result.stdout or "").splitlines():
                if "LISTENING" not in line.upper():
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                local = parts[1] if len(parts) > 1 else ""
                if not (local.endswith(needle) or local == needle):
                    continue
                try:
                    pid = int(parts[-1])
                except ValueError:
                    continue
                if pid > 0:
                    pids.add(pid)
        except Exception:
            pass
        if not pids:
            try:
                ps = (
                    f"$c=Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
                    f"-ErrorAction SilentlyContinue;"
                    f"if($c){{ $c | ForEach-Object {{ $_.OwningProcess }} | Sort-Object -Unique }}"
                )
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in (result.stdout or "").splitlines():
                    line = line.strip()
                    if line.isdigit():
                        pid = int(line)
                        if pid > 0:
                            pids.add(pid)
            except Exception:
                pass
    else:
        try:
            result = subprocess.run(
                ["lsof", "-t", f"-iTCP:{int(port)}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.add(int(line))
        except Exception:
            pass
    return sorted(pids)


def _taskkill_tree(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=12,
            )
            out = (result.stdout or "") + (result.stderr or "")
            return result.returncode == 0 or "SUCCESS" in out.upper() or "not found" in out.lower()
        os.kill(pid, 9)
        return True
    except ProcessLookupError:
        return True
    except Exception:
        return False


def _kill_process_on_port(port: int) -> bool:
    """终止占用指定端口的 LISTEN 进程及其进程树。"""
    killed_any = False
    for pid in _pids_listening_on_port(port):
        if _taskkill_tree(pid):
            killed_any = True
    return killed_any


def _read_hermes_tracked_pids() -> List[int]:
    """读取 Hermes 自身记录的 gateway PID（gateway_state.json / gateway.pid）。"""
    pids: List[int] = []
    home = hermes_home_dir()
    state_path = home / "gateway_state.json"
    pid_path = home / "gateway.pid"
    try:
        if state_path.is_file():
            data = json.loads(state_path.read_text(encoding="utf-8"))
            raw = data.get("pid")
            if raw is not None and str(raw).isdigit():
                pids.append(int(raw))
    except Exception:
        pass
    try:
        if pid_path.is_file():
            raw = pid_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if raw.isdigit():
                pids.append(int(raw))
    except Exception:
        pass
    seen: Set[int] = set()
    out: List[int] = []
    for pid in pids:
        if pid > 0 and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _clear_hermes_runtime_markers() -> None:
    home = hermes_home_dir()
    for name in ("gateway.pid", "gateway_state.json", "gateway.lock"):
        try:
            p = home / name
            if p.is_file():
                p.unlink()
        except OSError:
            pass


def _stop_via_official_hermes_api() -> Dict[str, Any]:
    """优先走 Hermes 官方停止路径（识别 PID 文件 / 进程扫描 / CLI stop）。"""
    detail: Dict[str, Any] = {"official": False, "killed": 0}
    home = ensure_hermes_home()
    os.environ["HERMES_HOME"] = str(home.resolve())

    # 先尝试官方 CLI：hermes gateway stop（与终端一致）
    try:
        cmd = None
        scripts_dir = Path(sys.executable).resolve().parent
        for name in ("hermes.exe", "hermes"):
            candidate = scripts_dir / name
            if candidate.is_file():
                cmd = [str(candidate), "gateway", "stop"]
                break
        if cmd is None:
            import shutil

            hermes_bin = shutil.which("hermes")
            if hermes_bin:
                cmd = [hermes_bin, "gateway", "stop"]
        if cmd:
            env = os.environ.copy()
            _inject_hermes_env(env)
            r = subprocess.run(
                cmd,
                cwd=str(_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=subprocess_creationflags_no_window(),
            )
            detail["cli_stop"] = {
                "returncode": r.returncode,
                "stdout": (r.stdout or "")[-200:],
                "stderr": (r.stderr or "")[-200:],
            }
    except Exception as e:
        detail["cli_stop_error"] = str(e)[:160]

    try:
        from hermes_cli.gateway import kill_gateway_processes, stop_profile_gateway

        stopped_profile = False
        try:
            stopped_profile = bool(stop_profile_gateway())
        except Exception as e:
            detail["stop_profile_error"] = str(e)[:160]
        killed = 0
        try:
            killed = int(kill_gateway_processes(force=True, all_profiles=False) or 0)
        except Exception as e:
            detail["kill_error"] = str(e)[:160]
        detail["official"] = True
        detail["stopped_profile"] = stopped_profile
        detail["killed"] = killed
    except Exception as e:
        detail["official_import_error"] = str(e)[:160]
    return detail


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


def _hermes_log_path() -> Path:
    base = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if not base:
        base = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Testory")
    return Path(base) / "logs" / "hermes_gateway.log"


def hermes_log_tail(max_lines: int = 40) -> str:
    """读取 hermes_gateway.log 末尾，供启动失败诊断。"""
    path = _hermes_log_path()
    try:
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-max(1, min(max_lines, 200)):])
    except OSError:
        return ""


def _inject_hermes_env(env: dict) -> None:
    from hermes_config import _HERMES_LLM_ENV_KEYS, resolve_hermes_api_server_key

    home = ensure_hermes_home()
    env["HERMES_HOME"] = str(home.resolve())
    env_path = home / ".env"
    if env_path.is_file():
        try:
            from dotenv import dotenv_values

            for k, v in dotenv_values(env_path).items():
                if not k or v is None:
                    continue
                # 上游 LLM 必须以 .env（平台刚同步的配置）为准，覆盖父进程旧 OPENAI_*
                if k in _HERMES_LLM_ENV_KEYS:
                    env[k] = str(v)
                    continue
                current = env.get(k)
                if not current or not str(current).strip():
                    env[k] = str(v)
        except ImportError:
            pass

    # 与 Hermes 进程共用同一 API_SERVER_KEY（勿擅自替换已有值）
    shared = resolve_hermes_api_server_key(persist_if_empty=True)
    env["API_SERVER_KEY"] = shared
    env["HERMES_API_SERVER_KEY"] = shared

    if not env.get("HERMES_GATEWAY_URL"):
        env["HERMES_GATEWAY_URL"] = "http://127.0.0.1:8642"

    if not env.get("GATEWAY_ALLOW_ALL_USERS"):
        env["GATEWAY_ALLOW_ALL_USERS"] = "true"

    # 桌面 gateway 鉴权：注入到 Hermes 子进程，避免 terminal/curl 缺 header → 401 死循环
    try:
        from desktop_service_bootstrap import _ensure_desktop_env_defaults

        _ensure_desktop_env_defaults(force=True)
    except Exception:
        pass
    for k in (
        "DESKTOP_AGENT_GATEWAY_URL",
        "DESKTOP_AGENT_GATEWAY_SECRET",
        "DESKTOP_AGENT_GATE_PORT",
        "DESKTOP_AGENT_SESSION_ID",
    ):
        v = (os.environ.get(k) or "").strip()
        if v:
            env[k] = v


def _start_gateway_process(*, replace: bool = False) -> None:
    """启动与终端 `hermes gateway run` 等价的前台进程。

    replace=True 仅在需要抢占端口时使用（官方 --replace）；默认 False 更快。
    """
    global _GATEWAY_PROC
    if _GATEWAY_PROC is not None and _GATEWAY_PROC.poll() is None:
        return
    env = os.environ.copy()
    _inject_hermes_env(env)
    env["HERMES_GATEWAY_DETACHED"] = "0"
    log_fp = _gateway_log_handle()
    _GATEWAY_PROC = subprocess.Popen(
        _hermes_gateway_cmd(replace=replace),
        cwd=str(_ROOT),
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=subprocess_creationflags_no_window(),
    )


def _stop_gateway_process() -> None:
    global _GATEWAY_PROC
    if _GATEWAY_PROC is None:
        return
    pid = _GATEWAY_PROC.pid
    try:
        if _GATEWAY_PROC.poll() is None:
            if sys.platform == "win32" and pid:
                try:
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(pid)],
                        capture_output=True,
                        timeout=10,
                    )
                except Exception:
                    pass
            try:
                if _GATEWAY_PROC.poll() is None:
                    _GATEWAY_PROC.terminate()
                    _GATEWAY_PROC.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                try:
                    _GATEWAY_PROC.kill()
                except Exception:
                    pass
    except Exception:
        pass
    _GATEWAY_PROC = None


def stop_hermes_gateway(*, clear_cdp: bool = True, cleanup_browser: bool = True) -> Dict[str, Any]:
    """彻底停止 Hermes Gateway 及其残留进程树。

    clear_cdp / cleanup_browser：用户点「停止」时应清理；
    内部 restart（例如 CDP 热更新失败）时传 False，避免刚写入的端点被清掉。

    任意异常都会在 finally 清除 _STOPPING，避免 UI 永久卡在「停止中」。
    stop 会递增 epoch，从而取消仍在进行的异步启动。
    """
    global _BOOTED, _STARTING, _STOPPING, _START_ERROR, _START_FINISHED, _LIFECYCLE_EPOCH, _START_BEGAN_AT, _STOP_BEGAN_AT, _LOADED_LLM_FP

    with _LIFECYCLE_LOCK:
        _LIFECYCLE_EPOCH += 1
        epoch = _LIFECYCLE_EPOCH
        _STOPPING = True
        _STOP_BEGAN_AT = time.monotonic()
        _STARTING = False
        _START_BEGAN_AT = 0.0
        _START_ERROR = ""
        _START_FINISHED = False
        _BOOTED = False
        _LOADED_LLM_FP = ""

    host, port = _gateway_listen_endpoint()
    detail: Dict[str, Any] = {"epoch": epoch, "port": port, "steps": []}
    deadline = time.monotonic() + 12.0

    def _timed_out() -> bool:
        return time.monotonic() >= deadline

    def _clear_stopping_flags() -> None:
        global _STOPPING, _STARTING, _START_BEGAN_AT, _STOP_BEGAN_AT, _START_FINISHED, _START_ERROR, _BOOTED
        with _LIFECYCLE_LOCK:
            # 若期间用户又点了启动（epoch 已变且正在 starting），勿误清 starting
            if epoch != _LIFECYCLE_EPOCH and _STARTING:
                _STOPPING = False
                _STOP_BEGAN_AT = 0.0
                return
            _STOPPING = False
            _STOP_BEGAN_AT = 0.0
            if epoch == _LIFECYCLE_EPOCH:
                _STARTING = False
                _START_BEGAN_AT = 0.0
                _START_FINISHED = False
                _START_ERROR = ""
                _BOOTED = False

    try:
        try:
            official = _stop_via_official_hermes_api()
            detail["official"] = official
            detail["steps"].append("official_api")
        except Exception as e:
            detail["official_error"] = str(e)[:160]

        try:
            _stop_gateway_process()
            detail["steps"].append("local_popen")
        except Exception as e:
            detail["local_popen_error"] = str(e)[:120]

        try:
            already_down = (
                not HermesGatewayClient().health_check(timeout_sec=0.35)
                and not _port_listening(host, port, timeout=0.15)
            )
        except Exception:
            already_down = False

        if already_down:
            detail["fast_path"] = True
            detail["port_cleared"] = True
            try:
                _clear_hermes_runtime_markers()
            except Exception:
                pass
            if clear_cdp:
                try:
                    from hermes_config import clear_hermes_cdp_endpoint
                    clear_hermes_cdp_endpoint(restart_gateway=False)
                    detail["steps"].append("clear_cdp")
                except Exception as e:
                    detail["clear_cdp_error"] = str(e)[:120]
            if cleanup_browser:
                try:
                    from ai_external_browser_bridge import force_cleanup_browser
                    force_cleanup_browser()
                    detail["steps"].append("cleanup_browser")
                except Exception as e:
                    detail["cleanup_browser_error"] = str(e)[:120]
            detail["fully_stopped"] = True
            return detail

        if not _timed_out():
            tracked = _read_hermes_tracked_pids()
            detail["tracked_pids"] = tracked
            for pid in tracked:
                if _timed_out():
                    break
                _taskkill_tree(pid)
            detail["steps"].append("tracked_pids")

        port_cleared = False
        for i in range(3):
            if _timed_out():
                break
            if not _port_listening(host, port, timeout=0.15):
                port_cleared = True
                break
            _kill_process_on_port(port)
            time.sleep(0.1 + i * 0.08)
        detail["port_cleared"] = port_cleared
        detail["steps"].append("port_kill")

        try:
            _clear_hermes_runtime_markers()
            detail["steps"].append("clear_markers")
        except Exception as e:
            detail["clear_markers_error"] = str(e)[:80]

        if clear_cdp:
            try:
                from hermes_config import clear_hermes_cdp_endpoint
                clear_hermes_cdp_endpoint(restart_gateway=False)
                detail["steps"].append("clear_cdp")
            except Exception as e:
                detail["clear_cdp_error"] = str(e)[:120]
        if cleanup_browser and not _timed_out():
            try:
                from ai_external_browser_bridge import force_cleanup_browser
                force_cleanup_browser()
                detail["steps"].append("cleanup_browser")
            except Exception as e:
                detail["cleanup_browser_error"] = str(e)[:120]

        try:
            still_up = _port_listening(host, port, timeout=0.2) or HermesGatewayClient().health_check(timeout_sec=0.4)
        except Exception:
            still_up = False
        detail["fully_stopped"] = not still_up
        if _timed_out():
            detail["timed_out"] = True
        return detail
    except Exception as e:
        detail["error"] = str(e)[:200]
        detail["fully_stopped"] = False
        return detail
    finally:
        _clear_stopping_flags()


def restart_hermes_gateway() -> dict:
    """CDP 或 LLM 配置变更后重启 Hermes Gateway 以加载新环境。"""
    stop_hermes_gateway(clear_cdp=False, cleanup_browser=False)
    return bootstrap_hermes_services(force=True, manual=True)


def ensure_hermes_llm_current(*, restart_if_stale: bool = True) -> dict:
    """同步平台所选模型到 Hermes；若 Gateway 仍在跑旧 Key/模型则重启。

    根因：仅改 registry / .env 时，已运行的 Hermes 子进程不会热加载 OPENAI_*。
    """
    global _LOADED_LLM_FP
    from hermes_config import (
        hermes_desired_llm_fingerprint,
        hermes_env_llm_snapshot,
        sync_platform_llm_credentials_to_hermes_env,
    )

    sync_info = sync_platform_llm_credentials_to_hermes_env()
    desired = hermes_desired_llm_fingerprint()
    snap = hermes_env_llm_snapshot()
    out: dict = {
        "synced": True,
        "desired_fingerprint": desired,
        "loaded_fingerprint": _LOADED_LLM_FP,
        "env_model": snap.get("model") or "",
        "action": "synced",
        "llm_sync": sync_info,
    }
    st = get_bootstrap_status()
    if not st.get("running"):
        out["action"] = "not_running"
        return out
    if _LOADED_LLM_FP and desired and _LOADED_LLM_FP == desired:
        out["action"] = "already_current"
        return out
    if not restart_if_stale:
        out["action"] = "stale_needs_restart"
        return out
    boot = restart_hermes_gateway()
    out["action"] = "restarted"
    out["restart"] = {
        "hermes_started": bool(boot.get("hermes_started")),
        "error": (boot.get("error") or "")[:160],
    }
    return out


def _clear_starting_locked(*, error: str = "", finished: bool = True) -> None:
    """Caller must hold _LIFECYCLE_LOCK."""
    global _STARTING, _START_FINISHED, _START_ERROR, _START_BEGAN_AT
    _STARTING = False
    _START_BEGAN_AT = 0.0
    if finished:
        _START_FINISHED = True
        _START_ERROR = (error or "")[:200]


def _force_stale_starting_unlock() -> bool:
    """若 starting 卡住过久，强制失败并返回 True。"""
    global _STARTING, _START_BEGAN_AT, _BOOTED
    with _LIFECYCLE_LOCK:
        if not _STARTING:
            return False
        began = _START_BEGAN_AT or 0.0
        if began <= 0:
            _START_BEGAN_AT = time.monotonic()
            return False
        if (time.monotonic() - began) < _START_STALE_SEC:
            return False
        _clear_starting_locked(error="启动超时（状态看门狗）：后台未在限定时间内就绪", finished=True)
        _BOOTED = False
        return True


def _force_stale_stopping_unlock() -> bool:
    """若 stopping 卡住过久，强制清除并返回 True。"""
    global _STOPPING, _STOP_BEGAN_AT
    with _LIFECYCLE_LOCK:
        if not _STOPPING:
            return False
        began = _STOP_BEGAN_AT or 0.0
        if began <= 0:
            _STOP_BEGAN_AT = time.monotonic()
            return False
        if (time.monotonic() - began) < _STOP_STALE_SEC:
            return False
        _STOPPING = False
        _STOP_BEGAN_AT = 0.0
        return True


def get_bootstrap_status() -> dict:
    """获取当前启动状态（供 status API 使用）。"""
    global _STARTING, _STOPPING, _START_ERROR, _START_FINISHED, _STOP_BEGAN_AT
    _force_stale_starting_unlock()
    _force_stale_stopping_unlock()
    try:
        from hermes_config import resolve_hermes_api_server_key
        resolve_hermes_api_server_key()
    except Exception:
        pass
    client = HermesGatewayClient()
    configured = client.is_configured()
    running = False
    if configured and not _STOPPING:
        try:
            running = client.health_check(timeout_sec=0.6)
        except Exception:
            running = False
    if running and _STARTING:
        with _LIFECYCLE_LOCK:
            if _STARTING:
                _clear_starting_locked(error="", finished=True)
                _START_ERROR = ""
    # 已停干净却仍标 stopping（超过短窗口）→ 清掉
    if _STOPPING and not running:
        try:
            host, port = _gateway_listen_endpoint()
            if not _port_listening(host, port, timeout=0.15):
                with _LIFECYCLE_LOCK:
                    began = _STOP_BEGAN_AT or 0.0
                    if began and (time.monotonic() - began) > 2.5:
                        _STOPPING = False
                        _STOP_BEGAN_AT = 0.0
        except Exception:
            pass
    cdp_connected = False
    if running:
        try:
            from hermes_config import hermes_cdp_endpoint_active
            cdp_connected = bool(hermes_cdp_endpoint_active())
        except Exception:
            pass
    host, port = _gateway_listen_endpoint()
    starting = bool(_STARTING) and not running and not _STOPPING
    stopping = bool(_STOPPING) and not running
    port_up = _port_listening(host, port)
    degraded = bool(port_up and not running and not starting and not stopping)
    start_error = _START_ERROR if _START_FINISHED and not running and not starting and not stopping else ""
    if degraded and not start_error:
        start_error = "端口被占用但健康检查失败，可点停止后重试"
    out = {
        "configured": configured,
        "running": running,
        "starting": starting,
        "stopping": stopping,
        "start_error": start_error,
        "start_finished": _START_FINISHED,
        "cdp_connected": cdp_connected,
        "port": port,
        "port_listening": port_up,
        "degraded": degraded,
        "lifecycle_epoch": _LIFECYCLE_EPOCH,
    }
    if stopping and _STOP_BEGAN_AT:
        out["stopping_elapsed_sec"] = round(time.monotonic() - _STOP_BEGAN_AT, 1)
    if starting and _START_BEGAN_AT:
        out["starting_elapsed_sec"] = round(time.monotonic() - _START_BEGAN_AT, 1)
    if start_error or degraded:
        tail = hermes_log_tail(25)
        if tail:
            out["log_tail"] = tail[-1500:]
    try:
        from hermes_config import hermes_desired_llm_fingerprint, hermes_env_llm_snapshot

        snap = hermes_env_llm_snapshot()
        desired = hermes_desired_llm_fingerprint()
        out["llm_model"] = snap.get("model") or ""
        out["llm_base_url"] = snap.get("base_url") or ""
        out["llm_stale"] = bool(
            running and _LOADED_LLM_FP and desired and _LOADED_LLM_FP != desired
        )
        out["llm_loaded_fingerprint"] = _LOADED_LLM_FP
    except Exception:
        pass
    return out


def bootstrap_hermes_services(*, force: bool = False, manual: bool = False) -> dict:
    """启动 Hermes Gateway（等价于终端：`hermes gateway run`）。

    manual=True：用户点击「启动」时调用，忽略 HERMES_AUTO_START_GATEWAY=0。
    """
    global _BOOTED, _STARTING, _STOPPING, _START_ERROR, _START_FINISHED, _LIFECYCLE_EPOCH, _START_BEGAN_AT, _STOP_BEGAN_AT

    # 非手动且关闭自动启动：不进入 starting 状态，避免 UI 报「启动失败」
    if not manual and not hermes_auto_start_gateway():
        return {"skipped": True, "reason": "auto_start_disabled", "hermes_started": False}

    with _LIFECYCLE_LOCK:
        # 停止中：若已卡太久则强清；否则短等由调用方重试，但仍允许 force 抢占
        if _STOPPING:
            began = _STOP_BEGAN_AT or 0.0
            stale = began and (time.monotonic() - began) >= min(4.0, _STOP_STALE_SEC)
            if stale or force:
                _STOPPING = False
                _STOP_BEGAN_AT = 0.0
            else:
                return {"skipped": True, "reason": "stopping", "hermes_started": False}
        if _BOOTED and not force:
            try:
                from hermes_config import hermes_desired_llm_fingerprint, resolve_hermes_api_server_key

                resolve_hermes_api_server_key()
            except Exception:
                pass
            if HermesGatewayClient().health_check(timeout_sec=0.8):
                try:
                    from hermes_config import hermes_desired_llm_fingerprint

                    desired = hermes_desired_llm_fingerprint()
                    if _LOADED_LLM_FP and desired and _LOADED_LLM_FP == desired:
                        return {"skipped": True, "already_running": True, "hermes_started": True}
                except Exception:
                    return {"skipped": True, "already_running": True, "hermes_started": True}
                # 上游模型已变：继续往下强制重拉
            _BOOTED = False
        # 卡住的 starting：允许 force 或看门狗后重试
        if _STARTING and not force:
            if _START_BEGAN_AT and (time.monotonic() - _START_BEGAN_AT) >= _START_STALE_SEC:
                _clear_starting_locked(error="启动超时，可重试", finished=True)
                _BOOTED = False
            else:
                return {"skipped": True, "reason": "starting"}
        _LIFECYCLE_EPOCH += 1
        epoch = _LIFECYCLE_EPOCH
        _BOOTED = True
        _STARTING = True
        _START_BEGAN_AT = time.monotonic()
        _STOPPING = False
        _STOP_BEGAN_AT = 0.0
        _START_ERROR = ""
        _START_FINISHED = False

    out: dict = {
        "hermes_url": "",
        "hermes_configured": False,
        "hermes_started": False,
        "epoch": epoch,
    }

    def _cancelled() -> bool:
        return epoch != _LIFECYCLE_EPOCH

    def _finish(started: bool, error: str = "") -> dict:
        # 勿在本函数对 _START_ERROR 赋值（会变成局部变量导致 UnboundLocalError）
        global _LOADED_LLM_FP
        with _LIFECYCLE_LOCK:
            if epoch != _LIFECYCLE_EPOCH:
                out["cancelled"] = True
                return out
            if started:
                _clear_starting_locked(error="", finished=True)
                out["hermes_started"] = True
                try:
                    from hermes_config import hermes_desired_llm_fingerprint

                    _LOADED_LLM_FP = hermes_desired_llm_fingerprint()
                    out["llm_fingerprint"] = _LOADED_LLM_FP
                except Exception:
                    pass
            else:
                msg = (error or "启动失败")[:200]
                _clear_starting_locked(error=msg, finished=True)
                out["error"] = msg
                tail = hermes_log_tail(30)
                if tail:
                    out["log_tail"] = tail[-1500:]
        return out

    try:
        from hermes_config import resolve_hermes_api_server_key

        ensure_hermes_home()
        shared_key = resolve_hermes_api_server_key(persist_if_empty=True)

        # 每次启动把平台推理 Key/Base 同步进 Hermes .env（修复 Missing Authentication header）
        try:
            from hermes_config import sync_platform_llm_credentials_to_hermes_env

            llm_sync = sync_platform_llm_credentials_to_hermes_env()
            out["llm_sync"] = llm_sync
            st = (llm_sync or {}).get("status") or {}
            if st.get("ok") is False:
                out["llm_compat_warning"] = st.get("message") or st.get("reason")
        except Exception as e:
            out["llm_sync_error"] = str(e)[:160]

        # 同步 bundled skills → HERMES_HOME（web/desktop/mobile/api）
        try:
            from hermes_skill_bootstrap import sync_bundled_skills_to_hermes

            sync_info = sync_bundled_skills_to_hermes()
            out["skills_synced"] = sync_info
        except Exception as e:
            out["skills_sync_error"] = str(e)[:160]

        client = HermesGatewayClient()
        out["hermes_url"] = client.base_url
        out["hermes_configured"] = client.is_configured()
        out["api_key_synced"] = bool(shared_key)

        if not client.is_configured():
            out["skipped"] = True
            out["reason"] = "not_configured"
            return _finish(False, "未配置 Hermes Gateway")

        host, port = _gateway_listen_endpoint()

        temp_env = os.environ.copy()
        _inject_hermes_env(temp_env)
        # 再次对齐客户端 token（与即将启动 / 已在跑的 Gateway 一致）
        os.environ["HERMES_API_SERVER_KEY"] = temp_env.get("HERMES_API_SERVER_KEY") or shared_key
        os.environ["API_SERVER_KEY"] = temp_env.get("API_SERVER_KEY") or shared_key
        client = HermesGatewayClient()

        if _cancelled():
            return _finish(False, "已取消")

        # 已在跑：仅当本进程记录的上游模型指纹仍匹配时才复用；否则杀掉并用新 OPENAI_* 重拉
        if client.health_check(timeout_sec=0.8):
            try:
                from hermes_config import hermes_desired_llm_fingerprint

                desired_fp = hermes_desired_llm_fingerprint()
            except Exception:
                desired_fp = ""
            if _LOADED_LLM_FP and desired_fp and _LOADED_LLM_FP == desired_fp:
                out["already_running"] = True
                return _finish(True)
            out["replacing_for_llm"] = True
            out["prev_llm_fingerprint"] = _LOADED_LLM_FP
            try:
                _stop_gateway_process()
            except Exception:
                pass
            try:
                _kill_process_on_port(port)
            except Exception:
                pass
            time.sleep(0.25)
            if _cancelled():
                return _finish(False, "已取消")
            need_replace = True
        else:
            port_up = _port_listening(host, port)
            if port_up:
                # 端口占用但鉴权失败：先对齐 key，禁止误杀健康进程
                resolve_hermes_api_server_key()
                client = HermesGatewayClient()
                if client.health_check(timeout_sec=0.8):
                    try:
                        from hermes_config import hermes_desired_llm_fingerprint

                        desired_fp = hermes_desired_llm_fingerprint()
                    except Exception:
                        desired_fp = ""
                    if _LOADED_LLM_FP and desired_fp and _LOADED_LLM_FP == desired_fp:
                        out["already_running"] = True
                        return _finish(True)
                    out["replacing_for_llm"] = True
                    try:
                        _stop_gateway_process()
                    except Exception:
                        pass
                    try:
                        _kill_process_on_port(port)
                    except Exception:
                        pass
                    time.sleep(0.25)
                    if _cancelled():
                        return _finish(False, "已取消")
                    need_replace = True
                else:
                    # 确认为僵尸/错误进程后再清理
                    _kill_process_on_port(port)
                    time.sleep(0.15)
                    if _cancelled():
                        return _finish(False, "已取消")
                    if client.health_check(timeout_sec=0.8):
                        out["already_running"] = True
                        return _finish(True)
                    need_replace = True
            else:
                need_replace = False

        _start_gateway_process(replace=need_replace)

        # 就绪探测：先 TCP，再短超时 /v1/models（与 CLI 启动体感接近）
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            if _cancelled():
                _stop_gateway_process()
                return _finish(False, "已取消")
            if _GATEWAY_PROC is not None and _GATEWAY_PROC.poll() is not None:
                return _finish(False, f"Gateway 进程异常退出 (code={_GATEWAY_PROC.returncode})")
            if _port_listening(host, port, timeout=0.25):
                if client.health_check(timeout_sec=0.6):
                    return _finish(True)
            time.sleep(0.2)
        return _finish(False, "启动超时，请检查 hermes 日志（或终端执行: hermes gateway run）")
    except Exception as e:
        try:
            _stop_gateway_process()
        except Exception:
            pass
        return _finish(False, str(e)[:200])


def sync_platform_llm_to_hermes() -> dict:
    """同步平台 LLM 配置到 Hermes，如果配置了独立 provider 则使用独立配置。"""
    from hermes_config import ensure_hermes_home, sync_platform_llm_credentials_to_hermes_env

    hermes_provider = (os.environ.get("HERMES_LLM_PROVIDER") or "").strip()
    result: dict = {"independent_provider": bool(hermes_provider)}
    if hermes_provider:
        result["provider"] = hermes_provider
        result["model"] = (os.environ.get("HERMES_LLM_MODEL") or "").strip()
    ensure_hermes_home(force_env=True)
    try:
        result.update(sync_platform_llm_credentials_to_hermes_env())
    except Exception as e:
        result["credential_sync_error"] = str(e)[:160]
    result["synced"] = True
    return result


def health_check_cdp() -> dict:
    """验证 Hermes 是否能到达当前 CDP 端点。"""
    from hermes_config import hermes_cdp_endpoint_active

    cdp_ws = hermes_cdp_endpoint_active()
    if not cdp_ws:
        return {"ok": False, "reason": "no_cdp_endpoint"}
    client = HermesGatewayClient()
    if not client.is_configured():
        return {"ok": False, "reason": "hermes_not_configured"}
    try:
        import requests

        base = (client.base_url or "").rstrip("/")
        resp = requests.get(f"{base}/v1/health/cdp", timeout=5)
        if resp.ok:
            data = resp.json() if resp.content else {}
            return {"ok": True, "cdp_endpoint": cdp_ws, "detail": data}
        return {"ok": False, "reason": "health_endpoint_error", "status": resp.status_code}
    except Exception as e:
        return {"ok": False, "reason": "request_failed", "error": str(e)}
