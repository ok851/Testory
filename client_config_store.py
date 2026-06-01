# -*- coding: utf-8 -*-
"""桌面客户端本地配置：团队服务器地址、会话 Token、首次启动完成标记。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _config_path() -> Path:
    raw = (os.environ.get("UAT_DATA_DIR") or "").strip()
    base = Path(raw) if raw else Path(__file__).resolve().parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "client_config.json"


def load_client_config() -> Dict[str, Any]:
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_client_config(data: Dict[str, Any]) -> None:
    path = _config_path()
    existing = load_client_config()
    existing.update(data)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def get_team_server_url() -> str:
    env_url = (os.environ.get("TEAM_SERVER_URL") or "").strip().rstrip("/")
    if env_url:
        return env_url
    cfg = load_client_config()
    return (cfg.get("team_server_url") or "").strip().rstrip("/")


def set_team_server_url(url: str) -> None:
    save_client_config({"team_server_url": (url or "").strip().rstrip("/")})


def get_auth_token() -> str:
    cfg = load_client_config()
    return (cfg.get("auth_token") or "").strip()


def set_auth_token(token: str) -> None:
    save_client_config({"auth_token": (token or "").strip()})


def is_setup_complete() -> bool:
    cfg = load_client_config()
    if cfg.get("setup_complete"):
        return True
    return bool(get_team_server_url() and get_auth_token())


def mark_setup_complete(complete: bool = True) -> None:
    save_client_config({"setup_complete": bool(complete)})


def clear_client_config() -> None:
    path = _config_path()
    if path.is_file():
        path.unlink()
