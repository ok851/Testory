"""
HTTP client for Hermes Agent API Server (OpenAI-compatible).

Environment:
- HERMES_GATEWAY_URL — base URL, default http://127.0.0.1:8642
- HERMES_API_SERVER_KEY — Bearer token (required)
- HERMES_GATEWAY_TIMEOUT — seconds (default 180)
- HERMES_TOOL_RESULT_MAX_CHARS — max chars returned to platform LLM (default 48000)
- HERMES_CHAT_COMPLETIONS_MODEL — model id (default hermes-agent)
- HERMES_EXECUTE_SYSTEM_PROMPT — optional system message for browser exploration tasks
- HERMES_CHAT_MAX_TOKENS — optional max_tokens for long exploration output
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import RequestException

from logger import uat_logger


def _norm(s: Any) -> str:
    return (str(s) if s is not None else "").strip()


def hermes_tool_result_max_chars() -> int:
    try:
        v = int(os.environ.get("HERMES_TOOL_RESULT_MAX_CHARS", "48000") or 48000)
    except ValueError:
        v = 48000
    return max(4000, min(v, 200000))


class HermesGatewayClient:
    def __init__(self) -> None:
        self.base_url = _norm(os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8642")).rstrip("/")
        self.token = _norm(os.environ.get("HERMES_API_SERVER_KEY", ""))
        try:
            raw_to = int(os.environ.get("HERMES_GATEWAY_TIMEOUT", "180") or "180")
        except ValueError:
            raw_to = 180
        self.timeout = max(30, min(raw_to, 1200))
        self.chat_model = _norm(os.environ.get("HERMES_CHAT_COMPLETIONS_MODEL", "hermes-agent")) or "hermes-agent"

    def is_configured(self) -> bool:
        if os.environ.get("HERMES_ENABLE", "1").strip().lower() in ("0", "false", "no", "off"):
            return False
        return bool(self.base_url and self.token)

    def execute_user_instruction(self, instruction: str, session_id: str = "") -> str:
        instruction = _norm(instruction)
        if not instruction:
            return json.dumps({"ok": False, "error": "instruction 为空"}, ensure_ascii=False)
        if not self.is_configured():
            return json.dumps(
                {
                    "ok": False,
                    "error": "Hermes Gateway 未配置：请设置 HERMES_GATEWAY_URL 与 HERMES_API_SERVER_KEY",
                },
                ensure_ascii=False,
            )
        if session_id:
            instruction = f"[session_id={session_id}]\n\n{instruction}"
        try:
            out = self._chat_completions(instruction)
        except RequestException as e:
            uat_logger.warning("Hermes Gateway 请求失败: %s", e)
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
        return _clip_tool_result(out, max_chars=hermes_tool_result_max_chars())

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _chat_completions(self, instruction: str) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        messages: List[Dict[str, str]] = []
        sys_p = _norm(os.environ.get("HERMES_EXECUTE_SYSTEM_PROMPT", ""))
        if not sys_p:
            sys_p = (
                "你是浏览器端测试代理。请按用户指令自主操作页面（优先使用已连接的浏览器 CDP），"
                "并在回复中给出可复现为自动化步骤的摘要（含 URL、关键选择器或可见文案、检查点结论）。"
            )
        messages.append({"role": "system", "content": sys_p})
        messages.append({"role": "user", "content": instruction})
        payload: Dict[str, Any] = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": 0.2,
        }
        try:
            mt = int(os.environ.get("HERMES_CHAT_MAX_TOKENS", "0") or 0)
            if mt > 0:
                payload["max_tokens"] = min(mt, 128000)
        except ValueError:
            pass
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
        if not resp.ok:
            raise ValueError(_http_error_detail(resp))
        data = resp.json() if resp.content else {}
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") or {}
            content = _norm(msg.get("content"))
            if content:
                return content
        err = data.get("error")
        if isinstance(err, dict):
            raise ValueError(_norm(err.get("message")) or json.dumps(err, ensure_ascii=False))
        raise ValueError("Hermes Gateway 返回为空或无法解析")

    def health_check(self, timeout_sec: float = 2.5) -> bool:
        if not self.base_url or not self.token:
            return False
        try:
            resp = requests.get(
                f"{self.base_url}/v1/models",
                headers=self._headers(),
                timeout=timeout_sec,
            )
            return resp.ok
        except RequestException:
            return False


def _http_error_detail(resp: requests.Response) -> str:
    try:
        body = (resp.text or "").strip().replace("\n", " ")[:480]
        return f"HTTP {resp.status_code}" + (f": {body}" if body else "")
    except Exception:
        return f"HTTP {resp.status_code}"


def _clip_tool_result(text: str, *, max_chars: Optional[int] = None) -> str:
    cap = int(max_chars) if max_chars is not None else hermes_tool_result_max_chars()
    cap = max(4000, min(cap, 200000))
    if len(text) <= cap:
        return text
    return text[: cap - 80] + "\n…(truncated for context limit)…"
