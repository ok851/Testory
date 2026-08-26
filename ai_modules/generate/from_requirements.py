# -*- coding: utf-8 -*-
"""从需求文本/文件一键生成用例（内部可先结构化场景，无场景时自动回退）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def synthetic_scenario_document(requirements_text: str, title: str = "需求整体测试") -> Dict[str, Any]:
    """当 LLM 未产出场景列表时，用整段需求构造单场景以便继续批量生成。"""
    excerpt = (requirements_text or "").strip()
    steps = []
    if excerpt:
        if len(excerpt) > 4000:
            steps.append(excerpt[:4000] + "…")
        else:
            steps.append(excerpt)
    else:
        steps.append("按需求说明书执行主流程验证")
    return {
        "system_under_test": title[:200],
        "scenarios": [
            {
                "id": "req_main",
                "title": title[:200],
                "priority": "P1",
                "type": "functional",
                "preconditions": [],
                "high_level_steps": steps,
                "expected_results": ["满足需求说明书中的功能与验收描述"],
            }
        ],
    }


def try_structured_scenarios(
    requirements_text: str,
    profile: Optional[Dict[str, Any]],
    extra_context: str = "",
) -> Tuple[Dict[str, Any], List[str]]:
    from modules.ai.ai_structured_scenarios import (
        generate_structured_scenarios_from_requirements,
        generate_structured_scenarios_from_requirements_chunked,
        structured_scenarios_chunk_size,
    )

    warns: List[str] = []
    try:
        if len(requirements_text) > int(structured_scenarios_chunk_size() * 1.15):
            doc, w0 = generate_structured_scenarios_from_requirements_chunked(
                requirements_text, profile, extra_context=extra_context
            )
        else:
            doc, w0 = generate_structured_scenarios_from_requirements(
                requirements_text, profile, extra_context=extra_context
            )
        warns.extend(w0 or [])
        if not isinstance(doc, dict):
            doc = {}
        scenarios = doc.get("scenarios") if isinstance(doc.get("scenarios"), list) else []
        if not scenarios:
            warns.append("未解析出结构化场景，已按整份需求生成单条用例目标。")
            doc = synthetic_scenario_document(
                requirements_text,
                title=(doc.get("system_under_test") or "需求整体测试") if doc else "需求整体测试",
            )
        return doc, warns
    except Exception as e:
        warns.append(f"结构化场景生成失败，已回退为单场景：{e}")
        return synthetic_scenario_document(requirements_text), warns
