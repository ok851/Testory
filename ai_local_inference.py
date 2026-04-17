import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.exceptions import RequestException

from ai_page_probe import (
    build_locator_candidates_from_probe_entry,
    extract_http_urls,
    fetch_page_controls_bundle,
    pick_probe_url,
    validate_plan_locators,
)


def _norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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
        return "css", m.group(1).strip()
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
        probe_page: bool = False,
        profile: Optional[Dict[str, Any]] = None,
        probe_url_hint: str = "",
    ) -> Dict[str, Any]:
        probe_note = ""
        page_snapshot = ""
        probe_registry: List[Dict[str, Any]] = []
        probe_url: Optional[str] = None
        if probe_page:
            hint = _norm_str(probe_url_hint)
            probe_url = pick_probe_url(
                goal,
                "",
                None,
                extra_hints=[hint] if hint else None,
            )
            if probe_url:
                page_snapshot, perr, probe_registry = fetch_page_controls_bundle(probe_url)
                if perr:
                    probe_note = perr
            else:
                probe_note = "未在描述中找到 http(s) URL，已跳过页面探测（请在目标中写明完整网址）"
        prompt = self._build_prompt(goal, project_name, page_snapshot=page_snapshot)
        using_model, content = self._complete_for_model(
            prompt, model, profile, meta_fallback=self.model_mid
        )
        parsed = self._parse_json_response(content)
        out = self._normalize_output(
            parsed, goal, project_name, using_model, probe_registry=probe_registry
        )
        meta = out.setdefault("meta", {})
        if profile and isinstance(profile, dict):
            meta["provider"] = profile.get("provider") or "cloud"
            meta["profile_id"] = profile.get("id") or ""
            meta["model"] = using_model
        else:
            meta["provider"] = "local"
        if probe_note:
            meta["probe_note"] = probe_note
        if page_snapshot:
            meta["probe_used"] = True
        self._attach_locator_validation(meta, probe_url, out.get("steps") or [])
        return out

    def refine_case_and_steps(
        self,
        user_message: str,
        project_name: str = "",
        current_plan: Dict[str, Any] = None,
        history: List[Dict[str, str]] = None,
        model: str = "",
        probe_page: bool = False,
        profile: Optional[Dict[str, Any]] = None,
        probe_url_hint: str = "",
    ) -> Dict[str, Any]:
        page_snapshot = ""
        probe_note = ""
        probe_registry: List[Dict[str, Any]] = []
        probe_url: Optional[str] = None
        if probe_page:
            plan = current_plan if isinstance(current_plan, dict) else {}
            cu = _norm_str(plan.get("case_url") or plan.get("caseUrl"))
            hint = _norm_str(probe_url_hint)
            probe_url = pick_probe_url(
                user_message,
                cu,
                plan,
                extra_hints=[hint] if hint else None,
            )
            if probe_url:
                page_snapshot, perr, probe_registry = fetch_page_controls_bundle(probe_url)
                if perr:
                    probe_note = perr
            else:
                probe_note = "未找到可探测的 URL（请在指令或当前用例中提供 http(s) 地址）"
        prompt = self._build_refine_prompt(
            user_message=user_message,
            project_name=project_name,
            current_plan=current_plan or {},
            history=history or [],
            page_snapshot=page_snapshot,
        )
        using_model, content = self._complete_for_model(
            prompt, model, profile, meta_fallback=self.model_mid
        )
        parsed = self._parse_json_response(content)
        out = self._normalize_output(
            parsed, user_message, project_name, using_model, probe_registry=probe_registry
        )
        meta = out.setdefault("meta", {})
        if profile and isinstance(profile, dict):
            meta["provider"] = profile.get("provider") or "cloud"
            meta["profile_id"] = profile.get("id") or ""
            meta["model"] = using_model
        else:
            meta["provider"] = "local"
        if probe_note:
            meta["probe_note"] = probe_note
        if page_snapshot:
            meta["probe_used"] = True
        self._attach_locator_validation(meta, probe_url, out.get("steps") or [])
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

    def chat_ollama(self, prompt: str, model: str, base_url: Optional[str] = None) -> str:
        root = (base_url or "").strip().rstrip("/") or self.base_url
        return self._chat_completion_at(prompt, model, root)

    def _chat_completion(self, prompt: str, model: str) -> str:
        return self._chat_completion_at(prompt, model, self.base_url)

    def _chat_completion_at(self, prompt: str, model: str, base_url: str) -> str:
        url = f"{base_url.rstrip('/')}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior QA engineer. "
                        "Return only JSON, no markdown. "
                        "Use web UI actions compatible with a test runner."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": self._ollama_options(),
        }
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

    def _build_prompt(self, goal: str, project_name: str, page_snapshot: str = "") -> str:
        snap_block = ""
        if (page_snapshot or "").strip():
            snap_block = (
                "\nBelow is a LIVE snapshot of interactive elements from the target page "
                "(use ONLY selectors that appear here or can be derived from id/name/placeholder shown; "
                "do not invent class names):\n"
                f"{page_snapshot.strip()}\n\n"
            )
        return (
            "You are the reasoning brain; when a LIVE page snapshot is included below, the server already used "
            "Playwright headless to list real interactive elements—your locators MUST prefer those lines "
            "(id/css/placeholder/aria-label) and MUST NOT invent class names absent from the snapshot.\n"
            "Each line starts with [n] (probe_index). When a step targets a listed control, set probe_index to that "
            "integer n AND still fill selector_type/selector_value from that line (or use recommended=(type)…); "
            "the server may override selectors using probe_index for stability.\n"
            "Generate one executable UI test case with steps from this natural language goal.\n"
            f"Project: {project_name or 'unknown'}\n"
            f"Goal: {goal}\n"
            f"{snap_block}"
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
            "- click: input_value usually empty; selector_value MUST be a real css/xpath/text (never empty unless probe_index is set).\n"
            "- navigate: input_value MUST be the full http(s) URL (never empty when a URL is known from the goal).\n"
            "- input/verify/extract_text: selector_value must be concrete when probe_index is empty.\n"
            "- At least 4 steps when possible; start with navigate if URL is known.\n"
            "- Never omit JSON keys; use \"\" only where the rules above allow empty."
        )

    def _build_refine_prompt(
        self,
        user_message: str,
        project_name: str,
        current_plan: Dict[str, Any],
        history: List[Dict[str, str]],
        page_snapshot: str = "",
    ) -> str:
        history_text = "\n".join(
            [f"- {item.get('role', 'user')}: {item.get('content', '')}" for item in history[-6:]]
        )
        plan_text = json.dumps(current_plan or {}, ensure_ascii=False)
        snap_block = ""
        if (page_snapshot or "").strip():
            snap_block = (
                "\nLIVE page element snapshot (prefer these locators; do not invent):\n"
                f"{page_snapshot.strip()}\n\n"
            )
        return (
            "You refine plans using the same rules: if a LIVE snapshot is present, selectors must align with "
            "those real elements only.\n"
            "You are refining an existing UI test case plan.\n"
            f"Project: {project_name or 'unknown'}\n"
            f"User latest instruction: {user_message}\n"
            f"Chat history:\n{history_text or '- none'}\n"
            f"Current plan JSON:\n{plan_text}\n"
            f"{snap_block}"
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
            "click: selector_value must be non-empty unless probe_index is set. "
            "verify: put expected substring in input_value when checking text. "
            "Do not return markdown."
        )

    def _parse_json_response(self, content: str) -> Dict[str, Any]:
        if not content:
            raise ValueError("本地模型返回为空")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            snippet = content[start : end + 1]
            return json.loads(snippet)
        raise ValueError("本地模型返回非JSON格式，无法解析")

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
