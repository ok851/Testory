import json
import os
from typing import Any, Dict, List

import requests
from requests.exceptions import RequestException


class LocalAIService:
    """
    Local LLM inference service (Ollama-compatible by default).
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.model_light = os.environ.get("LOCAL_LLM_MODEL_LIGHT", "qwen2:1.5b")
        self.model_mid = os.environ.get("LOCAL_LLM_MODEL_MID", "llama3:8b-instruct")
        self.timeout = int(os.environ.get("LOCAL_LLM_TIMEOUT", "90"))

    def generate_case_and_steps(self, goal: str, project_name: str = "", model: str = "") -> Dict[str, Any]:
        prompt = self._build_prompt(goal, project_name)
        using_model = (model or "").strip() or self.model_mid
        content = self._chat_completion(prompt, using_model)
        parsed = self._parse_json_response(content)
        return self._normalize_output(parsed, goal, project_name, using_model)

    def refine_case_and_steps(
        self,
        user_message: str,
        project_name: str = "",
        current_plan: Dict[str, Any] = None,
        history: List[Dict[str, str]] = None,
        model: str = "",
    ) -> Dict[str, Any]:
        prompt = self._build_refine_prompt(
            user_message=user_message,
            project_name=project_name,
            current_plan=current_plan or {},
            history=history or [],
        )
        using_model = (model or "").strip() or self.model_mid
        content = self._chat_completion(prompt, using_model)
        parsed = self._parse_json_response(content)
        return self._normalize_output(parsed, user_message, project_name, using_model)

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
            "options": {"temperature": 0.2},
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except RequestException as e:
            raise ValueError(
                "本地AI服务不可用：无法连接本地模型接口。"
                "请确认本地模型服务已启动，并检查 LOCAL_LLM_BASE_URL 配置。"
            ) from e
        data = resp.json() if resp.content else {}
        return ((data.get("message") or {}).get("content") or "").strip()

    def _build_prompt(self, goal: str, project_name: str) -> str:
        return (
            "Generate one executable UI test case with steps from this natural language goal.\n"
            f"Project: {project_name or 'unknown'}\n"
            f"Goal: {goal}\n\n"
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
            "Rules:\n"
            "- At least 4 steps.\n"
            "- First step should be navigate when URL is known.\n"
            "- Keep selectors meaningful and concise.\n"
            "- If unknown value, use empty string, never omit keys."
        )

    def _build_refine_prompt(
        self,
        user_message: str,
        project_name: str,
        current_plan: Dict[str, Any],
        history: List[Dict[str, str]],
    ) -> str:
        history_text = "\n".join(
            [f"- {item.get('role', 'user')}: {item.get('content', '')}" for item in history[-10:]]
        )
        plan_text = json.dumps(current_plan or {}, ensure_ascii=False)
        return (
            "You are refining an existing UI test case plan.\n"
            f"Project: {project_name or 'unknown'}\n"
            f"User latest instruction: {user_message}\n"
            f"Chat history:\n{history_text or '- none'}\n"
            f"Current plan JSON:\n{plan_text}\n\n"
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
            "Do not return markdown. Keep actions executable."
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
        case_name = (data.get("case_name") or "").strip() or f"AI生成用例-{goal[:30]}"
        case_url = (data.get("case_url") or "").strip()
        description = (data.get("description") or "").strip()
        precondition = (data.get("precondition") or "").strip()
        expected_result = (data.get("expected_result") or "").strip()
        raw_steps = data.get("steps") or []
        if not isinstance(raw_steps, list):
            raw_steps = []

        steps: List[Dict[str, str]] = []
        allowed_actions = {"navigate", "click", "input", "wait", "verify", "extract_text"}
        allowed_selector_types = {"css", "xpath", "text", ""}

        for i, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict):
                continue
            action = (step.get("action") or "").strip().lower()
            if action not in allowed_actions:
                action = "click"
            selector_type = (step.get("selector_type") or "").strip().lower()
            if selector_type not in allowed_selector_types:
                selector_type = "css"
            selector_value = (step.get("selector_value") or "").strip()
            input_value = (step.get("input_value") or "").strip()
            description_step = (step.get("description") or "").strip() or f"步骤{i}: {action}"

            steps.append(
                {
                    "action": action,
                    "selector_type": selector_type,
                    "selector_value": selector_value,
                    "input_value": input_value,
                    "description": description_step,
                }
            )

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
            "description": description or f"AI根据自然语言目标自动生成：{goal}",
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
