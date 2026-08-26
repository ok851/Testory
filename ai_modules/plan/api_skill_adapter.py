from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from modules.integration.api_http_helper import execute_api_spec_sync, get_json_path_value


_VAR_PATTERN = re.compile(r"\{\{(.+?)\}\}")


def _resolve_variables(text: str, variables: Dict[str, Any]) -> str:
    if not text or "{{" not in text:
        return text

    def _repl(m: re.Match) -> str:
        key = m.group(1).strip()
        val = variables.get(key)
        if val is None:
            parts = key.split(".")
            val = variables
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = None
                    break
        return str(val) if val is not None else m.group(0)

    return _VAR_PATTERN.sub(_repl, text)


def _deep_resolve(obj: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(obj, str):
        return _resolve_variables(obj, variables)
    if isinstance(obj, dict):
        return {k: _deep_resolve(v, variables) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_resolve(item, variables) for item in obj]
    return obj


def _extract_from_response(
    response_json: Any,
    response_text: str,
    response_headers: Dict[str, str],
    extract_config: Dict[str, Any],
) -> Dict[str, Any]:
    extracted: Dict[str, Any] = {}
    if not extract_config:
        return extracted
    for var_name, rule in extract_config.items():
        if not isinstance(rule, dict):
            continue
        json_path = rule.get("json_path", "")
        var_type = rule.get("type", "string")
        transform = rule.get("transform", "")
        if json_path:
            val = get_json_path_value(response_json, json_path)
        else:
            val = None

        if transform == "strip_currency":
            if isinstance(val, str):
                val = re.sub(r"[^\d.]", "", val)
            if val:
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
        elif transform == "parse_iso8601":
            pass
        elif transform == "strip_whitespace":
            if isinstance(val, str):
                val = val.strip()

        if var_type == "number" and val is not None:
            try:
                val = float(val)
            except (ValueError, TypeError):
                pass
        elif var_type == "string" and val is not None:
            val = str(val)
        elif var_type == "boolean" and val is not None:
            if isinstance(val, str):
                val = val.lower() in ("true", "1", "yes")
            else:
                val = bool(val)

        extracted[var_name] = val
    return extracted


def execute_api_stage(
    stage: Dict[str, Any],
    variables: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:

    if not variables:
        variables = {}
    resolved_stage = _deep_resolve(dict(stage), variables)

    req_block = resolved_stage.get("request", {})
    if isinstance(req_block, dict):
        method = req_block.get("method", resolved_stage.get("method", "GET"))
        url = req_block.get("url", resolved_stage.get("url", ""))
        headers = req_block.get("headers") or resolved_stage.get("headers") or {}
        body_data = req_block.get("body") if req_block.get("body") is not None else resolved_stage.get("body")
        body_type = req_block.get("body_type", resolved_stage.get("body_type", "json"))
        auth_type = req_block.get("auth_type", resolved_stage.get("auth_type", ""))
        bearer_token = req_block.get("bearer_token", resolved_stage.get("bearer_token", ""))
        timeout_val = req_block.get("timeout", resolved_stage.get("timeout", 30))
    else:
        method = resolved_stage.get("method", "GET")
        url = resolved_stage.get("url", "")
        headers = resolved_stage.get("headers") or {}
        body_data = resolved_stage.get("body")
        body_type = resolved_stage.get("body_type", "json")
        auth_type = resolved_stage.get("auth_type", "")
        bearer_token = resolved_stage.get("bearer_token", "")
        timeout_val = resolved_stage.get("timeout", 30)

    body_json = None
    body_form = None
    body_raw = None

    if body_type == "none":
        pass
    elif body_type == "form":
        body_form = body_data
    elif body_type == "raw":
        body_raw = str(body_data) if body_data else None
    else:
        body_json = body_data

    spec: Dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": headers,
        "timeout": timeout_val,
    }
    if body_json is not None:
        spec["body_json"] = body_json
    if body_form is not None:
        spec["body_form"] = body_form
    if body_raw is not None:
        spec["body_raw"] = body_raw
        spec["body_type"] = "raw"

    assert_block = resolved_stage.get("assert", {})
    if isinstance(assert_block, dict):
        status_in = assert_block.get("status_in")
        expected_status = assert_block.get("status", assert_block.get("expected_status", 200))
    else:
        status_in = resolved_stage.get("status_in")
        expected_status = resolved_stage.get("expected_status", 200)

    if auth_type:
        spec["auth_type"] = auth_type
        spec["bearer_token"] = bearer_token

    if status_in and isinstance(status_in, list):
        pass
    else:
        if not isinstance(expected_status, int):
            try:
                expected_status = int(expected_status)
            except (ValueError, TypeError):
                expected_status = 200
        spec["expected_status"] = expected_status

    t0 = time.perf_counter()
    result = execute_api_spec_sync(spec)
    elapsed = round((time.perf_counter() - t0) * 1000, 1)

    if status_in and isinstance(status_in, list):
        actual_status = result.get("status_code")
        result["ok_assert"] = actual_status in status_in
        if not result["ok_assert"]:
            result["assert_message"] = (
                f"HTTP {actual_status}（期望 {status_in}）"
            )
            result["error"] = result["assert_message"]
        else:
            result["error"] = None

    result["elapsed_ms"] = elapsed

    extract_config = resolved_stage.get("extract")
    if not isinstance(extract_config, dict) or not extract_config:
        extract_config = resolved_stage.get("vars_to_store")
    if not isinstance(extract_config, dict):
        extract_config = {}

    extracted = _extract_from_response(
        result.get("response_json"),
        result.get("response_text") or "",
        result.get("response_headers") or {},
        extract_config,
    )

    # 脱敏 + 必选校验（与 UI vars_to_store 对齐）
    try:
        from ai_modules.plan.var_extraction import (
            apply_value_policy,
            collect_extraction_rules,
            validate_required_extractions,
        )

        rules = collect_extraction_rules(
            {"extract": extract_config, "vars_to_store": resolved_stage.get("vars_to_store")}
        )
        for name in list(extracted.keys()):
            rule = rules.get(name) or {"name": name}
            extracted[name] = apply_value_policy(name, extracted[name], rule)
        missing = validate_required_extractions(rules, extracted)
        if missing:
            result["ok_assert"] = False
            result["error"] = f"API 变量抽取失败（必选缺失或为空）: {', '.join(missing)}"
            result["error_code"] = "VAR_EXTRACT_MISSING"
    except Exception:
        # 抽取策略模块异常时，至少保证 None 不静默当成功
        missing_simple = [k for k, v in extracted.items() if v is None]
        if missing_simple and result.get("ok_assert", True):
            result["ok_assert"] = False
            result["error"] = f"API 变量抽取失败: {', '.join(missing_simple)}"

    result["extracted"] = dict(extracted)
    return result, extracted


class ApiSkillAdapter:

    skill_id = "testory-api-test"
    skill_name = "API Test Skill"
    skill_description = "Execute HTTP API calls as part of cross-end test orchestration"

    def __init__(self):
        pass

    def can_handle(self, layer: str) -> bool:
        return layer in ("api", "API")

    def execute(
        self,
        stage: Dict[str, Any],
        variables: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return execute_api_stage(stage, variables)
