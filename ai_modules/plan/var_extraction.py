# -*- coding: utf-8 -*-
"""跨端变量抽取：vars_to_store / extract / 步骤 store_as 统一落地。

契约：
- 声明了必选变量却抽不到 → 调用方应将阶段标为失败（防假联动）
- 敏感字段默认脱敏写入 context
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization|cookie)",
    re.I,
)


def is_sensitive_var_name(name: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(name or "")))


def redact_value(value: Any) -> str:
    if value is None:
        return "[REDACTED]"
    s = str(value)
    if len(s) <= 4:
        return "****"
    return s[:2] + ("*" * min(8, len(s) - 4)) + s[-2:]


def _as_rule(name: str, raw: Any) -> Dict[str, Any]:
    """将 vars_to_store 条目规范为 rule dict。"""
    if isinstance(raw, dict):
        rule = dict(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if text.startswith("$") or text.startswith("json:"):
            rule = {"json_path": text[5:] if text.startswith("json:") else text}
        elif text in ("url", "page_url", "title", "page_title"):
            rule = {"source": "url" if "url" in text else "title"}
        else:
            rule = {"selector": text, "source": "text"}
    else:
        rule = {"value": raw}
    rule.setdefault("name", name)
    if "optional" not in rule:
        rule["optional"] = False
    if "redact" not in rule:
        rule["redact"] = is_sensitive_var_name(name)
    return rule


def collect_extraction_rules(stage: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """合并 stage.vars_to_store、stage.extract 与步骤级 store_as 声明。"""
    rules: Dict[str, Dict[str, Any]] = {}
    for key in ("vars_to_store", "extract"):
        block = stage.get(key)
        if not isinstance(block, dict):
            continue
        for name, raw in block.items():
            n = str(name or "").strip()
            if not n:
                continue
            rules[n] = _as_rule(n, raw)

    steps = stage.get("steps") or []
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            store_as = (
                step.get("store_as")
                or step.get("var_name")
                or step.get("extract_as")
                or ""
            )
            store_as = str(store_as).strip()
            if not store_as:
                continue
            action = (step.get("action") or "").strip().lower()
            if action not in (
                "extract_text",
                "extract",
                "get_text",
                "assert",
                "verify",
                "",
            ):
                # 非抽取类步骤也可声明 store_as（取 selector 文本）
                pass
            if store_as not in rules:
                rules[store_as] = _as_rule(
                    store_as,
                    {
                        "selector": step.get("selector") or step.get("selector_value") or "",
                        "source": step.get("source") or "text",
                        "attr": step.get("attr") or step.get("attribute") or "",
                        "optional": bool(step.get("optional")),
                    },
                )
    return rules


def apply_value_policy(name: str, value: Any, rule: Dict[str, Any]) -> Any:
    if value is None:
        return None
    if rule.get("redact") or (rule.get("redact") is not False and is_sensitive_var_name(name)):
        return redact_value(value)
    transform = (rule.get("transform") or "").strip().lower()
    if transform == "strip_whitespace" and isinstance(value, str):
        return value.strip()
    if transform == "strip_currency" and isinstance(value, str):
        cleaned = re.sub(r"[^\d.]", "", value)
        try:
            return float(cleaned) if cleaned else value
        except ValueError:
            return cleaned
    vtype = (rule.get("type") or "").strip().lower()
    if vtype == "number" and value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if vtype == "string" and value is not None:
        return str(value)
    if vtype == "boolean" and value is not None:
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "y")
        return bool(value)
    return value


def extract_web_variables(
    page: Any,
    rules: Dict[str, Dict[str, Any]],
    *,
    timeout_ms: int = 5000,
) -> Tuple[Dict[str, Any], List[str]]:
    """从 Playwright Page 按规则抽取。返回 (extracted, missing_required_names)。"""
    extracted: Dict[str, Any] = {}
    missing: List[str] = []
    if not rules:
        return extracted, missing
    if page is None:
        for name, rule in rules.items():
            if not rule.get("optional"):
                missing.append(name)
        return extracted, missing

    for name, rule in rules.items():
        # API 风格 json_path 在 Web 阶段不适用
        if rule.get("json_path") and not rule.get("selector") and not rule.get("source"):
            if not rule.get("optional"):
                missing.append(name)
            continue

        source = (rule.get("source") or "text").strip().lower()
        raw_val: Any = None
        try:
            if source in ("url", "page_url"):
                raw_val = getattr(page, "url", None) or ""
            elif source in ("title", "page_title"):
                raw_val = page.title() if callable(getattr(page, "title", None)) else ""
            elif source == "const" or "value" in rule and not rule.get("selector"):
                raw_val = rule.get("value")
            else:
                sel = (rule.get("selector") or rule.get("selector_value") or "").strip()
                if not sel:
                    if not rule.get("optional"):
                        missing.append(name)
                    continue
                loc = page.locator(sel)
                if source in ("attribute", "attr"):
                    attr = (rule.get("attr") or rule.get("attribute") or "value").strip()
                    raw_val = loc.get_attribute(attr, timeout=timeout_ms)
                elif source in ("input_value", "value"):
                    raw_val = loc.input_value(timeout=timeout_ms)
                elif source in ("inner_text", "text", "innertext"):
                    raw_val = loc.inner_text(timeout=timeout_ms)
                elif source == "text_content":
                    raw_val = loc.text_content(timeout=timeout_ms)
                else:
                    raw_val = loc.inner_text(timeout=timeout_ms)
        except Exception:
            raw_val = None

        if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip() and not rule.get("allow_empty")):
            if not rule.get("optional"):
                missing.append(name)
            continue
        extracted[name] = apply_value_policy(name, raw_val, rule)

    return extracted, missing


def merge_step_extractions(
    step_results: List[Dict[str, Any]],
    steps: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """从步骤结果中收集 extracted_text + store_as。"""
    out: Dict[str, Any] = {}
    steps = steps or []
    for i, row in enumerate(step_results or []):
        if not isinstance(row, dict):
            continue
        store_as = (row.get("store_as") or "").strip()
        text = row.get("extracted_text")
        if text is None:
            text = row.get("text")
        if not store_as and i < len(steps) and isinstance(steps[i], dict):
            store_as = str(
                steps[i].get("store_as")
                or steps[i].get("var_name")
                or steps[i].get("extract_as")
                or ""
            ).strip()
        if store_as and text is not None and str(text).strip() != "":
            rule = _as_rule(store_as, {"source": "text"})
            out[store_as] = apply_value_policy(store_as, text, rule)
    return out


def validate_required_extractions(
    rules: Dict[str, Dict[str, Any]],
    extracted: Dict[str, Any],
) -> List[str]:
    """返回仍缺失的必选变量名。"""
    missing: List[str] = []
    for name, rule in (rules or {}).items():
        if rule.get("optional"):
            continue
        if name not in extracted or extracted.get(name) is None:
            missing.append(name)
        elif isinstance(extracted.get(name), str) and not str(extracted.get(name)).strip():
            if not rule.get("allow_empty"):
                missing.append(name)
    return missing
