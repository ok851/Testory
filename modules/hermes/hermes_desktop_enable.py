# -*- coding: utf-8 -*-
"""启动智能体时启用桌面操控：Desktop Gateway(:8766) + Testory MCP(:9820) → Hermes。

Windows：官方 computer_use/cua-driver 不可用，靠 MCP windows_* + :8766。
Darwin：可选启用 platform_toolsets 中的 computer_use。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from modules.core.logger import uat_logger
from modules.core.subprocess_win import subprocess_creationflags_no_window

_MCP_PROC: Optional[subprocess.Popen] = None
_MCP_LOCK = threading.Lock()

DEFAULT_MCP_HTTP_PORT = 9820
DEFAULT_MCP_SERVER_NAME = "testory-desktop"


def _truthy(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def desktop_mcp_enabled() -> bool:
    """默认开启；设 HERMES_DESKTOP_MCP=0 可关闭。"""
    return _truthy("HERMES_DESKTOP_MCP", "1")


def desktop_mcp_http_port() -> int:
    try:
        return int(os.environ.get("TESTORY_MCP_HTTP_PORT", str(DEFAULT_MCP_HTTP_PORT)) or DEFAULT_MCP_HTTP_PORT)
    except ValueError:
        return DEFAULT_MCP_HTTP_PORT


def desktop_mcp_url() -> str:
    port = desktop_mcp_http_port()
    return (os.environ.get("TESTORY_DESKTOP_MCP_URL") or f"http://127.0.0.1:{port}/mcp").rstrip("/")


def _port_listening(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _mcp_listen_endpoint() -> Tuple[str, int]:
    raw = desktop_mcp_url()
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or desktop_mcp_http_port()
    return host, int(port)


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception as e:
        uat_logger.warning("read hermes config.yaml failed: %s", e)
        return {}


def _dump_yaml(path: Path, data: Dict[str, Any]) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    path.write_text(text, encoding="utf-8")


def hermes_config_yaml_path() -> Path:
    from modules.hermes.hermes_config import hermes_home_dir

    return hermes_home_dir() / "config.yaml"


def ensure_hermes_config_desktop_control() -> Dict[str, Any]:
    """Upsert HERMES_HOME/config.yaml：mcp_servers.testory-desktop；Darwin 加 computer_use。"""
    out: Dict[str, Any] = {"ok": False, "changed": False, "path": ""}
    if not desktop_mcp_enabled():
        out["skipped"] = True
        out["reason"] = "HERMES_DESKTOP_MCP=0"
        return out

    path = hermes_config_yaml_path()
    out["path"] = str(path)
    cfg = _load_yaml(path)
    before = yaml_snapshot(cfg)

    mcp_servers = cfg.get("mcp_servers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
        cfg["mcp_servers"] = mcp_servers

    name = (os.environ.get("TESTORY_DESKTOP_MCP_NAME") or DEFAULT_MCP_SERVER_NAME).strip() or DEFAULT_MCP_SERVER_NAME
    entry = mcp_servers.get(name) if isinstance(mcp_servers.get(name), dict) else {}
    url = desktop_mcp_url()
    # 保留用户自定义 headers / tools，仅强制 url + enabled
    entry = dict(entry)
    entry["url"] = url
    entry["enabled"] = True
    entry.setdefault("timeout", 120)
    entry.setdefault("connect_timeout", 60)
    mcp_servers[name] = entry
    cfg["mcp_servers"] = mcp_servers
    out["mcp_server"] = name
    out["mcp_url"] = url

    # macOS：启用官方 computer_use（Windows 上 check_fn 为假，写入也无害但默认不写以免误导）
    if sys.platform == "darwin" and _truthy("HERMES_ENABLE_COMPUTER_USE", "1"):
        pt = cfg.get("platform_toolsets")
        if not isinstance(pt, dict):
            pt = {}
            cfg["platform_toolsets"] = pt
        api_tools = pt.get("api_server")
        if not isinstance(api_tools, list):
            api_tools = ["hermes-api-server"]
        else:
            api_tools = list(api_tools)
        if "computer_use" not in api_tools:
            api_tools.append("computer_use")
            out["computer_use"] = True
        pt["api_server"] = api_tools
        cfg["platform_toolsets"] = pt

    after = yaml_snapshot(cfg)
    if before != after:
        _dump_yaml(path, cfg)
        out["changed"] = True
    out["ok"] = True
    return out


def yaml_snapshot(cfg: Dict[str, Any]) -> str:
    try:
        import yaml

        return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=True)
    except Exception:
        return str(cfg)


def ensure_testory_desktop_mcp_process() -> Dict[str, Any]:
    """若 :9820/mcp 未监听则拉起 python -m testory_mcp.transport（platform=desktop）。"""
    global _MCP_PROC
    out: Dict[str, Any] = {"ok": False, "started": False, "url": desktop_mcp_url()}
    if not desktop_mcp_enabled():
        out["skipped"] = True
        out["reason"] = "HERMES_DESKTOP_MCP=0"
        return out
    host, port = _mcp_listen_endpoint()
    if _port_listening(host, port):
        out["ok"] = True
        out["already_running"] = True
        return out

    with _MCP_LOCK:
        if _MCP_PROC is not None and _MCP_PROC.poll() is None:
            # 进程在但端口未开：稍等
            for _ in range(20):
                if _port_listening(host, port):
                    out["ok"] = True
                    out["started"] = True
                    return out
                time.sleep(0.15)
        try:
            from modules.desktop.desktop_service_bootstrap import _ensure_desktop_env_defaults

            _ensure_desktop_env_defaults(force=True)
        except Exception:
            pass

        env = os.environ.copy()
        env["TESTORY_MCP_PLATFORM"] = "desktop"
        env["TESTORY_MCP_HTTP_PORT"] = str(port)
        env.setdefault("DESKTOP_GATEWAY_INPROCESS", "1")
        try:
            root = Path(__file__).resolve().parents[2]
            _MCP_PROC = subprocess.Popen(
                [sys.executable, "-m", "testory_mcp.transport"],
                cwd=str(root),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess_creationflags_no_window(),
            )
            out["pid"] = _MCP_PROC.pid
        except Exception as e:
            out["error"] = str(e)[:160]
            return out

        for _ in range(40):
            if _MCP_PROC.poll() is not None:
                out["error"] = f"mcp_exit_code={_MCP_PROC.returncode}"
                return out
            if _port_listening(host, port):
                out["ok"] = True
                out["started"] = True
                return out
            time.sleep(0.15)
        out["error"] = "mcp_start_timeout"
        return out


def stop_testory_desktop_mcp() -> None:
    global _MCP_PROC
    with _MCP_LOCK:
        proc = _MCP_PROC
        _MCP_PROC = None
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        pass


def ensure_hermes_desktop_control() -> Dict[str, Any]:
    """一键：桌面 gateway + Hermes config MCP + MCP HTTP 进程。

    返回 changed=True 表示 config.yaml 有变更，调用方应重启 Hermes Gateway 以加载 MCP。
    """
    out: Dict[str, Any] = {"ok": False, "changed": False}
    if not desktop_mcp_enabled():
        out["skipped"] = True
        out["reason"] = "HERMES_DESKTOP_MCP=0"
        out["ok"] = True
        return out

    # 1) Desktop gateway :8766
    try:
        from modules.desktop.desktop_service_bootstrap import ensure_desktop_gateway_for_agent

        gw = ensure_desktop_gateway_for_agent()
        out["desktop_gateway"] = gw
    except Exception as e:
        out["desktop_gateway"] = {"ok": False, "error": str(e)[:160]}

    # 2) config.yaml mcp_servers
    try:
        cfg = ensure_hermes_config_desktop_control()
        out["config"] = cfg
        out["changed"] = bool(cfg.get("changed"))
    except Exception as e:
        out["config"] = {"ok": False, "error": str(e)[:160]}

    # 3) MCP HTTP process
    try:
        mcp = ensure_testory_desktop_mcp_process()
        out["mcp"] = mcp
    except Exception as e:
        out["mcp"] = {"ok": False, "error": str(e)[:160]}

    gw_ok = bool((out.get("desktop_gateway") or {}).get("ok")) or sys.platform != "win32"
    cfg_ok = bool((out.get("config") or {}).get("ok")) or bool((out.get("config") or {}).get("skipped"))
    mcp_ok = bool((out.get("mcp") or {}).get("ok")) or bool((out.get("mcp") or {}).get("skipped"))
    # Windows 上 gateway+mcp 都要好；配置必须写成功
    if sys.platform == "win32":
        out["ok"] = bool(cfg_ok and (gw_ok or mcp_ok))
    else:
        out["ok"] = bool(cfg_ok)
    return out
