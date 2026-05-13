"""
AI 用例计划步骤的统一规范化入口：所有来自模型的 steps 在持久化或返回给前端前经同一去重/校验。
与 app 中写入 DB 的逻辑对齐（action/selector 字段）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# 单独标签名、无 # . [ 组合符时极易误点多个元素，平台不鼓励作为最终定位
_OVERLY_BROAD_SINGLE_TAGS = frozenset(
    {
        "button",
        "input",
        "a",
        "div",
        "span",
        "form",
        "select",
        "label",
        "img",
        "li",
        "ul",
        "ol",
        "td",
        "tr",
        "table",
        "p",
        "section",
        "article",
        "html",
        "body",
        "h1",
        "h2",
        "h3",
        "h4",
    }
)


def is_overly_broad_css_selector(selector_value: str) -> bool:
    """是否为「仅标签名」等过宽 CSS（易与平台稳定定位要求不符）。"""
    s = _str(selector_value)
    if not s:
        return False
    low = s.lower()
    if low.startswith(("xpath:", "//", "xpath=", "text=", "role=")):
        return False
    if any(c in s for c in " \t\n>+~"):
        return False
    if "#" in s or "[" in s:
        return False
    if "." in s:
        return False
    return low in _OVERLY_BROAD_SINGLE_TAGS


def _looks_like_url_bar_expected_text(iv: str) -> bool:
    v = _str(iv)
    if not v:
        return False
    if v.startswith(("http://", "https://")):
        return True
    if re.search(r"[%][0-9A-Fa-f]{2}", v) and ("=" in v or "&" in v):
        return True
    if re.match(r"^[a-z_][a-z0-9_]*=", v, re.I):
        return True
    return False


def _description_suggests_url_assertion(desc: str) -> bool:
    d = desc or ""
    if not d.strip():
        return False
    dl = d.lower()
    if "url" in dl or "网址" in d or "地址栏" in d or "页面地址" in d:
        if any(k in d for k in ("验证", "检查", "断言", "确认")) or any(
            k in dl for k in ("verify", "assert", "check")
        ):
            return True
        if any(k in d for k in ("包含", "等于", "一致")):
            return True
        if "encoded" in dl or "query" in dl or "address" in dl or "href" in dl:
            return True
    return False


def repair_raw_ai_steps_for_platform(steps: Any) -> List[str]:
    """
    在归一化/探测 clamp 之前就地修正常见「模型格式」问题：
    - 校验地址栏 / URL / 查询串却使用 text_equals + CSS → 改为 url_contains 并清空 selector；
    - url_* 断言若仍带 selector → 清空以符合本平台执行路径。
    返回人类可读告警列表（可并入 API warnings）。
    """
    warns: List[str] = []
    if not isinstance(steps, list):
        return warns
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        action = _str(step.get("action")).lower()
        if action != "assert":
            continue
        raw_ct = _str(step.get("compare_type")).lower() or "text_contains"
        if raw_ct == "equals":
            ct = "text_equals"
        elif raw_ct == "contains":
            ct = "text_contains"
        else:
            ct = raw_ct
        iv = _str(step.get("input_value"))
        desc = _str(step.get("description"))
        sv = _str(step.get("selector_value"))

        url_cts = ("url_equals", "url_contains")
        if ct in url_cts:
            if sv or _str(step.get("selector_type")):
                step["selector_value"] = ""
                step["selector_type"] = ""
                step.pop("locator_candidates", None)
                warns.append(f"第{idx}步 URL 断言已清空多余 selector，以符合平台格式")
            continue

        text_like = ("text_equals", "text_contains", "text_regex", "")
        if ct not in text_like:
            continue
        if not (_description_suggests_url_assertion(desc) or _looks_like_url_bar_expected_text(iv)):
            continue
        step["compare_type"] = "url_contains"
        step["selector_value"] = ""
        step["selector_type"] = ""
        step.pop("locator_candidates", None)
        # wd= 百分号编码 → 解码为可读关键词（执行层仍比对原始/解码 URL 多种形态）
        try:
            from urllib.parse import unquote

            m = re.match(r"^(wd|word|query|q)=([^&]+)\s*$", iv, re.I)
            if m:
                dec = unquote(m.group(2)).strip()
                if dec and "%" not in dec:
                    step["input_value"] = dec
        except Exception:
            pass
        warns.append(f"第{idx}步已改为 URL 断言(compare_type=url_contains)，selector 已清空")
    return warns


def normalize_ai_step(step: dict) -> dict:
    allowed_actions = {"navigate", "click", "input", "wait", "verify", "extract_text", "assert"}
    action = _str(step.get("action")).lower()
    if action not in allowed_actions:
        action = "click"
    selector_type = _str(step.get("selector_type")).lower()
    selector_value = _str(step.get("selector_value"))
    input_value = _str(step.get("input_value"))
    description = _str(step.get("description"))
    compare_type = _str(step.get("compare_type"))
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
    if compare_type and action == "assert":
        out["compare_type"] = compare_type
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
            _str(step.get("compare_type")),
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
        if step["action"] in {"click", "input", "verify", "extract_text", "assert"} and not step["selector_value"]:
            ct = _str(step.get("compare_type")).lower()
            if step["action"] == "assert" and ct in ("url_equals", "url_contains"):
                pass
            else:
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
    repair_warns = repair_raw_ai_steps_for_platform(steps)
    clean, warnings = dedupe_and_validate_ai_steps(steps)
    warnings = list(repair_warns) + list(warnings)
    plan["steps"] = clean
    meta = plan.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        plan["meta"] = meta
    if warnings:
        meta["normalization_warnings"] = warnings
    extra: List[str] = []
    sr = meta.get("step_repair_warnings")
    if isinstance(sr, list):
        extra.extend(str(x) for x in sr if str(x).strip())
    cw = meta.get("selector_clamp_warnings")
    if isinstance(cw, list):
        extra = [str(x) for x in cw if str(x).strip()]
    merged_warnings = extra + warnings
    return plan, merged_warnings


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
            vt = (iv or "auto").strip().lower()
            if vt not in ("auto", "slider", "image", "visible", "exist", "clickable"):
                vt = "auto"
            out.append(
                {
                    "action": "verify",
                    "selector": sv,
                    "selector_type": st,
                    "iframe_selector": "",
                    "verify_type": vt,
                    "input_value": iv,
                    "text": iv,
                    "description": desc,
                }
            )
            continue
        if action == "assert":
            ct = _str(step.get("compare_type")).lower() or "text_contains"
            out.append(
                {
                    "action": "assert",
                    "selector": sv,
                    "selector_type": st,
                    "iframe_selector": "",
                    "input_value": iv,
                    "text": iv,
                    "compare_type": ct,
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
