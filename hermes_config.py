# -*- coding: utf-8 -*-
"""Hermes 内嵌配置：安装目录 .env 模板与 LLM provider 同步。"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

_ACTIVE_CDP_ENDPOINT: str = ""


def hermes_home_dir() -> Path:
    raw = (os.environ.get("HERMES_HOME") or "").strip()
    if raw:
        return Path(raw)
    uat = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if uat:
        return Path(uat) / "hermes"
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Testory" / "hermes"


def hermes_skills_dir() -> Path:
    custom = (os.environ.get("HERMES_SKILLS_DIR") or "").strip()
    if custom:
        return Path(custom)
    return hermes_home_dir() / "skills"


def _read_active_llm_profile() -> Dict[str, Any]:
    try:
        from ai_config_paths import ai_model_registry_path

        path = ai_model_registry_path()
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        aid = (raw.get("active_profile_id") or "").strip()
        for p in raw.get("profiles") or []:
            if isinstance(p, dict) and p.get("id") == aid:
                return p
        if raw.get("profiles") and isinstance(raw["profiles"][0], dict):
            return raw["profiles"][0]
    except Exception:
        pass
    return {}


def build_hermes_env_lines(*, api_key: Optional[str] = None) -> str:
    """生成 Hermes ~/.env 内容（写入 HERMES_HOME/.env）。"""
    key = (api_key or os.environ.get("HERMES_API_SERVER_KEY") or "").strip()
    if not key:
        key = secrets.token_urlsafe(24)
    prof = _read_active_llm_profile()
    base_url = (prof.get("base_url") or os.environ.get("LOCAL_LLM_BASE_URL") or "http://127.0.0.1:11434/v1").strip()
    model_id = (prof.get("model_id") or os.environ.get("LOCAL_LLM_MODEL_MID") or "llama3:8b-instruct").strip()
    api_key_llm = (prof.get("api_key") or "").strip()
    lines = [
        "API_SERVER_ENABLED=true",
        f"API_SERVER_KEY={key}",
        "API_SERVER_HOST=127.0.0.1",
        "API_SERVER_PORT=8642",
        'toolsets=["hermes-cli","browser","web","memory","skills"]',
        "HERMES_BROWSER_MODE=cdp_attach",
    ]
    cdp = (os.environ.get("HERMES_CDP_ENDPOINT") or "").strip()
    if cdp:
        lines.append(f"HERMES_CDP_ENDPOINT={cdp}")
    if prof.get("api_style") == "ollama" or prof.get("provider") == "ollama":
        lines.extend(
            [
                "PROVIDER=openai_compatible",
                f"OPENAI_API_BASE={base_url.rstrip('/')}",
                f"OPENAI_MODEL={model_id}",
                "OPENAI_API_KEY=ollama",
            ]
        )
    elif base_url:
        lines.extend(
            [
                "PROVIDER=openai_compatible",
                f"OPENAI_API_BASE={base_url.rstrip('/')}",
                f"OPENAI_MODEL={model_id}",
            ]
        )
        if api_key_llm:
            lines.append(f"OPENAI_API_KEY={api_key_llm}")
    return "\n".join(lines) + "\n"


def ensure_hermes_home(*, force_env: bool = False) -> Path:
    """创建 HERMES_HOME、skills 目录与默认 .env（不覆盖已有 .env 除非 force_env）。"""
    home = hermes_home_dir()
    home.mkdir(parents=True, exist_ok=True)
    hermes_skills_dir().mkdir(parents=True, exist_ok=True)
    env_path = home / ".env"
    if force_env or not env_path.is_file():
        env_path.write_text(build_hermes_env_lines(), encoding="utf-8")
    _sync_hermes_env_to_process(env_path)
    return home


def _sync_hermes_env_to_process(env_path: Path) -> None:
    """将 HERMES_HOME/.env 中的 API key 同步到进程环境（供 HermesGatewayClient 读取）。"""
    if not env_path.is_file():
        return
    try:
        from dotenv import dotenv_values

        for key, val in dotenv_values(env_path).items():
            if not key or val is None:
                continue
            if key not in os.environ:
                os.environ[key] = str(val)
    except ImportError:
        pass
    api_key = (os.environ.get("API_SERVER_KEY") or "").strip()
    if api_key and not (os.environ.get("HERMES_API_SERVER_KEY") or "").strip():
        os.environ["HERMES_API_SERVER_KEY"] = api_key
    if not (os.environ.get("HERMES_GATEWAY_URL") or "").strip():
        os.environ["HERMES_GATEWAY_URL"] = "http://127.0.0.1:8642"


def hermes_cdp_endpoint_active() -> str:
    """当前进程内已同步的画布 CDP WebSocket URL（空表示未 attach）。"""
    env = (os.environ.get("HERMES_CDP_ENDPOINT") or "").strip()
    if env:
        return env
    return (_ACTIVE_CDP_ENDPOINT or "").strip()


def hermes_cdp_attached() -> bool:
    return bool(hermes_cdp_endpoint_active())


def _hermes_env_path() -> Path:
    return hermes_home_dir() / ".env"


def _upsert_env_line(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{prefix}{value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{prefix}{value}")
    return out


def _remove_env_line(lines: list[str], key: str) -> list[str]:
    prefix = f"{key}="
    return [line for line in lines if not line.startswith(prefix)]


def _write_hermes_env_lines(lines: list[str]) -> None:
    env_path = _hermes_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join([ln for ln in lines if ln is not None]).strip()
    if text:
        text += "\n"
    env_path.write_text(text, encoding="utf-8")
    _sync_hermes_env_to_process(env_path)


def sync_hermes_cdp_endpoint(cdp_ws_url: str, *, restart_gateway: bool = True) -> bool:
    """
    将 Browser Runtime 返回的 cdp_browser_ws 写入 HERMES_HOME/.env 与进程环境，
    供 Hermes gateway 以 cdp_attach 模式操作画布 Chromium。
    """
    global _ACTIVE_CDP_ENDPOINT
    ws = (cdp_ws_url or "").strip()
    if not ws:
        return False
    ensure_hermes_home()
    env_path = _hermes_env_path()
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines = build_hermes_env_lines().splitlines()
    lines = _upsert_env_line(lines, "HERMES_CDP_ENDPOINT", ws)
    if "HERMES_BROWSER_MODE=cdp_attach" not in lines:
        lines.append("HERMES_BROWSER_MODE=cdp_attach")
    _write_hermes_env_lines(lines)
    os.environ["HERMES_CDP_ENDPOINT"] = ws
    os.environ["HERMES_BROWSER_MODE"] = "cdp_attach"
    _ACTIVE_CDP_ENDPOINT = ws
    if restart_gateway:
        try:
            from hermes_service_bootstrap import restart_hermes_gateway

            restart_hermes_gateway()
        except Exception:
            pass
    return True


def clear_hermes_cdp_endpoint(*, restart_gateway: bool = True) -> None:
    """画布会话结束时清除 CDP attach 配置。"""
    global _ACTIVE_CDP_ENDPOINT
    _ACTIVE_CDP_ENDPOINT = ""
    os.environ.pop("HERMES_CDP_ENDPOINT", None)
    env_path = _hermes_env_path()
    if env_path.is_file():
        lines = _remove_env_line(env_path.read_text(encoding="utf-8", errors="replace").splitlines(), "HERMES_CDP_ENDPOINT")
        _write_hermes_env_lines(lines)
    if restart_gateway:
        try:
            from hermes_service_bootstrap import restart_hermes_gateway

            restart_hermes_gateway()
        except Exception:
            pass
