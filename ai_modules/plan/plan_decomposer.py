from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from modules.ai.ai_local_inference import local_ai_service


def _load_active_profile() -> Dict[str, Any]:
    try:
        from modules.ai.ai_config_paths import ai_model_registry_path
        path = str(ai_model_registry_path())
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            profiles = raw.get("profiles") or []
            aid = (raw.get("active_profile_id") or "").strip()
            for p in profiles:
                if isinstance(p, dict) and p.get("id") == aid:
                    return p
            if profiles and isinstance(profiles[0], dict):
                return profiles[0]
    except Exception:
        pass
    default_model = os.environ.get("LOCAL_LLM_MODEL_MID", "llama3:8b-instruct")
    return {
        "provider": "ollama",
        "api_style": "ollama",
        "model_id": default_model,
        "api_key": "",
        "base_url": "",
    }

SCHEMA_VERSION = "1.0"

_SUPPORTED_LAYERS = frozenset({"api", "web", "desktop", "mobile"})
_LAYER_DEFAULT_TIMEOUT = {"api": 30, "web": 60, "desktop": 90, "mobile": 120}

_CROSS_END_SYSTEM_PROMPT = (
    "You are a senior cross-platform test architect. "
    "Your task is to decompose a natural-language business flow into a JSON CrossEndPlan "
    "with sequential stages across API, Web, Desktop, and Mobile platforms. "
    "Each stage runs on exactly one layer (api/web/desktop/mobile). "
    "Output ONLY valid JSON—no markdown fences, no commentary, no trailing text. "
    "First non-whitespace character must be '{'; last must be '}'."
)

_CROSS_END_USER_PROMPT_TEMPLATE = """Decompose the following business flow into a CrossEndPlan.

Business flow description:
{user_input}

Return a JSON object with this exact schema:

{{
  "schema_version": "{schema_version}",
  "scenario": "brief one-line label",
  "stages": [
    {{
      "id": "stage-1",
      "layer": "api|web|desktop|mobile",
      "label": "human-readable stage label",
      "skill": "testory-api-test|testory-web-browser|testory-windows-desktop|testory-android-mobile",
      "depends_on": [],
      "timeout_seconds": 30,
      "cleanup": false,
      "on_failure": "abort",
      "request": {{"method": "GET", "url": "..."}},
      "extract": {{"var_name": {{"json_path": "$.data.field", "type": "string"}}}},
      "assert": {{"status": 200}},
      "sync_point": "unique_sync_point_id"
    }}
  ]
}}

Rules:
1. layer "api": use for data preparation, status checks, cleanup. Always use `skill: "testory-api-test"`.
2. layer "web": use for browser interactions. Always use `skill: "testory-web-browser"`.
3. layer "desktop": use for Windows desktop app operations. Always use `skill: "testory-windows-desktop"`.
4. layer "mobile": use for mobile app operations. Always use `skill: "testory-android-mobile"`.
5. ALWAYS add a final Cleanup stage (layer "api") with `"cleanup": true, "on_failure": "continue"` to delete/restore any test data created.
6. Use `depends_on` to chain stages by their sync_point values.
7. For API stages, `extract` maps variable names to {{"json_path": "...", "type": "string|number|boolean|datetime"}}.
8. For non-API stages, omit `request` and use `steps` array with action/selector/value objects.
9. For Cleanup stages, `assert` should use `"status_in": [200, 202, 204, 404]` for idempotent cleanup.
10. Choose appropriate `timeout_seconds` per layer: api=30, web=60, desktop=90, mobile=120.
11. The id format must be "stage-1", "stage-2", etc. in execution order.
12. sync_point must be unique snake_case identifiers describing what was accomplished.

output:
"""


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline > 0:
            text = text[first_newline + 1:]
        else:
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _parse_llm_json(content: str) -> Optional[Dict[str, Any]]:
    content = _strip_markdown_fence(content)
    candidates: List[str] = [content]

    brace_depth = 0
    last_brace = -1
    for i, ch in enumerate(content):
        if ch == "{":
            if brace_depth == 0:
                candidates.append(content[i:])
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0:
                last_brace = i
    if last_brace > 0:
        candidates.append(content[: last_brace + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _validate_plan(plan: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    schema_ver = plan.get("schema_version", "")
    if schema_ver != SCHEMA_VERSION:
        warnings.append(
            f"Plan schema_version '{schema_ver}' != '{SCHEMA_VERSION}'; "
            "output may be incompatible"
        )
    stages = plan.get("stages")
    if not isinstance(stages, list) or not stages:
        warnings.append("Plan has no stages")
        return warnings

    has_cleanup = False
    sync_points: set = set()
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            warnings.append(f"Stage {i} is not a dict")
            continue
        sid = stage.get("id", f"stage-{i+1}")
        layer = stage.get("layer", "")
        if layer not in _SUPPORTED_LAYERS:
            warnings.append(f"Stage {sid}: unknown layer '{layer}'")
        if stage.get("cleanup"):
            has_cleanup = True
        sp = stage.get("sync_point", "")
        if sp in sync_points:
            warnings.append(f"Stage {sid}: duplicate sync_point '{sp}'")
        sync_points.add(sp)

    if not has_cleanup:
        warnings.append("Plan has no Cleanup stage—test data may be left behind")
    return warnings


class CrossEndPlanDecomposer:

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.environ.get(
            "LOCAL_LLM_MODEL_MID", "llama3:8b-instruct"
        )

    def decompose(
        self, user_input: str, auto_inject_cleanup: bool = True
    ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        prompt = _CROSS_END_USER_PROMPT_TEMPLATE.format(
            user_input=user_input, schema_version=SCHEMA_VERSION
        )
        try:
            from modules.ai.ai_multi_provider import dispatch_chat
            profile = _load_active_profile()
            raw = dispatch_chat(prompt, profile, local_ai_service)
        except Exception as e:
            return None, [f"LLM call failed: {e}"]

        plan = _parse_llm_json(raw)
        if plan is None:
            return None, [f"Failed to parse LLM output as JSON. Raw:\n{raw[:500]}"]

        plan.setdefault("schema_version", SCHEMA_VERSION)
        plan.setdefault("plan_id", f"cross-end-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}")
        if "scenario" not in plan:
            plan["scenario"] = user_input[:80]

        warnings = _validate_plan(plan)

        if auto_inject_cleanup:
            stages = plan.get("stages", [])
            has_cleanup = any(s.get("cleanup") for s in stages if isinstance(s, dict))
            if not has_cleanup and stages:
                next_id = len(stages) + 1
                plan["stages"] = list(stages)
                plan["stages"].append({
                    "id": f"stage-{next_id}",
                    "layer": "api",
                    "label": "[Auto-Generated Cleanup] 清理测试数据",
                    "skill": "testory-api-test",
                    "depends_on": [],
                    "timeout_seconds": 15,
                    "cleanup": True,
                    "on_failure": "continue",
                    "request": {"method": "DELETE", "url": "{{cleanup_url}}"},
                    "assert": {"status_in": [200, 202, 204, 404]},
                    "sync_point": "auto_cleanup",
                })
                warnings.append(
                    "Auto-injected Cleanup stage—please replace {{cleanup_url}} "
                    "with the actual cleanup endpoint"
                )

        return plan, warnings

    def decompose_sync(
        self, user_input: str
    ) -> Dict[str, Any]:
        plan, warnings = self.decompose(user_input)
        return {
            "ok": plan is not None,
            "plan": plan,
            "warnings": warnings,
        }
