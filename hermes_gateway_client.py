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
- HERMES_PASS_SESSION_ID — if 1, prefix instruction with [session_id=…] (default 0; can corrupt Hermes)
"""
from __future__ import annotations

import json
import os
import threading
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


def _is_corrupt_session_error(err_s: str) -> bool:
    s = err_s or ""
    return ("NoneType" in s and "attribute 'id'" in s) or (
        "NoneType" in s and "has no attribute 'id'" in s
    )


def _friendly_corrupt_msg(detail: str = "") -> str:
    base = (
        "智能体会话异常（内部状态损坏）。"
        "请点「停止」后再「启动」重试；若刚切换过启停，等状态恢复为「未启动/运行中」再执行任务。"
    )
    d = (detail or "").strip()
    if d and len(d) < 180:
        return f"{base}（{d}）"
    return base


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
        if not self.base_url:
            return False
        # 只要配置了 Gateway URL 即视为已配置；空/占位符 API key 会在启动时自动替换为默认 key
        return True

    def execute_user_instruction(
        self,
        instruction: str,
        session_id: str = "",
        abort_event=None,
        *,
        system_prompt: str = "",
        reset_session_on_corrupt: bool = True,
        pass_session_prefix: Optional[bool] = None,
    ) -> str:
        """执行自然语言指令。

        注意：默认 **不** 把平台 task session_id 注入为 `[session_id=…]`。
        该前缀会让 Hermes 尝试恢复内部会话，损坏时触发 NoneType.id。
        仅当 HERMES_PASS_SESSION_ID=1 或显式 pass_session_prefix=True 时注入。
        """
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
        if abort_event is not None and abort_event.is_set():
            return json.dumps({"ok": False, "error": "操作已被用户取消"}, ensure_ascii=False)

        if pass_session_prefix is None:
            pass_session_prefix = os.environ.get("HERMES_PASS_SESSION_ID", "0").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        sid = _norm(session_id)
        base_instruction = instruction
        if sid and pass_session_prefix:
            instruction = f"[session_id={sid}]\n\n{instruction}"

        def _run_once(instr: str) -> str:
            result_holder: Dict[str, Any] = {"text": None, "error": None}
            sys_override = _norm(system_prompt)

            def _call():
                try:
                    result_holder["text"] = self._chat_completions(instr, system_prompt=sys_override)
                except Exception as e:
                    result_holder["error"] = e

            t = threading.Thread(target=_call, daemon=True)
            t.start()
            while t.is_alive():
                t.join(timeout=0.2)
                if abort_event is not None and abort_event.is_set():
                    return json.dumps({"ok": False, "error": "操作已被用户取消"}, ensure_ascii=False)

            if result_holder["error"]:
                err = result_holder["error"]
                if isinstance(err, RequestException):
                    uat_logger.warning("Hermes Gateway 请求失败: %s", err)
                    return json.dumps({"ok": False, "error": str(err)}, ensure_ascii=False)
                err_s = str(err)
                if _is_corrupt_session_error(err_s):
                    return json.dumps(
                        {
                            "ok": False,
                            "error": _friendly_corrupt_msg(err_s),
                            "corrupt_session": True,
                        },
                        ensure_ascii=False,
                    )
                return json.dumps({"ok": False, "error": err_s}, ensure_ascii=False)

            text = _clip_tool_result(result_holder["text"], max_chars=hermes_tool_result_max_chars())
            # 偶发：HTTP 200 但正文就是异常串
            if _is_corrupt_session_error(text):
                return json.dumps(
                    {
                        "ok": False,
                        "error": _friendly_corrupt_msg(text),
                        "corrupt_session": True,
                    },
                    ensure_ascii=False,
                )
            return text

        first = _run_once(instruction)
        # 会话损坏：去掉 session 前缀自动重试一次
        try:
            parsed = json.loads(first)
        except Exception:
            parsed = None
        if (
            reset_session_on_corrupt
            and isinstance(parsed, dict)
            and parsed.get("corrupt_session")
        ):
            uat_logger.warning("Hermes corrupt session detected; retrying without session prefix")
            second = _run_once(base_instruction)
            try:
                p2 = json.loads(second)
                if isinstance(p2, dict):
                    p2["retried_without_session"] = True
                    return json.dumps(p2, ensure_ascii=False)
            except Exception:
                pass
            return second
        return first

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _default_system_prompt(self) -> str:
        return (
            "你是 Testory 跨层自动化执行代理（用户的手和眼睛）。"
            "可使用浏览器 CDP、桌面 gateway（UIA）、移动 bridge、接口 HTTP；"
            "优先结构化感知，视觉用于确认与弱控件界面。"
            "Web 流程中的 OS 弹窗请切桌面工具处理。"
            "在回复中给出可复现为自动化步骤的摘要（含 URL/窗口标题/选择器或可见文案/接口状态/检查点）。"
            "需要人工介入时输出 NEED_USER_ACTION:<原因>。"
            "高风险破坏性操作前先只读确认。"
        )

    def _chat_completions(self, instruction: str, system_prompt: str = "") -> str:
        url = f"{self.base_url}/v1/chat/completions"
        messages: List[Dict[str, str]] = []
        sys_p = _norm(system_prompt) or _norm(os.environ.get("HERMES_EXECUTE_SYSTEM_PROMPT", ""))
        if not sys_p:
            sys_p = self._default_system_prompt()
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
        raw_text = resp.content.decode('utf-8', errors='replace')
        data = json.loads(raw_text) if raw_text.strip() else {}
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
        if not self.base_url:
            return False
        # 启动探测前对齐 HERMES_HOME/.env 中的 API_SERVER_KEY，避免占位符被错误替换导致假超时
        try:
            from hermes_config import resolve_hermes_api_server_key

            synced = resolve_hermes_api_server_key()
            if synced and synced != self.token:
                self.token = synced
        except Exception:
            pass
        try:
            resp = requests.get(
                f"{self.base_url}/v1/models",
                headers=self._headers(),
                timeout=timeout_sec,
            )
            if resp.ok:
                return True
            # 401：无空 token 再试一次（部分部署允许匿名 /v1/models）
            if resp.status_code == 401:
                try:
                    resp = requests.get(
                        f"{self.base_url}/v1/models",
                        timeout=min(timeout_sec, 1.0),
                    )
                    return resp.ok
                except RequestException:
                    return False
            return False
        except RequestException:
            try:
                resp = requests.get(
                    f"{self.base_url}/v1/models",
                    timeout=min(timeout_sec, 1.0),
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
