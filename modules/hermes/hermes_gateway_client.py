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
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests
from requests.exceptions import RequestException

from modules.core.logger import uat_logger


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


def _abort_error_message(abort_event) -> str:
    """区分超时 / 工具死循环 / 用户真取消，禁止一律报「用户取消」。"""
    if abort_event is None:
        return "操作已被用户取消"
    if getattr(abort_event, "_timed_out", False):
        return "任务已超过设定的超时时间，已自动停止"
    reason = str(getattr(abort_event, "_abort_reason", "") or "").strip()
    if reason == "tool_loop":
        return "智能体因工具死循环已中止（非用户取消）"
    if reason == "timeout":
        return "任务已超过设定的超时时间，已自动停止"
    return "操作已被用户取消"


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
                    "error": "智能体未就绪：请先在页面点击「启动智能体」",
                },
                ensure_ascii=False,
            )
        if abort_event is not None and abort_event.is_set():
            return json.dumps(
                {"ok": False, "error": _abort_error_message(abort_event)},
                ensure_ascii=False,
            )

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
                    return json.dumps(
                        {"ok": False, "error": _abort_error_message(abort_event)},
                        ensure_ascii=False,
                    )

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
            "【最高优先级】你是 Testory 跨层自动化执行代理。"
            "网页：CDP 已 attach；若不在目标页则直接 browser_navigate 到任务 URL（禁止 about:blank）；"
            "按用户指令逐步执行，不要跳步；"
            "优先用指令内 DOM 控件清单 click/type；"
            "有 DOM 清单时直接操作，不要先调 browser_snapshot；"
            "browser_snapshot 是 DOM/a11y ref（非截图）：仅难定位时兜底（全程最多 2 次，禁止连续反复）；视觉仅兜底。"
            "禁止 skill_view / terminal。"
            "Windows 桌面优先 MCP windows_* / get_screen_*。"
            "未核验勿声称已完成。同一工具连续无进展超过 2 次必须换策略（如 browser_console），"
            "禁止因短信验证码请用户手填；仅图形验证码/滑块/扫码才 NEED_USER_ACTION。"
        )

    def _build_chat_payload(
        self, instruction: str, system_prompt: str = "", *, stream: bool = False
    ) -> Dict[str, Any]:
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
            "stream": bool(stream),
        }
        try:
            mt = int(os.environ.get("HERMES_CHAT_MAX_TOKENS", "0") or 0)
            if mt > 0:
                payload["max_tokens"] = min(mt, 128000)
        except ValueError:
            pass
        return payload

    def _chat_completions(self, instruction: str, system_prompt: str = "") -> str:
        url = f"{self.base_url}/v1/chat/completions"
        payload = self._build_chat_payload(instruction, system_prompt, stream=False)
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

    def execute_user_instruction_stream(
        self,
        instruction: str,
        session_id: str = "",
        abort_event=None,
        *,
        system_prompt: str = "",
    ) -> Iterator[Tuple[str, Dict[str, Any]]]:
        """流式执行：yield ('trace'|'delta'|'result'|'error', payload)。

        优先 OpenAI SSE；若不支持 stream 则退化为分块推送最终文本轨迹。
        """
        instruction = _norm(instruction)
        if not instruction:
            yield ("error", {"error": "instruction 为空"})
            return
        if not self.is_configured():
            yield (
                "error",
                {"error": "智能体未就绪：请先在页面点击「启动智能体」"},
            )
            return
        if abort_event is not None and abort_event.is_set():
            yield ("error", {"error": _abort_error_message(abort_event)})
            return

        url = f"{self.base_url}/v1/chat/completions"
        payload = self._build_chat_payload(instruction, system_prompt, stream=True)
        yield ("trace", {"stage": "start", "message": "Hermes 开始执行…"})
        try:
            resp = requests.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
                stream=True,
            )
        except RequestException as e:
            yield ("error", {"error": str(e)})
            return
        if not resp.ok:
            # 部分部署不支持 stream：回退非流式
            try:
                text = self.execute_user_instruction(
                    instruction, session_id, abort_event=abort_event, system_prompt=system_prompt
                )
                for ev in _trace_chunks_from_text(text):
                    yield ev
                yield ("result", {"content": text})
            except Exception as e:
                yield ("error", {"error": _http_error_detail(resp) if resp is not None else str(e)})
            return

        buf: List[str] = []
        saw_tool_activity = False
        tool_events: List[Dict[str, Any]] = []
        sse_event = ""
        try:
            for raw_line in resp.iter_lines(decode_unicode=True):
                if abort_event is not None and abort_event.is_set():
                    yield ("error", {"error": _abort_error_message(abort_event)})
                    return
                if raw_line is None:
                    continue
                line = raw_line.strip() if isinstance(raw_line, str) else str(raw_line).strip()
                if not line:
                    # SSE 空行结束上一事件；勿在 event: 与 data: 之间误清（部分实现会插空行）
                    continue
                # SSE 命名事件：event: hermes.tool.progress
                if line.lower().startswith("event:"):
                    sse_event = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line in ("[DONE]", "done"):
                    break
                try:
                    data = json.loads(line)
                except Exception:
                    continue

                # 官方 Chat Completions 自定义工具进度 / session chat/stream
                if sse_event in (
                    "hermes.tool.progress",
                    "tool.started",
                    "tool.completed",
                    "tool.progress",
                ) or (
                    isinstance(data, dict)
                    and (
                        data.get("type")
                        in ("hermes.tool.progress", "tool.started", "tool.completed")
                        or data.get("event")
                        in ("hermes.tool.progress", "tool.started", "tool.completed")
                    )
                ):
                    saw_tool_activity = True
                    te = _normalize_hermes_tool_progress(data, sse_event=sse_event)
                    if te:
                        tool_events.append(te)
                        yield ("tool", te)
                        # 勿 **te 覆盖 stage/message（te 可能含同名键）
                        yield (
                            "trace",
                            {
                                "stage": "tool_progress",
                                "message": (
                                    te.get("summary") or te.get("name") or "tool"
                                )[:240],
                                "tool": te.get("name"),
                                "tool_status": te.get("status"),
                                "sse_event": te.get("sse_event") or sse_event,
                            },
                        )
                    sse_event = ""
                    continue

                choices = data.get("choices") or []
                if not choices or not isinstance(choices[0], dict):
                    if isinstance(data, dict) and (data.get("tool") or data.get("name")):
                        saw_tool_activity = True
                        te = _normalize_hermes_tool_progress(
                            data, sse_event=sse_event or "tool"
                        )
                        if te:
                            tool_events.append(te)
                            yield ("tool", te)
                            yield (
                                "trace",
                                {
                                    "stage": "tool_progress",
                                    "message": (
                                        te.get("summary") or te.get("name") or "tool"
                                    )[:240],
                                    "tool": te.get("name"),
                                    "tool_status": te.get("status"),
                                    "sse_event": te.get("sse_event") or sse_event,
                                },
                            )
                        sse_event = ""
                    continue
                ch0 = choices[0]
                delta = ch0.get("delta") or {}
                msg = ch0.get("message") or {}
                piece = delta.get("content") or msg.get("content") or ""
                tool_calls = delta.get("tool_calls") or ch0.get("tool_calls") or msg.get("tool_calls")
                if tool_calls:
                    saw_tool_activity = True
                    # 仅标记有工具活动 + 思考轨迹；完整 args 多在后续 progress/completed
                    # 避免把每个 tool_calls delta 半截写入 action 卡
                    yield ("trace", {"stage": "tool", "tool_calls": tool_calls})
                    for tc in tool_calls if isinstance(tool_calls, list) else [tool_calls]:
                        te = _tool_call_delta_to_event(tc)
                        if te and (te.get("args") or te.get("name") not in ("", "tool")):
                            te_name = str(te.get("name") or "").strip()
                            # 平台工具（browser_*/windows_*/mobile_*）的 tool_calls delta
                            # 必须加入 tool_events，否则浏览器步骤永远不会被记录到实时用例
                            is_platform_tool = (
                                te_name.startswith("browser_")
                                or te_name.startswith("windows_")
                                or te_name.startswith("mobile_")
                                or te_name in ("navigate", "goto", "click", "type", "snapshot", "scroll",
                                               "open_app", "tap", "input_text", "swipe", "extract_otp",
                                               "launch_app", "focus_app", "press_key", "screenshot")
                            )
                            if is_platform_tool and te.get("args"):
                                # 合并 delta：优先 call_id，其次同名 running；避免多次 click 互相覆盖
                                _existing = None
                                _cid = str(te.get("call_id") or "").strip()
                                if _cid:
                                    for _e in tool_events:
                                        if str(_e.get("call_id") or "").strip() == _cid:
                                            _existing = _e
                                            break
                                if _existing is None:
                                    for _e in tool_events:
                                        if _e.get("name") != te_name:
                                            continue
                                        _st = str(_e.get("status") or "").lower()
                                        if _st in ("running", "in_progress", "started", "progress", ""):
                                            _existing = _e
                                            break
                                if _existing:
                                    _ea = _existing.get("args") if isinstance(_existing.get("args"), dict) else {}
                                    _na = te.get("args") if isinstance(te.get("args"), dict) else {}
                                    _merged_args = dict(_ea)
                                    _merged_args.update(_na)
                                    _existing["args"] = _merged_args
                                    if _cid:
                                        _existing["call_id"] = _cid
                                    if not _existing.get("result"):
                                        _existing["result"] = {"ok": True}
                                    if not _existing.get("status") or _existing["status"] == "running":
                                        _existing["status"] = "completed"
                                else:
                                    te["status"] = "completed"
                                    te["result"] = te.get("result") or {"ok": True}
                                    tool_events.append(te)
                            # 有函数名时进 traces 用的轻量事件
                            yield ("tool", te)
                if piece:
                    buf.append(piece)
                    yield ("delta", {"text": piece})
                    if any(
                        k in piece
                        for k in (
                            "windows_",
                            "skill_view",
                            "browser_",
                            "computer_use",
                            "get_screen_",
                            "NEED_USER_ACTION",
                        )
                    ):
                        yield ("trace", {"stage": "hint", "message": piece[:240]})
                sse_event = ""
        except RequestException as e:
            yield ("error", {"error": str(e)})
            return

        if tool_events:
            yield ("tool_events", {"events": tool_events[-80:]})

        text = _clip_tool_result("".join(buf), max_chars=hermes_tool_result_max_chars())
        if not text:
            # 流式 HTTP 已成功：禁止再跑一遍非流式（会重复桌面副作用）。
            if saw_tool_activity or tool_events:
                text = json.dumps(
                    {
                        "ok": True,
                        "partial": True,
                        "stream_empty_text": True,
                        "had_tool_activity": True,
                        "tool_count": len(tool_events),
                        "reply": (
                            "Hermes 流式会话已结束（有工具活动但无文本摘要）。"
                            "请结合逐步工具轨迹确认结果；未再重跑指令。"
                        ),
                    },
                    ensure_ascii=False,
                )
                yield (
                    "trace",
                    {
                        "stage": "stream_empty",
                        "message": f"流无文本摘要，但已收到 {len(tool_events)} 条工具事件（未重跑）",
                    },
                )
            else:
                text = json.dumps(
                    {
                        "ok": False,
                        "stream_empty_text": True,
                        "had_tool_activity": False,
                        "error": (
                            "Hermes 流式返回为空且未见工具轨迹。"
                            "常见原因：上游大模型鉴权失败或仍指向错误 Provider（如默认 OpenRouter）；"
                            "请确认前端所选引擎已同步到智能体（config.yaml model），"
                            "并检查 computer_use / 桌面 MCP 是否就绪后重试。"
                        ),
                    },
                    ensure_ascii=False,
                )
                yield (
                    "trace",
                    {
                        "stage": "stream_empty",
                        "message": "空流且无工具活动（未重跑）",
                    },
                )
        yield ("result", {"content": text, "tool_events": tool_events[-80:]})

    def health_check(self, timeout_sec: float = 2.5) -> bool:
        if not self.base_url:
            return False
        # 启动探测前对齐 HERMES_HOME/.env 中的 API_SERVER_KEY，避免占位符被错误替换导致假超时
        try:
            from modules.hermes.hermes_config import resolve_hermes_api_server_key

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
        low = body.lower()
        if "insufficient balance" in low or ("402" in low and "balance" in low):
            return (
                "upstream_balance: 当前大模型账户余额不足（HTTP "
                f"{resp.status_code}）。请更换模型或充值后，停止并重新启动智能体。"
            )
        if "missing authentication header" in low or (
            resp.status_code == 401 and ("auth" in low or "unauthorized" in low)
        ):
            return (
                "upstream_auth: 上游大模型鉴权失败（HTTP "
                f"{resp.status_code}）。请检查模型配置中的 API Key 后，停止并重新启动智能体。"
            )
        return f"HTTP {resp.status_code}" + (f": {body}" if body else "")
    except Exception:
        return f"HTTP {resp.status_code}"


def _clip_tool_result(text: str, *, max_chars: Optional[int] = None) -> str:
    cap = int(max_chars) if max_chars is not None else hermes_tool_result_max_chars()
    cap = max(4000, min(cap, 200000))
    if len(text) <= cap:
        return text
    return text[: cap - 80] + "\n…(truncated for context limit)…"


def _trace_chunks_from_text(text: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """把最终长文本拆成可见轨迹事件（无真实 SSE 时的降级）。"""
    raw = (text or "").strip()
    if not raw:
        return
    yield ("trace", {"stage": "summary", "message": "Hermes 返回执行摘要（非逐 token 流）…"})
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(
            k in s
            for k in (
                "windows_",
                "skill_view",
                "browser_",
                "computer_use",
                "get_screen_",
                "attach_window",
                "launch_app",
                "NEED_USER_ACTION",
                "步骤",
                "点击",
                "输入",
            )
        ):
            yield ("trace", {"stage": "line", "message": s[:240]})


def _normalize_hermes_tool_progress(
    data: Dict[str, Any], *, sse_event: str = ""
) -> Optional[Dict[str, Any]]:
    """统一 hermes.tool.progress / tool.started / 顶层 tool 字段。"""
    if not isinstance(data, dict):
        return None
    fn = data.get("function") if isinstance(data.get("function"), dict) else {}
    name = data.get("name") or data.get("tool") or data.get("tool_name") or fn.get("name")
    if not name and isinstance(data.get("item"), dict):
        name = data["item"].get("name") or data["item"].get("type")
    name = _norm(name) or "tool"
    args = data.get("arguments") or data.get("args") or data.get("input") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {"raw": args[:200]}
    if not isinstance(args, dict):
        args = {}
    status = _norm(
        data.get("status")
        or data.get("phase")
        or data.get("state")
        or ("completed" if "completed" in (sse_event or "") else "running")
    )
    # 扩大 result 提取范围：兼容多种字段名（不同版本/不同网关实现命名不一）
    result = (
        data.get("result")
        or data.get("output")
        or data.get("content")
        or data.get("response")
        or data.get("value")
        or data.get("data")
        or data.get("reply")
    )
    # 若仍是 None，但 data 里有 ok/success 布尔，至少存一个非空结果占位，避免后续被误过滤
    if result is None:
        for _ok_key in ("ok", "success", "succeeded"):
            if _ok_key in data and data.get(_ok_key) is not None:
                result = {"ok": bool(data.get(_ok_key) is not False)}
                break
    summary = _norm(data.get("message") or data.get("summary") or "")
    if not summary:
        act = args.get("action") or args.get("app") or args.get("text") or args.get("ref") or args.get("url") or ""
        summary = f"{name}" + (f"({act})" if act else "")
    call_id = _norm(
        data.get("call_id")
        or data.get("id")
        or data.get("tool_call_id")
        or data.get("toolCallId")
        or ""
    )
    out = {
        "name": name,
        "args": args,
        "status": status,
        "result": result,
        "summary": summary[:240],
        "sse_event": sse_event or "",
    }
    if call_id:
        out["call_id"] = call_id
    return out


def _tool_call_delta_to_event(tc: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(tc, dict):
        return None
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    name = _norm(fn.get("name") or tc.get("name") or "tool")
    raw_args = fn.get("arguments") if fn else tc.get("arguments")
    args: Dict[str, Any] = {}
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            args = json.loads(raw_args)
        except Exception:
            args = {"raw": raw_args[:200]}
    elif isinstance(raw_args, dict):
        args = raw_args
    call_id = _norm(tc.get("id") or tc.get("tool_call_id") or "")
    return {
        "name": name,
        "args": args,
        "status": "running",
        "summary": name,
        "sse_event": "tool_calls_delta",
        "call_id": call_id,
    }


def _args_richness(args: Any) -> int:
    if not isinstance(args, dict) or not args:
        return 0
    score = 0
    for k, v in args.items():
        if v is None or v == "" or v == {}:
            continue
        if k == "raw":
            score += 1
            continue
        score += 2 + min(len(str(v)), 40) // 10
    return score


def merge_hermes_tool_events(tool_evs: Any) -> List[Dict[str, Any]]:
    """合并同一工具调用的 args（delta）与 result（completed）。

    根因：completed 事件常只带 result/ok，args 留在先前 running delta；
    若按 name+args 分桶，空 args 的 completed 会单独成条并落成工具名占位步骤。
    """
    if not isinstance(tool_evs, list):
        return []
    pending_by_id: Dict[str, Dict[str, Any]] = {}
    pending_by_name: Dict[str, List[Dict[str, Any]]] = {}
    out: List[Dict[str, Any]] = []

    def _merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(a)
        merged.update({k: v for k, v in b.items() if v is not None and v != ""})
        a_args = a.get("args") if isinstance(a.get("args"), dict) else {}
        b_args = b.get("args") if isinstance(b.get("args"), dict) else {}
        if _args_richness(b_args) >= _args_richness(a_args):
            merged["args"] = dict(a_args)
            merged["args"].update(b_args)
        else:
            merged["args"] = dict(b_args)
            merged["args"].update(a_args)
        if b.get("result") is not None:
            merged["result"] = b.get("result")
        elif a.get("result") is not None:
            merged["result"] = a.get("result")
        cid = _norm(a.get("call_id") or b.get("call_id") or "")
        if cid:
            merged["call_id"] = cid
        return merged

    for te in tool_evs:
        if not isinstance(te, dict):
            continue
        name = _norm(te.get("name") or "tool") or "tool"
        args = te.get("args") if isinstance(te.get("args"), dict) else {}
        call_id = _norm(te.get("call_id") or te.get("id") or te.get("tool_call_id") or "")
        ts = _norm(te.get("status") or "").lower()
        has_result = te.get("result") is not None
        is_done = ts in (
            "completed", "success", "succeeded", "done", "finished", "failed", "error", "fail"
        ) or has_result
        has_args = _args_richness(args) > 0

        if call_id and call_id in pending_by_id and is_done:
            base = pending_by_id.pop(call_id)
            out.append(_merge(base, te))
            continue
        if is_done:
            queue = pending_by_name.get(name) or []
            if queue:
                base = queue.pop(0)
                out.append(_merge(base, te))
                continue
            if call_id and call_id in pending_by_id:
                base = pending_by_id.pop(call_id)
                out.append(_merge(base, te))
                continue
            out.append(dict(te))
            continue
        # running / 仅有 args：入队等待 completed
        if has_args or call_id:
            item = dict(te)
            if call_id:
                pending_by_id[call_id] = item
            pending_by_name.setdefault(name, []).append(item)

    # 残留 pending：有有效 args 的仍保留（后续 capture 可凭 args 落库）
    for cid, item in list(pending_by_id.items()):
        if item not in out:
            out.append(item)
    for _name, queue in pending_by_name.items():
        for item in queue:
            if item not in out and item not in pending_by_id.values():
                # 已按 call_id 加入的不再重复
                already = False
                cid = _norm(item.get("call_id") or "")
                if cid and any(_norm(x.get("call_id") or "") == cid for x in out):
                    already = True
                if not already:
                    out.append(item)
    return out

