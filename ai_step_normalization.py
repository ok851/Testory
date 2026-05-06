"""
AI 用例计划步骤的统一规范化入口：所有来自模型的 steps 在持久化或返回给前端前经同一去重/校验。
与 app 中写入 DB 的逻辑对齐（action/selector 字段）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_ai_step(step: dict) -> dict:
    allowed_actions = {"navigate", "click", "input", "wait", "verify", "extract_text"}
    action = _str(step.get("action")).lower()
    if action not in allowed_actions:
        action = "click"
    selector_type = _str(step.get("selector_type")).lower()
    selector_value = _str(step.get("selector_value"))
    input_value = _str(step.get("input_value"))
    description = _str(step.get("description"))
    lc = step.get("locator_candidates")
    if lc is not None and not isinstance(lc, str):
        try:
            lc = json.dumps(lc, ensure_ascii=False)
        except Exception:
            lc = ""
    elif lc is None:
        lc = ""
    else:
        lc = _str(lc)
    out: Dict[str, Any] = {
        "action": action,
        "selector_type": selector_type,
        "selector_value": selector_value,
        "input_value": input_value,
        "description": description,
    }
    if lc:
        out["locator_candidates"] = lc
    return out


def dedupe_and_validate_ai_steps(steps: list) -> Tuple[List[dict], List[str]]:
    """
    去重 + 非阻断校验提示。
    Returns: (clean_steps, warnings)
    """
    warnings: List[str] = []
    clean_steps: List[dict] = []
    seen = set()

    for raw in steps or []:
        if not isinstance(raw, dict):
            continue
        step = normalize_ai_step(raw)
        key = (
            step["action"],
            step["selector_type"],
            step["selector_value"],
            step["input_value"],
        )
        if key in seen:
            warnings.append(f"检测到重复步骤并已去重: {step['action']} {step['selector_value']}")
            continue
        seen.add(key)
        clean_steps.append(step)

    if clean_steps:
        first_action = clean_steps[0].get("action")
        if first_action != "navigate":
            warnings.append("建议首步使用 navigate 进入目标页面，以提升执行稳定性。")

    for idx, step in enumerate(clean_steps, start=1):
        if step["action"] in {"click", "input", "verify", "extract_text"} and not step["selector_value"]:
            warnings.append(f"第{idx}步缺少 selector_value，运行时可能失败。")
        if step["action"] == "input" and not step["input_value"]:
            warnings.append(f"第{idx}步 input 未填写输入值，请在步骤编辑中补充或重新生成。")
        if step["action"] == "navigate" and not step["input_value"]:
            warnings.append(f"第{idx}步 navigate 未填写 URL，请在步骤编辑中补充或重新生成。")
        if step["action"] == "wait":
            try:
                ms = int(step["input_value"] or "0")
                if ms > 15000:
                    warnings.append(f"第{idx}步等待时间较长({ms}ms)，建议改为显式条件等待。")
            except Exception:
                warnings.append(f'第{idx}步 wait 参数非数字: {step["input_value"]}')

    return clean_steps, warnings


def apply_step_normalization_to_plan(plan: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    就地更新 plan['steps']，并将 warnings 写入 plan['meta']['normalization_warnings']（合并已有 meta）。
    返回 (plan, warnings) 便于 API 同时设置顶层 warnings 字段。
    """
    if not isinstance(plan, dict):
        return plan, []
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return plan, []
    clean, warnings = dedupe_and_validate_ai_steps(steps)
    plan["steps"] = clean
    meta = plan.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        plan["meta"] = meta
    if warnings:
        meta["normalization_warnings"] = warnings
    return plan, warnings


def _wait_ms_from_ai_input(input_value: str) -> int:
    """AI 步骤 wait：<=120 视为秒，否则视为毫秒（与模型提示一致）。"""
    raw = _str(input_value)
    if not raw:
        return 1000
    try:
        v = int(float(raw))
    except Exception:
        return 1000
    if v <= 0:
        return 1000
    if v <= 120:
        return min(v * 1000, 120_000)
    return min(v, 600_000)


def _parse_locator_candidates(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    s = _str(val)
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


def ai_plan_steps_to_playwright_script_steps(steps: Any) -> List[Dict[str, Any]]:
    """
    将 AI 规划步骤转为 playwright_automation.execute_script_steps 可执行的步骤列表。
    （navigate 使用 url；click/input 使用 selector；wait 使用 time 毫秒）
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(steps, list):
        return out
    for raw in steps:
        if not isinstance(raw, dict):
            continue
        step = normalize_ai_step(raw)
        action = step["action"]
        st = step["selector_type"] or "css"
        sv = step["selector_value"]
        iv = step["input_value"]
        desc = step["description"]
        lc = _parse_locator_candidates(step.get("locator_candidates"))

        if action == "navigate":
            url = iv or sv
            if url:
                out.append({"action": "navigate", "url": url, "description": desc})
            continue
        if action == "click":
            if not sv:
                continue
            row: Dict[str, Any] = {
                "action": "click",
                "selector": sv,
                "selector_type": st,
                "iframe_selector": "",
                "description": desc,
            }
            if lc is not None:
                row["locator_candidates"] = lc
            out.append(row)
            continue
        if action == "input":
            if not sv or not iv:
                continue
            row = {
                "action": "input",
                "selector": sv,
                "selector_type": st,
                "iframe_selector": "",
                "text": iv,
                "input_value": iv,
                "description": desc,
            }
            if lc is not None:
                row["locator_candidates"] = lc
            out.append(row)
            continue
        if action == "wait":
            out.append({"action": "wait", "time": _wait_ms_from_ai_input(iv), "description": desc})
            continue
        if action == "verify":
            if not sv:
                continue
            out.append(
                {
                    "action": "verify",
                    "selector": sv,
                    "selector_type": st,
                    "iframe_selector": "",
                    "verify_type": "visible",
                    "input_value": iv,
                    "text": iv,
                    "description": desc,
                }
            )
            continue
        if action == "extract_text":
            row = {
                "action": "extract_text",
                "selector": sv,
                "selector_type": st,
                "iframe_selector": "",
                "description": desc,
            }
            out.append(row)
            continue
    return out
