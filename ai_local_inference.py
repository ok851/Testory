import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.exceptions import RequestException

from ai_page_probe import (
    build_locator_candidates_from_probe_entry,
    extract_http_urls,
    validate_plan_locators,
)

_log = logging.getLogger(__name__)


def _norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _strip_markdown_code_fence(text: str) -> str:
    """Remove leading/trailing ``` or ```json fences often added by chat models."""
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _strip_llm_noise(text: str) -> str:
    """去掉零宽字符与部分模型包裹的推理块（避免在源码中写入易被工具误处理的长标签名）。"""
    s = (text or "").strip().replace("\ufeff", "")
    if not s:
        return s
    # 常见推理标签（拆成 chr 拼接，避免静态扫描误伤）
    _ot = chr(60) + chr(116) + chr(104) + chr(105) + chr(110) + chr(107) + chr(62)
    _ct = chr(60) + chr(47) + chr(116) + chr(104) + chr(105) + chr(110) + chr(107) + chr(62)
    pat_think = re.escape(_ot) + r"[\s\S]*?" + re.escape(_ct)
    noise_patterns = (
        pat_think,
        r"<reasoning\b[^>]*>[\s\S]*?</reasoning>",
    )
    for _ in range(6):
        prev = s
        for pat in noise_patterns:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
        if s == prev:
            break
    return s.strip()


def _normalize_smart_quotes_for_json(text: str) -> str:
    """部分中文模型会输出弯引号，尝试替换为 ASCII 引号后再解析。"""
    s = text or ""
    return (
        s.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def _extract_balanced_json_object(text: str) -> Optional[str]:
    """First top-level `{ ... }` span; respects double-quoted JSON strings."""
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escape = False
        i = start
        while i < len(text):
            c = text[i]
            if escape:
                escape = False
                i += 1
                continue
            if in_string:
                if c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
                i += 1
                continue
            if c == '"':
                in_string = True
                i += 1
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
            i += 1
        start = text.find("{", start + 1)
    return None


def _trim_leading_until_first_brace(text: str) -> str:
    s = (text or "").strip()
    i = s.find("{")
    return s[i:] if i >= 0 else s


def _repair_json_trailing_commas(text: str) -> str:
    s = text or ""
    for _ in range(32):
        s2 = re.sub(r",(\s*[}\]])", r"\1", s)
        if s2 == s:
            break
        s = s2
    return s


def _fence_extract_blocks(text: str) -> List[str]:
    if not (text or "").strip():
        return []
    out: List[str] = []
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.I):
        inner = (m.group(1) or "").strip()
        if inner:
            out.append(inner)
    return out


def _deep_json_parse_string(val: Any) -> Any:
    """若值为可被 json.loads 的字符串（含多重字符串包裹），尽量剥到 dict/list。"""
    cur: Any = val
    for _ in range(5):
        if not isinstance(cur, str):
            return cur
        s = cur.strip()
        if not s:
            return cur
        try:
            cur = json.loads(s)
        except json.JSONDecodeError:
            return cur
    return cur


def _coerce_plan_root(val: Any) -> Optional[Dict[str, Any]]:
    val = _deep_json_parse_string(val)
    if isinstance(val, dict):
        return val
    if isinstance(val, list):
        if not val:
            return None
        if not all(isinstance(x, dict) for x in val):
            return None
        if val and _norm_str(val[0].get("action")):
            return {
                "case_name": "",
                "case_url": "",
                "description": "",
                "precondition": "",
                "expected_result": "",
                "steps": val,
            }
        for x in val:
            if isinstance(x, dict) and isinstance(x.get("steps"), list):
                return x
    return None


def _collect_json_try_strings(raw: str) -> List[str]:
    """生成一组待尝试解析的子串。"""
    s = (raw or "").strip()
    out: List[str] = []
    seen = set()

    def push(x: str) -> None:
        t = (x or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    push(s)
    for blk in _fence_extract_blocks(s):
        push(blk)
    trimmed = _trim_leading_until_first_brace(s)
    push(trimmed)
    push(_strip_markdown_code_fence(trimmed))
    push(_strip_markdown_code_fence(s))

    pos = 0
    while pos < len(s):
        j = s.find("{", pos)
        if j < 0:
            break
        frag = _extract_balanced_json_object(s[j:])
        if frag:
            push(frag)
            pos = j + max(len(frag), 1)
        else:
            pos = j + 1
    return out


def _infer_input_value_for_input_action(description: str, goal: str) -> str:
    """
    When the model leaves input_value empty for action=input, recover text from
    description (e.g. 「例如：自动化测试」) or goal (e.g. 在百度搜索自动化测试).
    """
    desc = _norm_str(description)
    goal = _norm_str(goal)
    # （例如：自动化测试）
    m = re.search(r"（\s*(?:例如|比如|如)\s*[：:]\s*([^）]+)）", desc)
    if m:
        return _norm_str(m.group(1))
    # (e.g. foo)
    m = re.search(r"\(\s*(?:e\.g\.|eg\.)\s*[：:]?\s*([^)]+)\)", desc, re.I)
    if m:
        return _norm_str(m.group(1))
    # 例如：xxx / 比如：xxx（行内，不含括号包裹）
    m = re.search(r"(?:例如|比如|如)\s*[：:]\s*([^\s）\)\n。,，;；]+)", desc)
    if m:
        return _norm_str(m.group(1))
    # 「关键词」
    m = re.search(r"「([^」]{1,200})」", desc)
    if m:
        return _norm_str(m.group(1))
    for text in (goal, desc):
        if not text:
            continue
        m = re.search(r"搜索\s*[「\"]?([^」\"\n]{1,120}?)(?:\s|$|的|，|。|？|；|；)", text)
        if m:
            cand = _norm_str(m.group(1))
            if cand and len(cand) < 220:
                return cand
        m = re.search(r"搜索\s*([^\s，。,；;\n]{1,120})", text)
        if m:
            cand = _norm_str(m.group(1))
            if cand and len(cand) < 220:
                return cand
    return ""


def _infer_input_value_for_verify_action(description: str) -> str:
    """When verify expects text but input_value is empty, try description."""
    desc = _norm_str(description)
    if not desc:
        return ""
    m = re.search(
        r"(?:预期|应|验证|断言|检查).{0,20}?(?:包含|含有|出现|显示|为)\s*[「」]?([^」\n。]{1,120})",
        desc,
    )
    if m:
        return _norm_str(m.group(1)).strip("「」\"'")
    m = re.search(r"包含\s*[「」]?([^」\n。]{1,120})", desc)
    if m:
        return _norm_str(m.group(1)).strip("「」\"'")
    return ""


def _first_http_url(*parts: str) -> str:
    for p in parts:
        for u in extract_http_urls(p or ""):
            u = u.rstrip(").,]}>'\"")
            if u.startswith("http://") or u.startswith("https://"):
                return u.split()[0]
    return ""


def _infer_navigate_url(goal: str, description: str, case_url: str) -> str:
    return _first_http_url(case_url, goal, description)


def _infer_wait_input_value(description: str) -> str:
    """Seconds 1–120 or milliseconds; default a small wait if model left empty."""
    desc = _norm_str(description)
    if not desc:
        return "3"
    m = re.search(r"(\d{1,6})\s*(?:毫秒|ms)\b", desc, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)\s*(?:秒|秒钟)(?!\s*毫秒)", desc)
    if m:
        sec = int(m.group(1))
        return str(min(120, max(1, sec)))
    m = re.search(r"(\d+)\s*[-~～]\s*(\d+)\s*秒", desc)
    if m:
        lo = int(m.group(1))
        return str(min(120, max(1, lo)))
    m = re.search(r"(?:等待|延时|停顿)\s*(\d{1,4})", desc)
    if m:
        v = int(m.group(1))
        if v > 120:
            return str(v)
        return str(max(1, v))
    return "3"


def _extract_selector_from_description(description: str) -> Tuple[str, str]:
    """Parse css:/xpath:/裸 #id from model text."""
    desc = _norm_str(description)
    if not desc:
        return "", ""
    m = re.search(r"(?:css|CSS)[:：]\s*([^\s|，。\n]+)", desc)
    if m:
        cap = m.group(1).strip()
        # 模型常把快照行号误写成 css:12；纯数字不是合法 CSS，忽略以免污染 selector_value
        if cap.isdigit():
            return "", ""
        return "css", cap
    m = re.search(r"(?:xpath|XPath)[:：]\s*([^\n]+)", desc)
    if m:
        return "xpath", m.group(1).strip()[:800]
    m = re.search(r"(?<![\w#])(\#[\w\-]{1,120})\b", desc)
    if m:
        return "css", m.group(1)
    m = re.search(r"\b(id|name)\s*[:=]\s*([\w\-]{1,120})", desc, re.I)
    if m:
        key, val = m.group(1).lower(), m.group(2)
        if key == "id":
            return "css", f"#{val}"
        return "css", f'[{key}="{val}"]'
    return "", ""


def _map_probe_selector_type(rty: str) -> str:
    rty = (rty or "").strip().lower()
    if rty in ("css", "xpath", "text"):
        return rty
    if rty == "partial_text":
        return "text"
    return "css"


def _probe_pick_selector(
    description: str,
    registry: Optional[List[Dict[str, Any]]],
    action: str,
) -> Tuple[str, str]:
    """Match probe registry row to description keywords (click vs input)."""
    if not registry or not isinstance(registry, list):
        return "", ""
    desc = _norm_str(description)
    if not desc:
        return "", ""
    prefer_in = action == "input"
    prefer_click = action in ("click", "extract_text")
    prefer_verify = action == "verify"
    best: Tuple[int, str, str] = (-1, "", "")
    threshold = 8 if prefer_verify else 5

    for ent in registry:
        if not isinstance(ent, dict):
            continue
        rec = _norm_str(ent.get("recommended_selector"))
        rty = _map_probe_selector_type(_norm_str(ent.get("recommended_selector_type")))
        if not rec:
            continue
        tag = _norm_str(ent.get("tag")).lower()
        typ = _norm_str(ent.get("typ")).lower()
        txt = _norm_str(ent.get("txt"))
        al = _norm_str(ent.get("al"))
        ph = _norm_str(ent.get("ph"))
        rid = _norm_str(ent.get("rid")).lower()
        blob = f"{txt} {al} {ph}".lower()
        score = 0
        if prefer_verify:
            if any(k in desc for k in ("标题", "结果", "页面", "包含", "验证")):
                rl = rec.lower()
                if any(x in rl for x in ("result", "title", "c-container", "content_left")):
                    score += 12
                if tag in ("h3", "a", "div"):
                    score += 2
        elif prefer_in:
            if tag in ("input", "textarea") or rid in ("textbox", "searchbox", "combobox"):
                score += 4
            if typ in ("text", "search", "textarea", ""):
                score += 1
            if any(k in desc for k in ("搜索", "关键词", "输入", "填写")) and (
                "搜" in blob or "search" in blob or ph or tag == "textarea"
            ):
                score += 8
        elif prefer_click:
            if tag in ("button", "a", "input") or rid == "button":
                score += 3
            if typ == "submit":
                score += 5
            if any(k in desc for k in ("点击", "按钮", "提交", "搜索")) and (
                "搜索" in desc
                or "百度" in desc
                or "提交" in blob
                or "搜" in blob
                or typ == "submit"
            ):
                score += 7
            if "按钮" in desc and tag == "button":
                score += 4
        if score > best[0]:
            best = (score, rty, rec)

    if best[0] >= threshold:
        return best[1], best[2]
    return "", ""


def _fallback_site_selectors(case_url: str, description: str, action: str) -> Tuple[str, str]:
    """Known baidu.com layouts when model omits selectors."""
    u = (case_url or "").lower()
    d = _norm_str(description)
    if "baidu.com" not in u or not d:
        return "", ""
    if action == "input" and any(k in d for k in ("搜索", "关键词", "输入")):
        if any(k in d for k in ("对话", "chat", "Chat", "AI")):
            return "css", "#chat-textarea"
        return "css", "#kw"
    if action == "click" and any(k in d for k in ("搜索", "提交", "百度一下")):
        if any(k in d for k in ("对话", "chat", "AI")):
            return "css", "#chat-submit-button"
        return "css", "#su"
    if action == "extract_text" and "结果" in d:
        return "css", ".result"
    if action == "verify" and any(k in d for k in ("结果", "标题", "搜索")):
        return "css", ".result-title"
    return "", ""


class LocalAIService:
    """
    Local LLM inference service (Ollama-compatible by default).
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.model_light = os.environ.get("LOCAL_LLM_MODEL_LIGHT", "qwen2:1.5b")
        self.model_mid = os.environ.get("LOCAL_LLM_MODEL_MID", "llama3:8b-instruct")
        self.timeout = int(os.environ.get("LOCAL_LLM_TIMEOUT", "240"))

    def list_installed_models(self, timeout: int = 8) -> Tuple[List[str], Optional[str]]:
        """
        Query Ollama-compatible /api/tags. Returns (model_names, error_message).
        """
        url = f"{self.base_url}/api/tags"
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
        except RequestException as e:
            return [], f"无法连接本地模型服务（{self.base_url}）：{e}"
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            return [], "本地模型服务返回了非 JSON 响应"
        raw_models = data.get("models")
        if not isinstance(raw_models, list):
            return [], None
        names: List[str] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            n = _norm_str(item.get("name"))
            if n:
                names.append(n)
        names = sorted(set(names))
        return names, None

    def generate_case_and_steps(
        self,
        goal: str,
        project_name: str = "",
        model: str = "",
        profile: Optional[Dict[str, Any]] = None,
        page_snapshot: Optional[str] = None,
        probe_registry: Optional[List[Dict[str, Any]]] = None,
        probe_url: Optional[str] = None,
        memory_context: Optional[str] = None,
        dom_context_pack: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap_t = (page_snapshot or "").strip()
        pr: List[Dict[str, Any]] = list(probe_registry) if probe_registry else []
        pu = (probe_url or "").strip() or None
        dom_t = (dom_context_pack or "").strip()
        if dom_t:
            dom_t = self._maybe_compress_dom_pack(dom_t, profile, model)
        prompt = self._build_prompt(
            goal,
            project_name,
            page_snapshot=snap_t,
            memory_context=(memory_context or "").strip() or None,
            dom_context_pack=dom_t or None,
        )
        using_model, content = self._complete_for_model(
            prompt, model, profile, meta_fallback=self.model_mid
        )
        parsed = self._parse_plan_with_plain_retry(content, prompt=prompt, model=model, profile=profile)
        out = self._normalize_output(
            parsed, goal, project_name, using_model, probe_registry=pr if pr else None
        )
        meta = out.setdefault("meta", {})
        if profile and isinstance(profile, dict):
            meta["provider"] = profile.get("provider") or "cloud"
            meta["profile_id"] = profile.get("id") or ""
            meta["model"] = using_model
        else:
            meta["provider"] = "local"
        self._attach_locator_validation(meta, pu, out.get("steps") or [])
        return out

    def refine_case_and_steps(
        self,
        user_message: str,
        project_name: str = "",
        current_plan: Dict[str, Any] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        model: str = "",
        profile: Optional[Dict[str, Any]] = None,
        page_snapshot: Optional[str] = None,
        probe_registry: Optional[List[Dict[str, Any]]] = None,
        probe_url: Optional[str] = None,
        memory_context: Optional[str] = None,
        dom_context_pack: Optional[str] = None,
        interaction_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snap_t = (page_snapshot or "").strip()
        pr: List[Dict[str, Any]] = list(probe_registry) if probe_registry else []
        pu = (probe_url or "").strip() or None
        dom_t = (dom_context_pack or "").strip()
        if dom_t:
            dom_t = self._maybe_compress_dom_pack(dom_t, profile, model)
        prompt = self._build_refine_prompt(
            user_message=user_message,
            project_name=project_name,
            current_plan=current_plan or {},
            history=history or [],
            page_snapshot=snap_t,
            memory_context=(memory_context or "").strip() or None,
            dom_context_pack=dom_t or None,
            interaction_context=interaction_context,
        )
        using_model, content = self._complete_for_model(
            prompt, model, profile, meta_fallback=self.model_mid
        )
        parsed = self._parse_plan_with_plain_retry(content, prompt=prompt, model=model, profile=profile)
        out = self._normalize_output(
            parsed, user_message, project_name, using_model, probe_registry=pr if pr else None
        )
        meta = out.setdefault("meta", {})
        if profile and isinstance(profile, dict):
            meta["provider"] = profile.get("provider") or "cloud"
            meta["profile_id"] = profile.get("id") or ""
            meta["model"] = using_model
        else:
            meta["provider"] = "local"
        self._attach_locator_validation(meta, pu, out.get("steps") or [])
        return out

    def _complete_for_model(
        self,
        prompt: str,
        model: str,
        profile: Optional[Dict[str, Any]],
        meta_fallback: str,
    ) -> Tuple[str, str]:
        if profile and isinstance(profile, dict):
            from ai_multi_provider import dispatch_chat

            mid = (profile.get("model_id") or "").strip() or meta_fallback
            label = (profile.get("label") or "").strip()
            using_model = label or mid
            content = dispatch_chat(prompt, profile, self)
            return using_model, content
        using_model = (model or "").strip() or meta_fallback
        content = self._chat_completion(prompt, using_model)
        return using_model, content

    def _parse_plan_with_plain_retry(
        self,
        content: str,
        *,
        prompt: str,
        model: str,
        profile: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            return self._parse_json_response(content)
        except ValueError as first_err:
            if profile or os.environ.get("LOCAL_LLM_JSON_RETRY_PLAIN", "1").strip().lower() in (
                "0",
                "false",
                "no",
                "off",
            ):
                raise
            mid = (model or "").strip() or self.model_mid
            _log.warning("AI 用例 JSON 解析失败，将在不使用 Ollama format=json 的情况下重试一次（model=%s）", mid)
            try:
                content2 = self._chat_completion(prompt, mid, json_format=False)
                return self._parse_json_response(content2)
            except Exception:
                raise first_err

    def chat_ollama(self, prompt: str, model: str, base_url: Optional[str] = None) -> str:
        root = (base_url or "").strip().rstrip("/") or self.base_url
        return self._chat_completion_at(prompt, model, root)

    def _chat_completion(self, prompt: str, model: str, *, json_format: Optional[bool] = None) -> str:
        return self._chat_completion_at(prompt, model, self.base_url, json_format=json_format)

    def _chat_completion_at(
        self,
        prompt: str,
        model: str,
        base_url: str,
        *,
        json_format: Optional[bool] = None,
    ) -> str:
        url = f"{base_url.rstrip('/')}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior QA engineer. Output must be exactly one JSON object—nothing else. "
                        "No markdown fences, no commentary, no trailing text. "
                        "First non-whitespace character must be '{'; last must be '}'. "
                        "Schema: UI test plan with case_name, case_url, description, precondition, expected_result, steps[]."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": self._ollama_options(),
        }
        use_json_format = json_format
        if use_json_format is None:
            use_json_format = os.environ.get("LOCAL_LLM_JSON_FORMAT", "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        if use_json_format:
            payload["format"] = "json"
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except RequestException as e:
            detail = str(e).strip() or type(e).__name__
            response = getattr(e, "response", None)
            if response is not None:
                try:
                    body = (response.text or "").strip().replace("\n", " ")[:480]
                    detail = f"HTTP {response.status_code}" + (f": {body}" if body else "")
                except Exception:
                    detail = f"HTTP {response.status_code}: {detail}"
            raise ValueError(
                "本地AI服务不可用："
                f"{detail}。"
                "列表能连但生成失败时，常见为超时、模型未加载或模型名错误；"
                "请确认 ollama serve 正常、执行 ollama list 能看到所选模型，"
                "必要时增大环境变量 LOCAL_LLM_TIMEOUT，并检查 LOCAL_LLM_BASE_URL。"
            ) from e
        data = resp.json() if resp.content else {}
        return ((data.get("message") or {}).get("content") or "").strip()

    def chat_ollama_messages(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Multi-turn Ollama /api/chat with optional tool definitions (OpenAI-compatible tool schema).
        Returns assistant message dict: content (optional), tool_calls (optional).
        """
        root = (base_url or "").strip().rstrip("/") or self.base_url
        url = f"{root}/api/chat"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": self._ollama_options(),
        }
        if tools:
            payload["tools"] = tools
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except RequestException as e:
            detail = str(e).strip() or type(e).__name__
            response = getattr(e, "response", None)
            if response is not None:
                try:
                    body = (response.text or "").strip().replace("\n", " ")[:480]
                    detail = f"HTTP {response.status_code}" + (f": {body}" if body else "")
                except Exception:
                    detail = f"HTTP {response.status_code}: {detail}"
            raise ValueError(
                "本地AI服务不可用（tools）："
                f"{detail}。"
                "请确认模型支持 tool calling，必要时关闭 AI_CHAT_TOOLS_ENABLE 或换用云端 OpenAI 兼容模型。"
            ) from e
        data = resp.json() if resp.content else {}
        msg = data.get("message") or {}
        out: Dict[str, Any] = {
            "role": msg.get("role") or "assistant",
            "content": msg.get("content"),
        }
        if msg.get("tool_calls"):
            out["tool_calls"] = msg["tool_calls"]
        return out

    def _ollama_options(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {"temperature": 0.2}
        np = (os.environ.get("LOCAL_LLM_NUM_PREDICT") or "").strip()
        if np.isdigit():
            opts["num_predict"] = int(np)
        nt = (os.environ.get("LOCAL_LLM_NUM_THREAD") or "").strip()
        if nt.isdigit():
            opts["num_thread"] = int(nt)
        return opts

    def _fill_missing_step_payloads(
        self,
        steps: List[Dict[str, Any]],
        goal: str,
        case_url: str = "",
        probe_registry: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """补全模型漏填的 URL、等待时长、输入/校验文案、以及选择器（描述/探测/站点回退）。"""
        g = _norm_str(goal)
        cu = _norm_str(case_url)
        for step in steps:
            if not isinstance(step, dict):
                continue
            action = _norm_str(step.get("action")).lower()
            iv = _norm_str(step.get("input_value"))
            sv = _norm_str(step.get("selector_value"))
            desc = _norm_str(step.get("description"))

            if action == "navigate" and not iv:
                inferred = _infer_navigate_url(g, desc, cu)
                if inferred:
                    step["input_value"] = inferred
                    cu = cu or inferred

            elif action == "wait" and not iv:
                step["input_value"] = _infer_wait_input_value(desc)

            elif action == "input" and not iv:
                inferred = _infer_input_value_for_input_action(desc, g)
                if inferred:
                    step["input_value"] = inferred

            elif action == "verify" and not iv:
                inferred = _infer_input_value_for_verify_action(desc)
                if inferred:
                    step["input_value"] = inferred

            sv = _norm_str(step.get("selector_value"))

            if action in ("click", "extract_text", "verify", "input") and not sv:
                st2, sv2 = _extract_selector_from_description(desc)
                if not sv2 and probe_registry:
                    st2, sv2 = _probe_pick_selector(desc, probe_registry, action)
                if not sv2:
                    st2, sv2 = _fallback_site_selectors(cu, desc, action)
                if sv2:
                    step["selector_type"] = st2 or "css"
                    step["selector_value"] = sv2

    def _maybe_compress_dom_pack(
        self,
        text: str,
        profile: Optional[Dict[str, Any]],
        model: str,
    ) -> str:
        if (os.environ.get("LOCAL_AI_DOM_PACK_COMPRESS", "0").strip().lower() not in ("1", "true", "yes", "on")):
            return text
        try:
            min_len = int((os.environ.get("LOCAL_AI_DOM_PACK_COMPRESS_MIN_CHARS") or "12000").strip() or "12000")
        except ValueError:
            min_len = 12000
        if len(text) < min_len:
            return text
        light = (os.environ.get("LOCAL_LLM_MODEL_LIGHT") or "qwen2:1.5b").strip() or "qwen2-1.5b"
        prompt = (
            "Condense the following DOM context to at most 40 short lines. "
            "Do NOT invent controls, URLs, or probe indices. Preserve any [n] or [Region …] labels that appear.\n\n"
        ) + text[:20000]
        try:
            using_model, content = self._complete_for_model(prompt, light, profile, meta_fallback=light)
            out = (content or "").strip()
            return out if out else text
        except ValueError as e:
            if (os.environ.get("LOCAL_AI_DEBUG", "").strip() == "1"):
                raise
            return text

    def _attach_locator_validation(
        self,
        meta: Dict[str, Any],
        probe_url: Optional[str],
        steps: List[Dict[str, Any]],
    ) -> None:
        if (os.environ.get("LOCAL_AI_PROBE_VALIDATE", "1").strip().lower() in ("0", "false", "no")):
            return
        u = _norm_str(probe_url)
        if not u or not steps:
            return
        warnings, verr = validate_plan_locators(u, steps)
        if verr:
            meta["locator_validation_error"] = verr
        if warnings:
            meta["locator_validation"] = warnings

    def _build_prompt(
        self,
        goal: str,
        project_name: str,
        page_snapshot: str = "",
        memory_context: Optional[str] = None,
        dom_context_pack: Optional[str] = None,
    ) -> str:
        mem_block = ""
        if memory_context:
            mem_block = f"\nRetrieved similar context (may be from past runs; verify against the LIVE snapshot):\n{memory_context}\n\n"
        snap_block = ""
        if (page_snapshot or "").strip():
            snap_block = (
                "\nBelow is a LIVE snapshot of interactive elements from the target page "
                "(use ONLY selectors that appear here or can be derived from id/name/placeholder shown; "
                "do not invent class names):\n"
                f"{page_snapshot.strip()}\n\n"
            )
        dom_block = ""
        if (dom_context_pack or "").strip():
            dom_block = (
                "\nDOM structure hint (grouped by vertical region; optional a11y trim — use only to disambiguate, "
                "not as source of truth for selectors if it conflicts with the LIVE list above):\n"
                f"{dom_context_pack.strip()}\n\n"
            )
        return (
            "You are the reasoning brain; when a LIVE page snapshot is included below, the server already used "
            "Playwright headless to list real interactive elements—your locators MUST prefer those lines "
            "(id/css/placeholder/aria-label) and MUST NOT invent class names absent from the snapshot.\n"
            "Each snapshot line starts with [n] — that integer is ONLY for the JSON field probe_index. "
            "NEVER put the line number in selector_value (e.g. selector_value must NOT be \"1\" or \"12\" alone). "
            "Copy the real locator from that line into selector_type/selector_value (e.g. css #kw, [name=\\\"wd\\\"], xpath …). "
            "If you use probe_index=n, still prefer selectors shown on that same line; the server maps probe_index to stable locators.\n"
            "Generate one executable UI test case with steps from this natural language goal.\n"
            f"Project: {project_name or 'unknown'}\n"
            f"Goal: {goal}\n"
            f"{mem_block}"
            f"{snap_block}"
            f"{dom_block}"
            "Output strict JSON with this schema:\n"
            "{\n"
            '  "case_name": "string",\n'
            '  "case_url": "string or empty",\n'
            '  "description": "string",\n'
            '  "precondition": "string",\n'
            '  "expected_result": "string",\n'
            '  "steps": [\n'
            "    {\n"
            '      "action": "navigate|click|input|wait|verify|extract_text",\n'
            '      "selector_type": "css|xpath|text",\n'
            '      "selector_value": "string",\n'
            '      "input_value": "string",\n'
            '      "description": "string",\n'
            '      "probe_index": "integer or empty string if not tied to a snapshot line"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Field rules:\n"
            "- navigate: put the full URL ONLY in input_value; selector_type and selector_value MUST be empty strings; probe_index empty.\n"
            "- wait: input_value MUST be a non-empty integer duration — SECONDS 1–120, OR milliseconds if value > 120 (e.g. 1500); never leave empty.\n"
            "- input: input_value MUST be the exact characters to type into the field (never empty). Put the typed text in input_value, not only in description (e.g. to search for X, input_value must be X).\n"
            "- verify: when asserting visible text, put the expected substring in input_value when applicable.\n"
            "- click: input_value usually empty; selector_value MUST be a real css/xpath/text from the snapshot (never a lone digit). "
            "Use probe_index for [n], not selector_value.\n"
            "- navigate: input_value MUST be the full http(s) URL (never empty when a URL is known from the goal).\n"
            "- input/verify/extract_text: selector_value must be concrete when probe_index is empty.\n"
            "- At least 4 steps when possible; start with navigate if URL is known.\n"
            "- Never omit JSON keys; use \"\" only where the rules above allow empty.\n"
            "OUTPUT FORMAT: respond with that single JSON object only—no markdown, no prose."
        )

    @staticmethod
    def _format_interaction_context(ctx: Optional[Dict[str, Any]]) -> str:
        """UI 传入的结构化情境（高亮步骤、划词、操作类型），与对话气泡/右键优化对齐。"""
        if not ctx or not isinstance(ctx, dict):
            return ""
        parts: List[str] = []
        raw_ix = ctx.get("focus_step_index")
        if raw_ix is not None and str(raw_ix).strip() != "":
            try:
                parts.append(
                    f"User focused on step index (1-based, same order as steps array): {int(float(str(raw_ix)))}"
                )
            except (TypeError, ValueError):
                pass
        multi = ctx.get("focus_step_indices")
        if isinstance(multi, (list, tuple)) and multi:
            try:
                norm = [int(float(str(x))) for x in multi[:24]]
                parts.append(f"Relevant step indices (1-based): {norm}")
            except (TypeError, ValueError):
                pass
        sel = (ctx.get("browser_selection_text") or ctx.get("selection_text") or "").strip()
        if sel:
            cap = 2400
            if len(sel) > cap:
                sel = sel[: cap - 1] + "…"
            parts.append(
                "User highlighted text in the page — treat as expected visible text for assertions or target copy: "
                f"{sel}"
            )
        kind = (ctx.get("action_kind") or ctx.get("intent") or "").strip()
        if kind:
            parts.append(
                f"UI intent hint: {kind}. If merge_steps: merge the indicated steps into one atomic step while "
                "keeping probe_index valid. If assert_from_selection: add a verify (or assert visible text) using "
                "the highlighted text. If optimize_step: adjust only the focused step (retry, wait, selectors)."
            )
        if not parts:
            return ""
        return "\nInteraction context (from editor / browser; obey when consistent with the LIVE snapshot):\n" + "\n".join(
            f"- {p}" for p in parts
        ) + "\n\n"

    @staticmethod
    def _sanitize_chat_history_for_prompt(history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
        """Drop UI-only keys (warningsList, etc.), cap size — keeps refine requests small and on-topic."""
        out: List[Dict[str, str]] = []
        if not history:
            return out
        for item in history:
            if not isinstance(item, dict):
                continue
            role = _norm_str(item.get("role")).lower() or "user"
            if role not in ("user", "assistant", "system"):
                role = "user"
            content = _norm_str(item.get("content"))
            if not content:
                continue
            if len(content) > 12000:
                content = content[:11999] + "…"
            out.append({"role": role, "content": content})
        return out[-12:]

    def _build_refine_prompt(
        self,
        user_message: str,
        project_name: str,
        current_plan: Dict[str, Any],
        history: Optional[List[Dict[str, Any]]],
        page_snapshot: str = "",
        memory_context: Optional[str] = None,
        dom_context_pack: Optional[str] = None,
        interaction_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        hist_clean = self._sanitize_chat_history_for_prompt(history)[-6:]
        history_text = "\n".join(
            [f"- {item.get('role', 'user')}: {item.get('content', '')}" for item in hist_clean]
        )
        plan_text = json.dumps(current_plan or {}, ensure_ascii=False)
        iact = self._format_interaction_context(interaction_context)
        mem_block = ""
        if memory_context:
            mem_block = f"\nRetrieved similar context (past cases; verify against LIVE snapshot):\n{memory_context}\n\n"
        snap_block = ""
        if (page_snapshot or "").strip():
            snap_block = (
                "\nLIVE page element snapshot (prefer these locators; do not invent):\n"
                f"{page_snapshot.strip()}\n\n"
            )
        dom_block = ""
        if (dom_context_pack or "").strip():
            dom_block = "\nDOM structure hint (grouped):\n" + dom_context_pack.strip() + "\n\n"
        return (
            "You refine plans using the same rules: if a LIVE snapshot is present, selectors must align with "
            "those real elements only.\n"
            "You are refining an existing UI test case plan.\n"
            f"Project: {project_name or 'unknown'}\n"
            f"{iact}"
            f"User latest instruction: {user_message}\n"
            f"Chat history:\n{history_text or '- none'}\n"
            f"Current plan JSON:\n{plan_text}\n"
            f"{mem_block}"
            f"{snap_block}"
            f"{dom_block}"
            "Return strict JSON only, with full schema fields and a full updated steps list:\n"
            "{\n"
            '  "case_name": "string",\n'
            '  "case_url": "string or empty",\n'
            '  "description": "string",\n'
            '  "precondition": "string",\n'
            '  "expected_result": "string",\n'
            '  "steps": [\n'
            "    {\n"
            '      "action": "navigate|click|input|wait|verify|extract_text",\n'
            '      "selector_type": "css|xpath|text",\n'
            '      "selector_value": "string",\n'
            '      "input_value": "string",\n'
            '      "description": "string",\n'
            '      "probe_index": "integer or empty"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "navigate step: URL only in input_value; empty selector fields. "
            "wait: seconds 1-120 OR milliseconds if >120. "
            "input: input_value must contain the exact text to type (never empty). "
            "click: selector_value must be a real locator from the snapshot, never a lone digit; use probe_index for line [n]. "
            "verify: put expected substring in input_value when checking text. "
            "Do not return markdown. OUTPUT FORMAT: one JSON object only—no fences, no prose."
        )

    def _parse_json_response(self, content: str) -> Dict[str, Any]:
        raw = _strip_llm_noise((content or "").strip())
        if not raw:
            raise ValueError("本地模型返回为空")

        bases = _collect_json_try_strings(raw)
        variants: List[str] = []
        seen = set()
        for b in bases:
            for v in (
                b,
                _normalize_smart_quotes_for_json(b),
                _repair_json_trailing_commas(b),
                _repair_json_trailing_commas(_normalize_smart_quotes_for_json(b)),
            ):
                t = (v or "").strip()
                if t and t not in seen:
                    seen.add(t)
                    variants.append(t)

        last_err: Optional[Exception] = None
        for cand in variants:
            try:
                val = json.loads(cand)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                last_err = e
                continue
            root = _coerce_plan_root(val)
            if isinstance(root, dict):
                return root
            last_err = ValueError("JSON 已解析但不是预期的用例对象或步骤列表")

        preview = raw.replace("\r", " ").replace("\n", " ").strip()[:220]
        msg = (
            "无法将模型应答解析为用例 JSON（已尝试：markdown 代码块、截取首个对象、修复尾随逗号、弯引号等）。"
            "建议：① 重启本 Web 服务（避免加载旧的 .pyc）；② 升级 Ollama；③ 设置环境变量 LOCAL_LLM_JSON_FORMAT=0 关闭 JSON 约束；"
            "④ 设置 LOCAL_LLM_JSON_RETRY_PLAIN=0 可禁用「无 format 重试」；⑤ 换用 qwen2.5、llama3.1 等指令模型。"
            f" 应答摘要：{preview!r}"
        )
        if last_err is not None:
            raise ValueError(msg) from last_err
        raise ValueError(msg)

    def _normalize_output(
        self,
        data: Dict[str, Any],
        goal: str,
        project_name: str,
        using_model: str,
        probe_registry: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        goal_s = _norm_str(goal)
        case_name = _norm_str(data.get("case_name")) or f"AI生成用例-{goal_s[:30]}"
        case_url = _norm_str(data.get("case_url") or data.get("caseUrl"))
        description = _norm_str(data.get("description"))
        precondition = _norm_str(data.get("precondition"))
        expected_result = _norm_str(data.get("expected_result"))
        raw_steps = data.get("steps") or []
        if not isinstance(raw_steps, list):
            raw_steps = []

        probe_by_index: Dict[int, Dict[str, Any]] = {}
        if probe_registry:
            for ent in probe_registry:
                if not isinstance(ent, dict):
                    continue
                try:
                    ii = int(ent.get("i"))
                except (TypeError, ValueError):
                    continue
                probe_by_index[ii] = ent

        steps: List[Dict[str, Any]] = []
        allowed_actions = {"navigate", "click", "input", "wait", "verify", "extract_text"}
        allowed_selector_types = {"css", "xpath", "text", ""}

        for i, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict):
                continue
            action = _norm_str(step.get("action")).lower()
            if action not in allowed_actions:
                action = "click"
            selector_type = _norm_str(step.get("selector_type")).lower()
            if selector_type not in allowed_selector_types:
                selector_type = "css"
            selector_value = _norm_str(step.get("selector_value"))
            input_value = _norm_str(step.get("input_value"))
            description_step = _norm_str(step.get("description")) or f"步骤{i}: {action}"

            pi_raw = step.get("probe_index")
            pi: Optional[int] = None
            if pi_raw is not None and _norm_str(pi_raw) != "":
                try:
                    pi = int(float(str(pi_raw).strip()))
                except (TypeError, ValueError):
                    pi = None
            if (
                pi is None
                and action in ("click", "input", "verify", "extract_text")
                and selector_value.isdigit()
            ):
                try:
                    cand_pi = int(selector_value)
                    if probe_by_index and cand_pi in probe_by_index:
                        pi = cand_pi
                    selector_value = ""
                except ValueError:
                    selector_value = ""
            if pi is not None and pi in probe_by_index:
                ent = probe_by_index[pi]
                rec = _norm_str(ent.get("recommended_selector"))
                rty = _norm_str(ent.get("recommended_selector_type")).lower()
                if rec:
                    selector_value = rec
                    if rty in ("css", "xpath", "text"):
                        selector_type = rty

            if action == "navigate":
                url_guess = _norm_str(input_value or selector_value or case_url)
                if url_guess.startswith("//"):
                    url_guess = "https:" + url_guess
                steps.append(
                    {
                        "action": "navigate",
                        "selector_type": "",
                        "selector_value": "",
                        "input_value": url_guess,
                        "description": description_step,
                    }
                )
                if url_guess and not case_url:
                    case_url = url_guess
                continue

            row_step: Dict[str, Any] = {
                "action": action,
                "selector_type": selector_type,
                "selector_value": selector_value,
                "input_value": input_value,
                "description": description_step,
            }
            if pi is not None:
                row_step["probe_index"] = str(pi)
                if pi in probe_by_index:
                    lc = build_locator_candidates_from_probe_entry(probe_by_index[pi])
                    if lc:
                        row_step["locator_candidates"] = lc
            steps.append(row_step)

        if case_url and steps and steps[0].get("action") == "navigate":
            if not _norm_str(steps[0].get("input_value")):
                steps[0]["input_value"] = case_url

        if len(steps) < 1:
            steps = [
                {
                    "action": "navigate",
                    "selector_type": "",
                    "selector_value": "",
                    "input_value": case_url,
                    "description": "导航到目标页面",
                },
                {
                    "action": "wait",
                    "selector_type": "",
                    "selector_value": "",
                    "input_value": "1500",
                    "description": "等待页面加载稳定",
                },
            ]

        self._fill_missing_step_payloads(steps, goal_s, case_url, probe_registry)

        if steps and str(steps[0].get("action") or "").lower() == "navigate":
            u0 = _norm_str(steps[0].get("input_value"))
            if u0 and not case_url:
                case_url = u0

        return {
            "case_name": case_name,
            "case_url": case_url,
            "description": description or f"AI根据自然语言目标自动生成：{goal_s}",
            "precondition": precondition,
            "expected_result": expected_result,
            "steps": steps,
            "meta": {
                "provider": "local",
                "model": using_model,
                "project_name": project_name or "",
            },
        }


local_ai_service = LocalAIService()
