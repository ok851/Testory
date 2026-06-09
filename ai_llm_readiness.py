# -*- coding: utf-8 -*-
"""混合 LLM 就绪检测：Ollama 优先，否则引导云端 API。"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from ai_config_paths import ai_model_registry_path


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _ollama_base_url() -> str:
    raw = (os.environ.get("LOCAL_LLM_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[:-3]
    return raw


def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_ollama() -> Tuple[bool, List[str], str]:
    """返回 (reachable, model_names, error_message)。"""
    base = _ollama_base_url()
    host = "127.0.0.1"
    port = 11434
    if "://" in base:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(base)
            host = parsed.hostname or host
            port = parsed.port or port
        except Exception:
            pass
    if not _port_open(host, port):
        return False, [], f"无法连接 Ollama（{host}:{port}）"
    url = f"{base}/api/tags"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        models = []
        for item in payload.get("models") or []:
            if isinstance(item, dict) and item.get("name"):
                models.append(str(item["name"]))
        return True, models, ""
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return False, [], str(e)


def _load_registry() -> Dict[str, Any]:
    path = ai_model_registry_path()
    if not path.is_file():
        return {"version": 2, "profiles": [], "active_profile_id": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"version": 2, "profiles": [], "active_profile_id": ""}
    except Exception:
        return {"version": 2, "profiles": [], "active_profile_id": ""}


def _active_profile(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    aid = (cfg.get("active_profile_id") or "").strip()
    for p in cfg.get("profiles") or []:
        if isinstance(p, dict) and p.get("id") == aid:
            return p
    profiles = cfg.get("profiles") or []
    if profiles and isinstance(profiles[0], dict):
        return profiles[0]
    return None


def ensure_default_registry_if_missing() -> bool:
    """首次启动写入默认 Ollama profile（不覆盖已有 registry）。"""
    path = ai_model_registry_path()
    if path.is_file():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    mid = (os.environ.get("LOCAL_LLM_MODEL_MID") or "llama3:8b-instruct").strip()
    base = (os.environ.get("LOCAL_LLM_BASE_URL") or "http://127.0.0.1:11434/v1").strip()
    cfg = {
        "version": 2,
        "active_profile_id": "local-ollama",
        "profiles": [
            {
                "id": "local-ollama",
                "label": "本地 Ollama",
                "provider": "ollama",
                "api_style": "ollama",
                "base_url": base,
                "model_id": mid,
            }
        ],
    }
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def assess_llm_readiness(*, local_ai_service: Any = None) -> Dict[str, Any]:
    """
    返回前端向导所需状态：
    ready / mode(ollama|cloud|none) / ollama / cloud_profile / wizard_dismissed
    """
    ensure_default_registry_if_missing()
    cfg = _load_registry()
    profile = _active_profile(cfg)
    ollama_ok, ollama_models, ollama_err = probe_ollama()

    cloud_ready = False
    cloud_label = ""
    if profile:
        prov = (profile.get("provider") or "").strip()
        if prov not in ("ollama",) and (profile.get("api_key") or "").strip():
            cloud_ready = True
            cloud_label = (profile.get("label") or profile.get("model_id") or prov).strip()

    if ollama_ok and ollama_models:
        mode = "ollama"
        ready = True
    elif cloud_ready:
        mode = "cloud"
        ready = True
    elif ollama_ok:
        mode = "ollama"
        ready = bool(profile and profile.get("provider") == "ollama")
    else:
        mode = "none"
        ready = False

    if local_ai_service is not None:
        try:
            installed, err = local_ai_service.list_installed_models()
            if installed:
                ollama_models = list(dict.fromkeys(list(ollama_models) + list(installed)))
                ollama_ok = True
                ollama_err = err or ""
                if ollama_models and not cloud_ready:
                    mode = "ollama"
                    ready = True
        except Exception:
            pass

    dismissed = _truthy("AI_LLM_WIZARD_DISMISSED", "0")
    return {
        "ready": ready,
        "mode": mode,
        "ollama": {
            "reachable": ollama_ok,
            "models": ollama_models[:40],
            "error": ollama_err,
            "base_url": _ollama_base_url(),
            "download_url": "https://ollama.com/download",
        },
        "cloud_profile": {
            "configured": cloud_ready,
            "label": cloud_label,
            "active_profile_id": (cfg.get("active_profile_id") or "").strip(),
        },
        "wizard_dismissed": dismissed,
        "recommendation": (
            "已检测到 Ollama，可直接使用 AI 测试。"
            if mode == "ollama" and ready
            else (
                "已配置云端模型，可直接使用 AI 测试。"
                if mode == "cloud" and ready
                else "请安装 Ollama（推荐）或在设置中配置云端 API Key。"
            )
        ),
    }
