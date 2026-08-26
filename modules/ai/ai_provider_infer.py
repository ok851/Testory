"""根据 model + key + base_url 三字段推断 AI 提供商。"""
from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse


def _host_from_base_url(base_url: str) -> str:
    bu = (base_url or "").strip()
    if not bu:
        return ""
    if "://" not in bu:
        bu = "https://" + bu
    try:
        netloc = urlparse(bu).netloc or ""
        return netloc.lower().split(":")[0]
    except Exception:
        return ""


def _is_ollama_base_url(base_url: str) -> bool:
    bu = (base_url or "").strip().lower()
    if not bu:
        return False
    return "11434" in bu or "ollama" in bu


def infer_provider_from_simple_config(
    base_url: str = "",
    api_key: str = "",
    providers: List[Dict[str, Any]] | None = None,
) -> str:
    """
    简单三字段模式下的 provider 推断：
    - 无密钥且（未填 Base URL 或 Ollama 地址）→ ollama
    - 代理 Key（tp-）→ custom_openai
    - 未填 Base URL → openai
    - 有 Base URL → 按目录 default_base_url 匹配，否则 custom_openai
    """
    bu = (base_url or "").strip()
    bu_low = bu.lower()
    key = (api_key or "").strip()
    key_low = key.lower()

    if key_low.startswith("tp-"):
        return "custom_openai"

    if not key:
        if not bu or _is_ollama_base_url(bu):
            return "ollama"

    if not bu:
        return "openai"

    if _is_ollama_base_url(bu):
        return "ollama"

    host = _host_from_base_url(bu)
    for p in providers or []:
        if not isinstance(p, dict):
            continue
        pid = (p.get("id") or "").strip()
        if not pid or pid == "custom_openai":
            continue
        def_bu = (p.get("default_base_url") or "").strip()
        if not def_bu:
            continue
        def_low = def_bu.lower().rstrip("/")
        bu_norm = bu_low.rstrip("/")
        def_host = _host_from_base_url(def_bu)
        if def_host and host and host == def_host:
            return pid
        if bu_norm == def_low or bu_norm.startswith(def_low + "/"):
            return pid

    return "custom_openai"
