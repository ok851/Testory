"""
Multi-provider LLM dispatch for AI test generation (Ollama, OpenAI-compatible, Anthropic, Gemini).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import quote

import requests
from requests.exceptions import RequestException

if TYPE_CHECKING:
    from ai_local_inference import LocalAIService


def _norm(s: Any) -> str:
    return (str(s) if s is not None else "").strip()


def openai_compatible_chat(
    base_url: str,
    api_key: str,
    model_id: str,
    prompt: str,
    timeout: int = 240,
) -> str:
    """OpenAI /v1/chat/completions compatible endpoints."""
    b = _norm(base_url)
    if not b:
        raise ValueError("云端模型需配置 base_url（默认见提供商目录）")
    if "/chat/completions" in b:
        url = b
    else:
        url = b.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {_norm(api_key)}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": _norm(model_id),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a senior QA engineer. Reply with exactly one JSON object only—no markdown fences, "
                    "no commentary. First non-whitespace character must be '{'. "
                    "Schema: UI test plan with case_name, case_url, description, precondition, expected_result, steps[]. "
                    "Steps: use action assert (+compare_type) for text/URL expectations; use verify only for captcha/human checks with input_value auto|slider|image|visible|exist|clickable. "
                    "Selectors must be real css/xpath from the page, never snapshot line numbers alone."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except RequestException as e:
        _raise_http("OpenAI 兼容接口", e)
    data = resp.json() if resp.content else {}
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") or {}
        content = _norm(msg.get("content"))
        if content:
            return content
    err = data.get("error")
    if isinstance(err, dict):
        raise ValueError("云端模型错误: " + _norm(err.get("message") or json.dumps(err, ensure_ascii=False)))
    raise ValueError("云端模型返回为空或无法解析")


def openai_compatible_chat_completion(
    base_url: str,
    api_key: str,
    model_id: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    *,
    temperature: float = 0.2,
    timeout: int = 240,
) -> Dict[str, Any]:
    """POST /v1/chat/completions; returns assistant message dict (content, tool_calls optional)."""
    b = _norm(base_url)
    if not b:
        raise ValueError("云端模型需配置 base_url")
    if "/chat/completions" in b:
        url = b
    else:
        url = b.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {_norm(api_key)}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": _norm(model_id),
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except RequestException as e:
        _raise_http("OpenAI 兼容接口", e)
    data = resp.json() if resp.content else {}
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") or {}
        out: Dict[str, Any] = {
            "role": msg.get("role") or "assistant",
            "content": msg.get("content"),
        }
        if msg.get("tool_calls"):
            out["tool_calls"] = msg["tool_calls"]
        return out
    err = data.get("error")
    if isinstance(err, dict):
        raise ValueError("云端模型错误: " + _norm(err.get("message") or json.dumps(err, ensure_ascii=False)))
    raise ValueError("云端模型返回为空或无法解析")


def dispatch_chat_completion_messages(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    profile: Dict[str, Any],
    local_service: "LocalAIService",
    *,
    temperature: float = 0.2,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Multi-turn chat completion with optional tools (Ollama or OpenAI-compatible profiles).
    """
    style = _norm(profile.get("api_style"))
    provider = _norm(profile.get("provider"))
    model_id = _norm(profile.get("model_id"))
    api_key = profile.get("api_key")
    base_url = _norm(profile.get("base_url"))
    to = timeout if timeout is not None else int(os.environ.get("LOCAL_LLM_TIMEOUT", "240"))

    if not model_id:
        raise ValueError("模型配置缺少 model_id")

    if style == "ollama" or provider == "ollama":
        obase = base_url or local_service.base_url
        return local_service.chat_ollama_messages(messages, model_id, tools, obase)

    if style == "anthropic_messages" or provider == "anthropic":
        raise ValueError("Anthropic 当前不支持 AI 对话工具循环，请改用 Ollama 或 OpenAI 兼容模型")

    if style == "google_gemini" or provider == "google_gemini":
        raise ValueError("Gemini 当前不支持 AI 对话工具循环，请改用 Ollama 或 OpenAI 兼容模型")

    if not _norm(api_key):
        raise ValueError("该提供商需要 API 密钥")
    return openai_compatible_chat_completion(
        base_url, str(api_key), model_id, messages, tools, temperature=temperature, timeout=to
    )


def anthropic_messages_chat(
    base_url: str,
    api_key: str,
    model_id: str,
    prompt: str,
    timeout: int = 240,
) -> str:
    b = _norm(base_url) or "https://api.anthropic.com"
    url = b.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": _norm(api_key),
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _norm(model_id),
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except RequestException as e:
        _raise_http("Anthropic", e)
    data = resp.json() if resp.content else {}
    blocks = data.get("content")
    if isinstance(blocks, list):
        parts = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(_norm(block.get("text")))
        text = "".join(parts).strip()
        if text:
            return text
    raise ValueError("Anthropic 返回为空或无法解析")


def google_gemini_chat(
    api_key: str,
    model_id: str,
    prompt: str,
    timeout: int = 240,
) -> str:
    mid = _norm(model_id)
    if not mid.startswith("models/"):
        mid = "models/" + mid
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/{mid}:generateContent"
        f"?key={quote(_norm(api_key), safe='')}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        "You are a senior QA engineer. Return only JSON, no markdown. "
                        "Use web UI actions compatible with a test runner."
                    )
                }
            ]
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
    except RequestException as e:
        _raise_http("Google Gemini", e)
    data = resp.json() if resp.content else {}
    cands = data.get("candidates")
    if isinstance(cands, list) and cands:
        content = cands[0].get("content") or {}
        parts = content.get("parts")
        if isinstance(parts, list) and parts:
            t = _norm(parts[0].get("text"))
            if t:
                return t
    raise ValueError("Gemini 返回为空或无法解析")


def _raise_http(label: str, e: RequestException) -> None:
    detail = str(e).strip() or type(e).__name__
    response = getattr(e, "response", None)
    hint = ""
    if response is not None:
        try:
            body = (response.text or "").strip().replace("\n", " ")[:480]
            detail = f"HTTP {response.status_code}" + (f": {body}" if body else "")
            low = body.lower()
            sc = int(response.status_code or 0)
            if sc == 402 or (sc in (400, 403) and "insufficient balance" in low):
                hint = "（服务商提示余额不足或欠费：请到对应平台控制台充值/检查账单后再试。）"
            elif sc == 401:
                hint = "（请核对 API Key 是否正确、是否已启用或权限是否足够。）"
            elif sc == 429:
                hint = "（触发限流或配额：请稍后重试或升级套餐。）"
        except Exception:
            detail = f"HTTP {response.status_code}: {detail}"
    raise ValueError(f"{label} 请求失败：{detail}{hint}") from e


def dispatch_chat(
    prompt: str,
    profile: Dict[str, Any],
    local_service: "LocalAIService",
) -> str:
    """
    Route chat completion by profile.api_style (and provider fallback).
    """
    style = _norm(profile.get("api_style"))
    provider = _norm(profile.get("provider"))
    model_id = _norm(profile.get("model_id"))
    api_key = profile.get("api_key")
    base_url = _norm(profile.get("base_url"))
    timeout = int(os.environ.get("LOCAL_LLM_TIMEOUT", "240"))

    if not model_id:
        raise ValueError("模型配置缺少 model_id")

    if style == "ollama" or provider == "ollama":
        obase = base_url or local_service.base_url
        return local_service.chat_ollama(prompt, model_id, obase)

    if style == "anthropic_messages" or provider == "anthropic":
        if not _norm(api_key):
            raise ValueError("Anthropic 需要 API 密钥")
        return anthropic_messages_chat(base_url or "https://api.anthropic.com", str(api_key), model_id, prompt, timeout)

    if style == "google_gemini" or provider == "google_gemini":
        if not _norm(api_key):
            raise ValueError("Gemini 需要 API 密钥")
        return google_gemini_chat(str(api_key), model_id, prompt, timeout)

    # Default: OpenAI-compatible (most cloud vendors)
    if not _norm(api_key):
        raise ValueError("该提供商需要 API 密钥")
    return openai_compatible_chat(base_url, str(api_key), model_id, prompt, timeout)
