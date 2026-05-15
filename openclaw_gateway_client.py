"""
HTTP client for OpenClaw Gateway from the AI automation testing platform backend.

Supports:
- OPENCLAW_EXECUTE_MODE=chat_completions (default): POST /v1/chat/completions with model openclaw/default
- OPENCLAW_EXECUTE_MODE=tools_invoke: POST configurable path (default /tools/invoke) with JSON body

Environment:
- OPENCLAW_GATEWAY_URL — base URL without trailing slash, e.g. http://127.0.0.1:18789
- OPENCLAW_GATEWAY_TOKEN — Bearer token for HTTP API
- OPENCLAW_GATEWAY_TIMEOUT — seconds (default 180；整系统探索可设 300–900)
- OPENCLAW_TOOL_RESULT_MAX_CHARS — 回传给 LLM 的最大字符（默认 48000，上限约 200000）
- OPENCLAW_TOOLS_INVOKE_PATH — default /tools/invoke
- OPENCLAW_EXECUTE_TOOL_NAME — required for tools_invoke when using named tools
- OPENCLAW_GATEWAY_SESSION_KEY — optional sessionKey for tools.invoke
- OPENCLAW_CHAT_COMPLETIONS_MODEL — override model id (default openclaw/default)
- OPENCLAW_EXECUTE_SYSTEM_PROMPT — 可选；非空时作为 chat_completions 的 system 消息，引导网关侧做长链路/模块化探索
- OPENCLAW_CHAT_MAX_TOKENS — 可选；>0 时写入请求 max_tokens，便于长探索输出
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


def _tool_result_max_chars() -> int:
    """回传给平台 LLM 的 OpenClaw 结果最大长度（与网关超时一样可随任务调大）。"""
    try:
        v = int(os.environ.get("OPENCLAW_TOOL_RESULT_MAX_CHARS", "48000") or 48000)
    except ValueError:
        v = 48000
    return max(4000, min(v, 200000))


class OpenClawGatewayClient:
    def __init__(self) -> None:
        self.base_url = _norm(os.environ.get("OPENCLAW_GATEWAY_URL", "")).rstrip("/")
        self.token = _norm(os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""))
        try:
            raw_to = int(os.environ.get("OPENCLAW_GATEWAY_TIMEOUT", "180") or "180")
        except ValueError:
            raw_to = 180
        # 允许长链路探索（整模块 / 多页）；仍设上限避免挂死 worker
        self.timeout = max(30, min(raw_to, 1200))
        self.invoke_path = _norm(os.environ.get("OPENCLAW_TOOLS_INVOKE_PATH", "/tools/invoke")) or "/tools/invoke"
        if not self.invoke_path.startswith("/"):
            self.invoke_path = "/" + self.invoke_path
        self.execute_mode = _norm(os.environ.get("OPENCLAW_EXECUTE_MODE", "chat_completions")).lower() or "chat_completions"
        self.tool_name = _norm(os.environ.get("OPENCLAW_EXECUTE_TOOL_NAME", ""))
        self.session_key = _norm(os.environ.get("OPENCLAW_GATEWAY_SESSION_KEY", ""))
        self.chat_model = _norm(os.environ.get("OPENCLAW_CHAT_COMPLETIONS_MODEL", "openclaw/default")) or "openclaw/default"

    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)

    def execute_user_instruction(self, instruction: str, session_id: str = "") -> str:
        """
        Run OpenClaw for a natural-language instruction. Returns text for the LLM tool role (truncated if huge).
        """
        instruction = _norm(instruction)
        if not instruction:
            return json.dumps({"ok": False, "error": "instruction 为空"}, ensure_ascii=False)
        if not self.is_configured():
            return json.dumps(
                {
                    "ok": False,
                    "error": "OpenClaw Gateway 未配置：请设置 OPENCLAW_GATEWAY_URL 与 OPENCLAW_GATEWAY_TOKEN",
                },
                ensure_ascii=False,
            )

        use_invoke = self.execute_mode == "tools_invoke" or bool(self.tool_name)
        if use_invoke and not self.tool_name:
            uat_logger.warning(
                "OPENCLAW_EXECUTE_MODE=tools_invoke 但未设置 OPENCLAW_EXECUTE_TOOL_NAME，改用 chat_completions"
            )
            use_invoke = False

        try:
            if use_invoke:
                out = self._tools_invoke(instruction, session_id)
            else:
                out = self._chat_completions(instruction)
        except RequestException as e:
            uat_logger.warning("OpenClaw Gateway 请求失败: %s", e)
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

        return _clip_tool_result(out, max_chars=_tool_result_max_chars())

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _tools_invoke(self, instruction: str, session_id: str) -> str:
        args: Dict[str, Any] = {"instruction": instruction, "message": instruction}
        if session_id:
            args["session_id"] = session_id
        body: Dict[str, Any] = {"name": self.tool_name, "args": args}
        if self.session_key:
            body["sessionKey"] = self.session_key
        url = f"{self.base_url}{self.invoke_path}"
        resp = requests.post(url, json=body, headers=self._headers(), timeout=self.timeout)
        if not resp.ok:
            raise ValueError(_http_error_detail(resp))
        data = resp.json() if resp.content else {}
        return json.dumps(data, ensure_ascii=False)

    def _chat_completions(self, instruction: str) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        messages: List[Dict[str, str]] = []
        sys_p = _norm(os.environ.get("OPENCLAW_EXECUTE_SYSTEM_PROMPT", ""))
        if sys_p:
            messages.append({"role": "system", "content": sys_p})
        messages.append({"role": "user", "content": instruction})
        payload: Dict[str, Any] = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": 0.2,
        }
        try:
            mt = int(os.environ.get("OPENCLAW_CHAT_MAX_TOKENS", "0") or 0)
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
        raise ValueError("OpenClaw Gateway 返回为空或无法解析")


def _http_error_detail(resp: requests.Response) -> str:
    try:
        body = (resp.text or "").strip().replace("\n", " ")[:480]
        return f"HTTP {resp.status_code}" + (f": {body}" if body else "")
    except Exception:
        return f"HTTP {resp.status_code}"


def _clip_tool_result(text: str, *, max_chars: Optional[int] = None) -> str:
    cap = int(max_chars) if max_chars is not None else _tool_result_max_chars()
    cap = max(4000, min(cap, 200000))
    if len(text) <= cap:
        return text
    return text[: cap - 80] + "\n…(truncated for context limit)…"
