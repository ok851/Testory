"""
Multi-provider LLM dispatch for AI test generation (Ollama, OpenAI-compatible, Anthropic, Gemini).
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import quote

import requests
from requests.exceptions import RequestException

if TYPE_CHECKING:
    from ai_local_inference import LocalAIService


# ---------------------------------------------------------------------------
# 熔断器 + 指数退避重试
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """简易熔断器：连续失败达到阈值后熔断指定时长。"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60):
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if time.time() < self._open_until:
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self._failure_threshold:
                self._open_until = time.time() + self._recovery_timeout


_cloud_breaker = CircuitBreaker()


def _is_retryable_error(e: RequestException) -> bool:
    """判断 HTTP 异常是否值得重试（429 / 5xx / 连接错误）。"""
    resp = getattr(e, "response", None)
    if resp is not None:
        sc = resp.status_code
        if sc == 429:
            return True
        if 400 <= sc < 500:
            return False
    return True


def retry_with_backoff(
    func: Callable,
    max_retries: int = 3,
    breaker: Optional[CircuitBreaker] = None,
    abort_event: Optional[threading.Event] = None,
) -> Callable:
    """返回可调用对象：失败时指数退避重试（2s/4s/8s），支持熔断器与取消中断。"""

    def wrapper(*args, **kwargs):
        last_exc: Optional[RequestException] = None
        for attempt in range(max_retries + 1):
            if abort_event is not None and abort_event.is_set():
                raise InterruptedError("操作已被用户取消")
            if breaker is not None and not breaker.allow():
                raise ValueError("云端服务暂时不可用（熔断中），请稍后重试")
            try:
                result = func(*args, **kwargs)
                if breaker is not None:
                    breaker.record_success()
                return result
            except RequestException as e:
                last_exc = e
                if not _is_retryable_error(e):
                    break
                # 计算等待时间
                resp = getattr(e, "response", None)
                wait_s = 2 ** (attempt + 1)  # 2s, 4s, 8s
                if resp is not None and resp.status_code == 429:
                    ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                    if ra:
                        try:
                            wait_s = max(1.0, float(ra))
                        except (ValueError, TypeError):
                            pass
                if attempt < max_retries:
                    if abort_event is not None:
                        if abort_event.wait(timeout=wait_s):
                            raise InterruptedError("操作已被用户取消")
                    else:
                        time.sleep(wait_s)
        # 所有重试耗尽
        if breaker is not None:
            breaker.record_failure()
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("重试耗尽")

    return wrapper


def _norm(s: Any) -> str:
    return (str(s) if s is not None else "").strip()


def normalize_api_key(api_key: Any) -> str:
    """去掉首尾空白；若用户粘贴了「Bearer xxx」则只保留密钥本体。"""
    k = _norm(api_key)
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    return k


def _openai_compat_endpoint_url(base_url: str, *, provider: str = "", group_id: str = "") -> str:
    b = _norm(base_url)
    if not b:
        raise ValueError("云端模型需配置 base_url（默认见提供商目录）")
    if "/chat/completions" in b:
        url = b
    else:
        url = b.rstrip("/") + "/chat/completions"
    gid = _norm(group_id)
    if gid and (_norm(provider) == "minimax" or "minimax" in b.lower()):
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}GroupId={quote(gid, safe='')}"
    return url


def _uses_xiaomimimo_auth(base_url: str, provider: str = "", api_key: str = "") -> bool:
    bu = _norm(base_url).lower()
    prov = _norm(provider)
    key = normalize_api_key(api_key)
    if prov in ("xiaomi_mimo_token", "xiaomi_mimo"):
        return True
    if "xiaomimimo.com" in bu:
        return True
    return bool(key.startswith("tp-") and "xiaomimimo" in bu)


def _openai_compat_headers(api_key: str, *, provider: str = "", base_url: str = "") -> Dict[str, str]:
    key = normalize_api_key(api_key)
    if not key:
        raise ValueError("该提供商需要 API 密钥")
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if _uses_xiaomimimo_auth(base_url, provider, key):
        # 小米 MiMo Token Plan 官方示例使用 api-key 头，而非 Authorization: Bearer
        headers["api-key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"
    return headers


_JSON_PLAN_SYSTEM = (
    "You are a senior QA engineer. Reply with exactly one JSON object only—no markdown fences, "
    "no commentary. First non-whitespace character must be '{'. "
    "Schema: AI-assisted web test plan with case_name, case_url, description, precondition, expected_result, steps[]. "
    "Steps: use action assert (+compare_type) for text/URL expectations; use verify only for captcha/human checks with input_value auto|slider|image|visible|exist|clickable. "
    "For tianai-captcha (TAC) and mixed captcha types prefer verify auto (curve/rotate/click-text auto-detected). "
    "Selectors must be real css/xpath from the page, never snapshot line numbers alone."
)

_ASSISTANT_CHAT_SYSTEM = (
    "你是 Testory 平台的 AI 测试助手。用简洁自然的中文与用户对话。"
    "闲聊、问身份/能力、问建议时直接回答。"
    "禁止输出测试用例 JSON（不要出现 case_name / steps 等字段）。"
    "禁止假装已操作浏览器或桌面；需要自动化时请提示用户给出可执行任务指令。"
)


def openai_compatible_chat(
    base_url: str,
    api_key: str,
    model_id: str,
    prompt: str,
    timeout: int = 240,
    *,
    provider: str = "",
    group_id: str = "",
    abort_event: Optional[threading.Event] = None,
    purpose: str = "json_plan",
) -> str:
    """OpenAI /v1/chat/completions compatible endpoints.

    purpose:
      - json_plan: 强制用例 JSON（生成/优化用例）
      - assistant: 自然语言对话（闲聊/问答）
    """
    url = _openai_compat_endpoint_url(base_url, provider=provider, group_id=group_id)
    headers = _openai_compat_headers(api_key, provider=provider, base_url=base_url)
    sys_content = (
        _ASSISTANT_CHAT_SYSTEM
        if (purpose or "").strip().lower() in ("assistant", "chat", "nl")
        else _JSON_PLAN_SYSTEM
    )
    payload: Dict[str, Any] = {
        "model": _norm(model_id),
        "messages": [
            {
                "role": "system",
                "content": sys_content,
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2 if (purpose or "").strip().lower() == "json_plan" else 0.4,
    }

    def _do_request():
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp

    try:
        resp = retry_with_backoff(_do_request, max_retries=3, breaker=_cloud_breaker, abort_event=abort_event)()
    except RequestException as e:
        _raise_http("OpenAI 兼容接口", e)

    raw_text = resp.content.decode('utf-8', errors='replace') if resp.content else ""
    data = json.loads(raw_text) if raw_text.strip() else {}
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
    provider: str = "",
    group_id: str = "",
    abort_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """POST /v1/chat/completions; returns assistant message dict (content, tool_calls optional)."""
    url = _openai_compat_endpoint_url(base_url, provider=provider, group_id=group_id)
    headers = _openai_compat_headers(api_key, provider=provider, base_url=base_url)
    payload: Dict[str, Any] = {
        "model": _norm(model_id),
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools

    def _do_request():
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp

    try:
        resp = retry_with_backoff(_do_request, max_retries=3, breaker=_cloud_breaker, abort_event=abort_event)()
    except RequestException as e:
        _raise_http("OpenAI 兼容接口", e)

    raw_text = resp.content.decode('utf-8', errors='replace') if resp.content else ""
    data = json.loads(raw_text) if raw_text.strip() else {}
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
    abort_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """
    Multi-turn chat completion with optional tools (Ollama, OpenAI-compatible, Anthropic).
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
        if not _norm(api_key):
            raise ValueError("Anthropic 需要 API 密钥")
        system_text = ""
        api_messages = messages
        if messages and messages[0].get("role") == "system":
            system_text = messages[0].get("content") or ""
            api_messages = messages[1:]
        return anthropic_messages_chat(
            base_url or "https://api.anthropic.com",
            str(api_key),
            model_id,
            timeout=to,
            messages=api_messages,
            tools=tools,
            system=system_text,
            abort_event=abort_event,
        )

    if style == "google_gemini" or provider == "google_gemini":
        raise ValueError("Gemini 当前不支持 AI 对话工具循环，请改用 Ollama 或 OpenAI 兼容模型")

    gid = _norm(profile.get("group_id"))
    return openai_compatible_chat_completion(
        base_url,
        str(api_key),
        model_id,
        messages,
        tools,
        temperature=temperature,
        timeout=to,
        provider=provider,
        group_id=gid,
        abort_event=abort_event,
    )


def anthropic_messages_chat(
    base_url: str,
    api_key: str,
    model_id: str,
    prompt: str = "",
    timeout: int = 240,
    *,
    messages: Optional[List[Dict[str, Any]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    system: str = "",
    abort_event: Optional[threading.Event] = None,
) -> Any:
    """Anthropic Messages API（支持 tool_use）。

    当 *tools* 为空且未传 *messages* 时返回 ``str``（向后兼容旧调用）；
    当 *tools* 非空时返回 ``dict``（OpenAI 兼容格式，含 ``tool_calls``）。
    """
    b = _norm(base_url) or "https://api.anthropic.com"
    url = b.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": _norm(api_key),
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    # 构建 messages：优先使用显式 messages 参数
    if messages is not None:
        api_messages: List[Dict[str, Any]] = []
        system_parts: List[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            else:
                api_messages.append({"role": role, "content": content})
        system_text = system or ("\n\n".join(system_parts) if system_parts else "")
    else:
        api_messages = [{"role": "user", "content": prompt}]
        system_text = system

    payload: Dict[str, Any] = {
        "model": _norm(model_id),
        "max_tokens": 8192,
        "messages": api_messages,
    }
    if system_text:
        payload["system"] = system_text

    # 工具 schema 转换：OpenAI → Anthropic
    if tools:
        anth_tools: List[Dict[str, Any]] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            if t.get("type") == "function":
                fn = t.get("function") or {}
                anth_tool: Dict[str, Any] = {
                    "name": fn.get("name", ""),
                    "input_schema": fn.get("parameters") or {"type": "object"},
                }
                if fn.get("description"):
                    anth_tool["description"] = fn["description"]
                anth_tools.append(anth_tool)
        if anth_tools:
            payload["tools"] = anth_tools

    def _do_request():
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp

    try:
        resp = retry_with_backoff(_do_request, max_retries=3, breaker=_cloud_breaker, abort_event=abort_event)()
    except RequestException as e:
        _raise_http("Anthropic", e)

    raw_text = resp.content.decode('utf-8', errors='replace') if resp.content else ""
    data = json.loads(raw_text) if raw_text.strip() else {}
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise ValueError("Anthropic 返回为空或无法解析")

    # 解析 content blocks：text + tool_use
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(_norm(block.get("text")))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                },
            })

    text_content = "".join(text_parts).strip()

    # 有 tools 时返回 dict（OpenAI 兼容）
    if tools:
        out: Dict[str, Any] = {
            "role": "assistant",
            "content": text_content if text_content else None,
        }
        if tool_calls:
            out["tool_calls"] = tool_calls
        return out

    # 无 tools 时返回 str（向后兼容）
    if text_content:
        return text_content
    raise ValueError("Anthropic 返回为空或无法解析")


def google_gemini_chat(
    api_key: str,
    model_id: str,
    prompt: str,
    timeout: int = 240,
    *,
    abort_event: Optional[threading.Event] = None,
    purpose: str = "json_plan",
) -> str:
    mid = _norm(model_id)
    if not mid.startswith("models/"):
        mid = "models/" + mid
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/{mid}:generateContent"
        f"?key={quote(_norm(api_key), safe='')}"
    )
    sys_text = (
        _ASSISTANT_CHAT_SYSTEM
        if (purpose or "").strip().lower() in ("assistant", "chat", "nl")
        else (
            "You are a senior QA engineer. Return only JSON, no markdown. "
            "Use web UI actions compatible with a test runner."
        )
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "systemInstruction": {
            "parts": [
                {
                    "text": sys_text
                }
            ]
        },
    }

    def _do_request():
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp

    try:
        resp = retry_with_backoff(_do_request, max_retries=3, breaker=_cloud_breaker, abort_event=abort_event)()
    except RequestException as e:
        _raise_http("Google Gemini", e)

    raw_text = resp.content.decode('utf-8', errors='replace') if resp.content else ""
    data = json.loads(raw_text) if raw_text.strip() else {}
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
                if "1004" in body or "authorized_error" in low or "api secret key" in low:
                    hint += (
                        " MiniMax 常见原因：① 使用了第三方/代理 Key（如 tp- 开头）访问官方 api.minimaxi.com；"
                        "② 将「订阅 Key（sk-cp-）」与「按量计费 Key」混用；"
                        "③ Key 填错或复制不完整。请到 MiniMax 控制台 → Token Plan / API Keys 重新复制，"
                        "国内 Base URL 用 https://api.minimaxi.com/v1，国际用 https://api.minimax.io/v1。"
                    )
            elif sc == 429:
                hint = "（触发限流或配额：请稍后重试或升级套餐。）"
            elif sc == 400 and (
                "not supported model" in low
                or "param incorrect" in low
                or "model not found" in low
                or "invalid model" in low
            ):
                if "xiaomimimo" in (getattr(response, "url", None) or "").lower() or "xiaomimimo" in low:
                    hint = (
                        "（该地址为小米 MiMo Token Plan：model 应填 mimo-v2.5-pro、mimo-v2.5 或 mimo-v2-flash，"
                        "勿填 MiniMax-M3 / abab7-preview 等其它厂商型号。）"
                    )
                else:
                    hint = (
                        "（API 已连通，但 model_id 不被该地址支持：请在「AI 设置」中编辑模型，"
                        "改用提供商/代理商文档中的型号；MiniMax 官方建议 MiniMax-M3 / MiniMax-M2.5，"
                        "Base URL 填 https://api.minimaxi.com/v1（国内）或 https://api.minimax.io/v1，"
                        "勿填带 /chat/completions 的完整路径。）"
                    )
        except Exception:
            detail = f"HTTP {response.status_code}: {detail}"
    raise ValueError(f"{label} 请求失败：{detail}{hint}") from e


def dispatch_chat(
    prompt: str,
    profile: Dict[str, Any],
    local_service: "LocalAIService",
    *,
    purpose: str = "json_plan",
) -> str:
    """
    Route chat completion by profile.api_style (and provider fallback).

    purpose:
      - json_plan（默认）: 用例生成等，要求 JSON
      - assistant / chat: 自然语言对话，禁止用例 JSON
    """
    style = _norm(profile.get("api_style"))
    provider = _norm(profile.get("provider"))
    model_id = _norm(profile.get("model_id"))
    api_key = profile.get("api_key")
    base_url = _norm(profile.get("base_url"))
    timeout = int(os.environ.get("LOCAL_LLM_TIMEOUT", "240"))
    purpose_n = (purpose or "json_plan").strip().lower() or "json_plan"

    if not model_id:
        raise ValueError("模型配置缺少 model_id")

    if style == "ollama" or provider == "ollama":
        obase = base_url or local_service.base_url
        return local_service.chat_ollama(prompt, model_id, obase, purpose=purpose_n)

    if style == "anthropic_messages" or provider == "anthropic":
        if not _norm(api_key):
            raise ValueError("Anthropic 需要 API 密钥")
        sys = (
            _ASSISTANT_CHAT_SYSTEM
            if purpose_n in ("assistant", "chat", "nl")
            else _JSON_PLAN_SYSTEM
        )
        return anthropic_messages_chat(
            base_url or "https://api.anthropic.com",
            str(api_key),
            model_id,
            prompt,
            timeout,
            system=sys,
        )

    if style == "google_gemini" or provider == "google_gemini":
        if not _norm(api_key):
            raise ValueError("Gemini 需要 API 密钥")
        return google_gemini_chat(
            str(api_key), model_id, prompt, timeout, purpose=purpose_n
        )

    # Default: OpenAI-compatible (most cloud vendors)
    if not normalize_api_key(api_key):
        raise ValueError("该提供商需要 API 密钥")
    return openai_compatible_chat(
        base_url,
        str(api_key),
        model_id,
        prompt,
        timeout,
        provider=provider,
        group_id=_norm(profile.get("group_id")),
        purpose=purpose_n,
    )


# ---------------------------------------------------------------------------
# 流式 API（Streaming）— 用于 SSE 推送 LLM 推理进度
# ---------------------------------------------------------------------------

def openai_compatible_chat_stream(
    base_url: str,
    api_key: str,
    model_id: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    *,
    temperature: float = 0.2,
    timeout: int = 240,
    provider: str = "",
    group_id: str = "",
    abort_event: Optional[threading.Event] = None,
):
    """OpenAI 兼容流式 chat completion。yield (event_type, data) 元组。

    event_type: "content_delta" | "tool_call_delta" | "done" | "error"
    """
    url = _openai_compat_endpoint_url(base_url, provider=provider, group_id=group_id)
    headers = _openai_compat_headers(api_key, provider=provider, base_url=base_url)
    payload: Dict[str, Any] = {
        "model": _norm(model_id),
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools

    if not _cloud_breaker.allow():
        yield ("error", "云端服务暂时不可用（熔断中），请稍后重试")
        return

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout, stream=True)
        resp.raise_for_status()
    except RequestException as e:
        _cloud_breaker.record_failure()
        yield ("error", f"云端请求失败: {e}")
        return

    # 解析 SSE 流
    tool_buffers: Dict[int, Dict[str, str]] = {}  # index -> {id, name, arguments}
    content_buf = ""
    try:
        for line in resp.iter_lines():
            if abort_event is not None and abort_event.is_set():
                resp.close()
                yield ("error", "操作已被用户取消")
                return
            if not line:
                continue
            try:
                line_text = line.decode('utf-8', errors='replace')
            except Exception:
                continue
            if not line_text.startswith("data: "):
                continue
            data_str = line_text[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            delta = ((chunk.get("choices") or [{}])[0] or {}).get("delta") or {}
            # 内容增量
            content_delta = delta.get("content")
            if content_delta:
                content_buf += content_delta
                yield ("content_delta", content_delta)
            # 工具调用增量
            tc_list = delta.get("tool_calls")
            if isinstance(tc_list, list):
                for tc in tc_list:
                    idx = tc.get("index", 0)
                    if idx not in tool_buffers:
                        tool_buffers[idx] = {"id": "", "name": "", "arguments": ""}
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        tool_buffers[idx]["name"] = fn["name"]
                        yield ("tool_call_delta", {
                            "index": idx,
                            "name": fn["name"],
                            "arguments_len": len(tool_buffers[idx]["arguments"]),
                        })
                    if fn.get("arguments"):
                        tool_buffers[idx]["arguments"] += fn["arguments"]
                        yield ("tool_call_delta", {
                            "index": idx,
                            "name": tool_buffers[idx]["name"],
                            "arguments_len": len(tool_buffers[idx]["arguments"]),
                        })
                    if tc.get("id"):
                        tool_buffers[idx]["id"] = tc["id"]
    except Exception as e:
        yield ("error", f"流式读取中断: {e}")
        return
    finally:
        resp.close()

    _cloud_breaker.record_success()

    # 组装最终 assistant message
    out: Dict[str, Any] = {"role": "assistant", "content": content_buf or None}
    if tool_buffers:
        calls = []
        for idx in sorted(tool_buffers.keys()):
            tb = tool_buffers[idx]
            calls.append({
                "id": tb["id"] or f"call_{idx}",
                "type": "function",
                "function": {"name": tb["name"], "arguments": tb["arguments"]},
            })
        out["tool_calls"] = calls
    yield ("done", out)


def anthropic_messages_stream(
    base_url: str,
    api_key: str,
    model_id: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    *,
    system: str = "",
    timeout: int = 240,
    abort_event: Optional[threading.Event] = None,
):
    """Anthropic Messages API 流式。yield (event_type, data) 元组。"""
    b = _norm(base_url) or "https://api.anthropic.com"
    url = b.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": _norm(api_key),
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": _norm(model_id),
        "max_tokens": 8192,
        "messages": messages,
        "stream": True,
    }
    if system:
        payload["system"] = system
    if tools:
        anth_tools: List[Dict[str, Any]] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            if t.get("type") == "function":
                fn = t.get("function") or {}
                anth_tool: Dict[str, Any] = {
                    "name": fn.get("name", ""),
                    "input_schema": fn.get("parameters") or {"type": "object"},
                }
                if fn.get("description"):
                    anth_tool["description"] = fn["description"]
                anth_tools.append(anth_tool)
        if anth_tools:
            payload["tools"] = anth_tools

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout, stream=True)
        resp.raise_for_status()
    except RequestException as e:
        yield ("error", f"Anthropic 请求失败: {e}")
        return

    content_buf = ""
    tool_calls: List[Dict[str, Any]] = []
    current_tool_idx = -1
    current_tool_input_buf = ""
    try:
        for line in resp.iter_lines():
            if abort_event is not None and abort_event.is_set():
                resp.close()
                yield ("error", "操作已被用户取消")
                return
            if not line:
                continue
            try:
                line_text = line.decode('utf-8', errors='replace')
            except Exception:
                continue
            if not line_text.startswith("data: "):
                continue
            data_str = line_text[6:].strip()
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "content_block_start":
                cb = event.get("content_block") or {}
                if cb.get("type") == "tool_use":
                    current_tool_idx = len(tool_calls)
                    tool_calls.append({
                        "id": cb.get("id", ""),
                        "type": "function",
                        "function": {"name": cb.get("name", ""), "arguments": ""},
                    })
                    current_tool_input_buf = ""
            elif etype == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        content_buf += text
                        yield ("content_delta", text)
                elif delta.get("type") == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    if partial and current_tool_idx >= 0:
                        current_tool_input_buf += partial
            elif etype == "content_block_stop":
                if current_tool_idx >= 0 and current_tool_idx < len(tool_calls):
                    tool_calls[current_tool_idx]["function"]["arguments"] = current_tool_input_buf
                    current_tool_idx = -1
                    current_tool_input_buf = ""
            elif etype == "message_stop":
                break
    except Exception as e:
        yield ("error", f"Anthropic 流式读取中断: {e}")
        return
    finally:
        resp.close()

    out: Dict[str, Any] = {"role": "assistant", "content": content_buf or None}
    if tool_calls:
        out["tool_calls"] = tool_calls
    yield ("done", out)


def get_llm_profile_by_id(profile_id: str) -> Optional[Dict[str, Any]]:
    """按 id 取模型配置；找不到返回 None。"""
    pid = (profile_id or "").strip()
    if not pid:
        return None
    try:
        from ai_config_paths import ai_model_registry_path

        reg_path = ai_model_registry_path()
        if not reg_path.is_file():
            return None
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        for p in reg.get("profiles") or []:
            if isinstance(p, dict) and (p.get("id") or "").strip() == pid:
                return p
    except Exception:
        pass
    return None


def set_active_llm_profile_id(profile_id: str) -> bool:
    """将 registry 的 active_profile_id 设为指定配置。"""
    pid = (profile_id or "").strip()
    if not pid or not get_llm_profile_by_id(pid):
        return False
    try:
        from ai_config_paths import ai_model_registry_path

        reg_path = ai_model_registry_path()
        reg = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.is_file() else {}
        reg["active_profile_id"] = pid
        reg["version"] = 2
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def get_active_llm_profile() -> Optional[Dict[str, Any]]:
    """获取当前激活的 LLM 配置。"""
    try:
        from ai_config_paths import ai_model_registry_path
        reg_path = ai_model_registry_path()
        if reg_path.is_file():
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
            aid = (reg.get("active_profile_id") or "").strip()
            for p in (reg.get("profiles") or []):
                if isinstance(p, dict) and p.get("id") == aid:
                    return p
            if reg.get("profiles"):
                return reg["profiles"][0]
    except Exception:
        pass
    return None


def dispatch_chat_stream(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    profile: Optional[Dict[str, Any]],
    local_service: "LocalAIService",
    *,
    temperature: float = 0.2,
    timeout: Optional[int] = None,
    abort_event: Optional[threading.Event] = None,
):
    """流式多轮 chat completion。yield (event_type, data)。

    支持 Ollama（降级为非流式）、OpenAI 兼容、Anthropic。
    """
    if not profile:
        yield ("error", "未配置推理模型")
        return
    style = _norm(profile.get("api_style"))
    provider = _norm(profile.get("provider"))
    model_id = _norm(profile.get("model_id"))
    api_key = profile.get("api_key")
    base_url = _norm(profile.get("base_url"))
    to = timeout if timeout is not None else int(os.environ.get("LOCAL_LLM_TIMEOUT", "240"))

    if not model_id:
        yield ("error", "模型配置缺少 model_id")
        return

    # Ollama 降级为非流式（Ollama 的 streaming tool calling 支持不完善）
    if style == "ollama" or provider == "ollama":
        try:
            obase = base_url or local_service.base_url
            result = local_service.chat_ollama_messages(messages, model_id, tools, obase)
            yield ("done", result)
        except Exception as e:
            yield ("error", str(e))
        return

    if style == "anthropic_messages" or provider == "anthropic":
        if not _norm(api_key):
            yield ("error", "Anthropic 需要 API 密钥")
            return
        system_text = ""
        api_messages = messages
        if messages and messages[0].get("role") == "system":
            system_text = messages[0].get("content") or ""
            api_messages = messages[1:]
        yield from anthropic_messages_stream(
            base_url or "https://api.anthropic.com",
            str(api_key),
            model_id,
            api_messages,
            tools=tools,
            system=system_text,
            timeout=to,
            abort_event=abort_event,
        )
        return

    if style == "google_gemini" or provider == "google_gemini":
        yield ("error", "Gemini 当前不支持流式工具循环，请改用 Ollama 或 OpenAI 兼容模型")
        return

    # Default: OpenAI 兼容
    gid = _norm(profile.get("group_id"))
    yield from openai_compatible_chat_stream(
        base_url,
        str(api_key),
        model_id,
        messages,
        tools,
        temperature=temperature,
        timeout=to,
        provider=provider,
        group_id=gid,
        abort_event=abort_event,
    )
