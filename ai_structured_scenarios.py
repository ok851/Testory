"""
从需求说明书（纯文本 / Markdown）生成半结构化测试场景。
输出独立于 UI 元素库，便于人工评审后再映射为用例步骤或接入 LOCAL_AI。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

from logger import uat_logger


SCENARIO_SCHEMA_HINT = """{
  "system_under_test": "string",
  "scope_notes": ["string"],
  "features": [{"id": "string", "name": "string", "description": "string"}],
  "scenarios": [{
    "id": "string",
    "feature_id": "string",
    "title": "string",
    "priority": "P0|P1|P2|P3",
    "type": "functional|negative|boundary|exploratory",
    "preconditions": ["string"],
    "high_level_steps": ["string"],
    "expected_results": ["string"],
    "test_data_hints": ["string"]
  }],
  "traceability": [{"requirement_excerpt": "string", "scenario_id": "string"}],
  "risks_and_unknowns": ["string"]
}"""


def structured_scenarios_max_chars() -> int:
    try:
        n = int(os.environ.get("AI_SCENARIO_REQUIREMENTS_MAX_CHARS", "24000") or "24000")
    except ValueError:
        n = 24000
    return max(2000, min(n, 200000))


def generate_structured_scenarios_from_requirements(
    requirements_text: str,
    profile: Dict[str, Any],
    *,
    extra_context: str = "",
) -> Tuple[Dict[str, Any], List[str]]:
    """调用已配置的推理链路；返回 (解析后的 dict, warnings)。"""
    warns: List[str] = []
    body = (requirements_text or "").strip()
    if not body:
        return {}, ["需求正文为空"]

    cap = structured_scenarios_max_chars()
    if len(body) > cap:
        body = body[: cap - 80] + "\n…(truncated)…"
        warns.append(f"需求正文已截断至约 {cap} 字符")

    ctx = (extra_context or "").strip()
    prompt = (
        "你是资深测试架构师。根据下面的需求片段生成测试场景规划。\n"
        "约束：不要编写 UI 自动化步骤（禁止 css/xpath）；只输出高层次场景。\n"
        "必须使用 ONLY JSON（不要 markdown），且结构与示例字段一致：\n"
        + SCENARIO_SCHEMA_HINT
        + "\n\n需求正文：\n"
        + body
    )
    if ctx:
        prompt += "\n\n补充上下文：\n" + ctx[:4000]

    from ai_selector_recovery import _extract_json_obj
    from ai_local_inference import local_ai_service
    from ai_multi_provider import dispatch_chat

    try:
        raw = dispatch_chat(prompt, profile, local_ai_service)
    except Exception as e:
        uat_logger.warning("[STRUCT_SCENARIO] LLM failed: %s", e)
        return {}, [str(e)]

    data = _extract_json_obj(raw)
    if not isinstance(data, dict):
        warns.append("模型输出无法解析为 JSON")
        return {}, warns

    if not data.get("scenarios"):
        warns.append("JSON 中 scenarios 为空，请检查需求是否过于笼统")

    return data, warns
