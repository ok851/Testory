import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.exceptions import RequestException

from ai_page_probe import fetch_page_controls_summary, pick_probe_url


def _norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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
    ) -> Dict[str, Any]:
        probe_note = ""
        page_snapshot = ""
        if probe_page:
            pu = pick_probe_url(goal, "", None)
            if pu:
                page_snapshot, perr = fetch_page_controls_summary(pu)
                if perr:
                    probe_note = perr
            else:
                probe_note = "未在描述中找到 http(s) URL，已跳过页面探测（请在目标中写明完整网址）"
        prompt = self._build_prompt(goal, project_name, page_snapshot=page_snapshot)
        using_model = (model or "").strip() or self.model_mid
        content = self._chat_completion(prompt, using_model)
        parsed = self._parse_json_response(content)
        out = self._normalize_output(parsed, goal, project_name, using_model)
        meta = out.setdefault("meta", {})
        if probe_note:
            meta["probe_note"] = probe_note
        if page_snapshot:
            meta["probe_used"] = True
        return out

    def refine_case_and_steps(
        self,
        user_message: str,
        project_name: str = "",
        current_plan: Dict[str, Any] = None,
        history: List[Dict[str, str]] = None,
        model: str = "",
        probe_page: bool = False,
    ) -> Dict[str, Any]:
        page_snapshot = ""
        probe_note = ""
        if probe_page:
            plan = current_plan if isinstance(current_plan, dict) else {}
            cu = _norm_str(plan.get("case_url"))
            pu = pick_probe_url(user_message, cu, plan)
            if pu:
                page_snapshot, perr = fetch_page_controls_summary(pu)
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
        using_model = (model or "").strip() or self.model_mid
        content = self._chat_completion(prompt, using_model)
        parsed = self._parse_json_response(content)
        out = self._normalize_output(parsed, user_message, project_name, using_model)
        meta = out.setdefault("meta", {})
        if probe_note:
            meta["probe_note"] = probe_note
        if page_snapshot:
            meta["probe_used"] = True
        return out

    def _chat_completion(self, prompt: str, model: str) -> str:
        url = f"{self.base_url}/api/chat"
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
            '      "description": "string"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Field rules:\n"
            "- navigate: put the full URL ONLY in input_value; selector_type and selector_value MUST be empty strings.\n"
            "- wait: input_value MUST be a non-empty integer duration — SECONDS 1–120, OR milliseconds if value > 120 (e.g. 1500); never leave empty.\n"
            "- click/input/verify/extract_text: selector_value must be a concrete css/xpath/text locator.\n"
            "- At least 4 steps when possible; start with navigate if URL is known.\n"
            "- If unknown value, use empty string, never omit keys."
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
            '      "description": "string"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "navigate step: URL only in input_value; empty selector fields. "
            "wait: seconds 1-120 OR milliseconds if >120. "
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

    def _normalize_output(self, data: Dict[str, Any], goal: str, project_name: str, using_model: str) -> Dict[str, Any]:
        goal_s = _norm_str(goal)
        case_name = _norm_str(data.get("case_name")) or f"AI生成用例-{goal_s[:30]}"
        case_url = _norm_str(data.get("case_url"))
        description = _norm_str(data.get("description"))
        precondition = _norm_str(data.get("precondition"))
        expected_result = _norm_str(data.get("expected_result"))
        raw_steps = data.get("steps") or []
        if not isinstance(raw_steps, list):
            raw_steps = []

        steps: List[Dict[str, str]] = []
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

            steps.append(
                {
                    "action": action,
                    "selector_type": selector_type,
                    "selector_value": selector_value,
                    "input_value": input_value,
                    "description": description_step,
                }
            )

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
