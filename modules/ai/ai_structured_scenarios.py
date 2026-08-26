"""
从需求说明书（纯文本 / Markdown）生成半结构化测试场景。
输出独立于 UI 元素库，便于人工评审后再映射为用例步骤或接入 LOCAL_AI。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

from modules.core.logger import uat_logger


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


def structured_scenarios_chunk_size() -> int:
    try:
        n = int(os.environ.get("AI_SCENARIO_CHUNK_CHARS", "12000") or "12000")
    except ValueError:
        n = 12000
    return max(4000, min(n, 60000))


def _merge_scenario_documents(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge multiple partial JSON documents from chunked LLM calls."""
    out: Dict[str, Any] = {
        "system_under_test": "",
        "scope_notes": [],
        "features": [],
        "scenarios": [],
        "traceability": [],
        "risks_and_unknowns": [],
    }
    seen_sid = set()
    seen_fid = set()
    for p in parts:
        if not isinstance(p, dict):
            continue
        if not out["system_under_test"] and p.get("system_under_test"):
            out["system_under_test"] = str(p.get("system_under_test") or "")
        for k in ("scope_notes", "risks_and_unknowns"):
            for x in p.get(k) or []:
                if isinstance(x, str) and x.strip() and x not in out[k]:  # type: ignore[index]
                    out[k].append(x.strip())  # type: ignore[index]
        for f in p.get("features") or []:
            if not isinstance(f, dict):
                continue
            fid = str(f.get("id") or "").strip() or str(f.get("name") or "")
            if fid and fid not in seen_fid:
                seen_fid.add(fid)
                out["features"].append(f)
        for s in p.get("scenarios") or []:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "").strip() or str(s.get("title") or "")
            key = sid or json.dumps(s, ensure_ascii=False)[:120]
            if key in seen_sid:
                continue
            seen_sid.add(key)
            out["scenarios"].append(s)
        for t in p.get("traceability") or []:
            if isinstance(t, dict):
                out["traceability"].append(t)
    return out


def generate_structured_scenarios_from_requirements_chunked(
    requirements_text: str,
    profile: Dict[str, Any],
    *,
    extra_context: str = "",
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Split very long requirements into chunks, call generate_structured_scenarios_from_requirements
    per chunk, then merge scenarios (dedupe by id).
    """
    warns: List[str] = []
    body = (requirements_text or "").strip()
    if not body:
        return {}, ["需求正文为空"]

    cap = structured_scenarios_max_chars()
    chunk_sz = structured_scenarios_chunk_size()
    if len(body) <= cap and len(body) <= chunk_sz * 1.25:
        return generate_structured_scenarios_from_requirements(
            body, profile, extra_context=extra_context
        )

    warns.append(f"长文档分块处理：每块约 {chunk_sz} 字符")
    parts: List[Dict[str, Any]] = []
    n_chunks = 0
    for i in range(0, min(len(body), cap), chunk_sz):
        chunk = body[i : i + chunk_sz]
        n_chunks += 1
        ctx = (extra_context or "").strip()
        if n_chunks > 1:
            ctx = (ctx + "\n" if ctx else "") + f"（第 {n_chunks} 段续篇，避免与已输出场景 id 重复）"
        doc, w = generate_structured_scenarios_from_requirements(
            chunk, profile, extra_context=ctx[:4000]
        )
        warns.extend(w)
        if doc:
            parts.append(doc)
        if n_chunks >= 24:
            warns.append("已达分块上限（24），后续内容未处理")
            break

    if not parts:
        return {}, warns
    merged = _merge_scenario_documents(parts)
    if not merged.get("scenarios"):
        warns.append("分块合并后 scenarios 为空")
    return merged, warns


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
        "约束：不要编写页面选择器级自动化步骤（禁止 css/xpath）；只输出高层次场景。\n"
        "必须使用 ONLY JSON（不要 markdown），且结构与示例字段一致：\n"
        + SCENARIO_SCHEMA_HINT
        + "\n\n需求正文：\n"
        + body
    )
    if ctx:
        prompt += "\n\n补充上下文：\n" + ctx[:4000]

    from modules.ai.ai_selector_recovery import _extract_json_obj
    from modules.ai.ai_local_inference import local_ai_service
    from modules.ai.ai_multi_provider import dispatch_chat

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
