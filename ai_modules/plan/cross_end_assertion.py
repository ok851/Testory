# -*- coding: utf-8 -*-
"""跨端一致性断言：从上下文变量 / UI 选择器解析多源，失败挡总成功。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .context_bus import CrossEndContext

_VAR_REF = re.compile(r"^\{\{(.+?)\}\}$")


def _norm_value(val: Any, val_type: str = "auto") -> Any:
    if val is None:
        return None
    if val_type == "number" or (
        val_type == "auto" and isinstance(val, str) and _looks_number(val)
    ):
        try:
            return float(str(val).replace(",", "").strip())
        except (ValueError, TypeError):
            return val if val_type != "number" else None
    if val_type == "string":
        return str(val)
    if val_type == "boolean":
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes")
        return bool(val)
    return val


def _looks_number(s: str) -> bool:
    s = (s or "").replace(",", "").strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_missing(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str) and not val.strip():
        return True
    return False


def _lookup_context_var(context: CrossEndContext, key: str) -> Any:
    key = (key or "").strip()
    if not key:
        return None
    m = _VAR_REF.match(key)
    if m:
        key = m.group(1).strip()
    val = context.get_variable(key)
    if val is not None:
        return val
    try:
        return context._resolve_key(key)
    except Exception:
        return None


def _read_web_selector(spec: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
    """从当前浏览器页面读取 UI 源。无 page → 明确失败原因。"""
    selector = str(spec.get("selector") or spec.get("css") or "").strip()
    if not selector:
        return None, "UI 源缺少 selector"
    try:
        from browser_manager import get_page

        page = get_page()
    except Exception:
        page = None
    if page is None:
        return None, "UI 源需要浏览器页面，但 get_page() 为空"

    source = str(spec.get("source") or spec.get("attr_source") or "text").strip().lower()
    timeout_ms = int(spec.get("timeout_ms") or 5000)
    try:
        loc = page.locator(selector)
        if source in ("attribute", "attr"):
            attr = str(spec.get("attr") or spec.get("attribute") or "value").strip()
            text = loc.get_attribute(attr, timeout=timeout_ms)
        elif source in ("input_value", "value"):
            text = loc.input_value(timeout=timeout_ms)
        else:
            text = loc.inner_text(timeout=timeout_ms)
        text = (text or "").strip()
        if not text and not spec.get("allow_empty"):
            return None, f"UI 选择器未读到非空文本: {selector}"
        return text, None
    except Exception as e:
        return None, f"UI 读取失败 ({selector}): {e}"


def resolve_assertion_source(
    context: CrossEndContext,
    label: str,
    raw: Any,
) -> Tuple[Any, Optional[str]]:
    """
    解析单个断言来源。
    支持：字面量 / 变量名 / {{var}} / {var|variable|path} / {selector} UI 读取。
    """
    if raw is None:
        return None, f"来源 '{label}' 为空"
    if isinstance(raw, (int, float, bool)):
        return raw, None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, f"来源 '{label}' 为空字符串"
        # 纯字面量（带引号）或明显非变量
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            return s[1:-1], None
        if "{{" in s:
            resolved = context.resolve(s)
            if resolved == s and _VAR_REF.match(s):
                # 未替换成功
                return None, f"来源 '{label}' 变量未找到: {s}"
            # resolve 可能留下未替换片段
            if "{{" in str(resolved):
                return None, f"来源 '{label}' 仍含未解析变量: {resolved}"
            return resolved, None
        # 当作上下文变量键
        val = _lookup_context_var(context, s)
        if val is not None:
            return val, None
        # 找不到变量时：若像 CSS 选择器则尝试 UI（#/./[）
        if s[:1] in ("#", ".", "[") or s.startswith("//") or ":" in s:
            return _read_web_selector({"selector": s})
        return None, f"来源 '{label}' 变量未找到: {s}"

    if isinstance(raw, dict):
        if raw.get("selector") or raw.get("css"):
            return _read_web_selector(raw)
        var_key = (
            raw.get("var")
            or raw.get("variable")
            or raw.get("path")
            or raw.get("key")
            or raw.get("from")
        )
        if var_key:
            val = _lookup_context_var(context, str(var_key))
            if val is None:
                return None, f"来源 '{label}' 变量未找到: {var_key}"
            return val, None
        if "value" in raw or "literal" in raw:
            return raw.get("value", raw.get("literal")), None
        return None, f"来源 '{label}' 配置无法解析（需 var / selector / value）"

    return raw, None


def resolve_assertion_sources(
    context: CrossEndContext,
    sources_spec: Any,
) -> Tuple[Dict[str, Any], List[str]]:
    """返回 (resolved_map, error_messages)。任一来源失败则 errors 非空。"""
    errors: List[str] = []
    resolved: Dict[str, Any] = {}
    if not sources_spec:
        return {}, ["断言未声明 sources"]
    if isinstance(sources_spec, list):
        # ["api_balance", "web_balance"] → 同名变量
        as_dict = {str(i): v for i, v in enumerate(sources_spec)}
        # better: use the string itself as label
        as_dict = {}
        for item in sources_spec:
            if isinstance(item, str):
                as_dict[item] = item
            elif isinstance(item, dict):
                label = str(item.get("name") or item.get("label") or item.get("var") or len(as_dict))
                as_dict[label] = item
            else:
                as_dict[str(len(as_dict))] = item
        sources_spec = as_dict
    if not isinstance(sources_spec, dict):
        return {}, ["sources 必须是对象或列表"]

    for label, raw in sources_spec.items():
        val, err = resolve_assertion_source(context, str(label), raw)
        if err:
            errors.append(err)
            continue
        if _is_missing(val):
            errors.append(f"来源 '{label}' 解析结果为空")
            continue
        resolved[str(label)] = val
    return resolved, errors


def assert_cross_end_consistency(
    context: CrossEndContext,
    field_name: str,
    sources: Dict[str, Any],
    tolerance: float = 0.01,
    *,
    expected: Any = None,
    val_type: str = "auto",
    declared_source_count: int = 0,
) -> Tuple[bool, str]:
    """
    比较多源值。Y6 契约：
    - 声明了 ≥2 个来源但有效值不足 → 失败（不得「跳过比较」当绿）
    - 单源 + expected → 与期望比较
    - 单源无 expected 且声明仅 1 源 → 失败（不完整）
    """
    normed: Dict[str, Any] = {
        k: _norm_value(v, val_type) for k, v in (sources or {}).items()
    }
    values = list(normed.values())
    if not values:
        msg = f"字段 '{field_name}' 无任何来源数据"
        context.add_assertion(f"{field_name} 跨端一致性", False, msg)
        return False, msg

    if all(v is None for v in values):
        msg = f"字段 '{field_name}' 全部来源均为空"
        context.add_assertion(f"{field_name} 跨端一致性", False, msg)
        return False, msg

    non_none = [v for v in values if v is not None]
    declared = declared_source_count or len(sources)

    # 显式 expected：与所有源比较
    if expected is not None:
        exp = _norm_value(expected, val_type)
        for label, v in normed.items():
            if v is None:
                continue
            if isinstance(exp, (int, float)) and isinstance(v, (int, float)):
                if abs(float(v) - float(exp)) > tolerance:
                    msg = f"与期望不一致: {label}={v!r} expected={exp!r}"
                    context.add_assertion(f"{field_name} 跨端一致性", False, msg)
                    return False, msg
            elif str(v) != str(exp):
                msg = f"与期望不一致: {label}={v!r} expected={exp!r}"
                context.add_assertion(f"{field_name} 跨端一致性", False, msg)
                return False, msg
        context.add_assertion(
            f"{field_name} 跨端一致性", True, f"与期望一致: {list(normed.items())} == {exp!r}"
        )
        return True, f"与期望一致: {exp!r}"

    if declared >= 2 and len(non_none) < 2:
        msg = (
            f"字段 '{field_name}' 声明了 {declared} 个来源，但仅 {len(non_none)} 个有效，"
            "不得跳过比较当绿"
        )
        context.add_assertion(f"{field_name} 跨端一致性", False, msg)
        return False, msg

    if len(non_none) < 2:
        msg = (
            f"字段 '{field_name}' 仅有一个来源且未提供 expected，断言不完整"
        )
        context.add_assertion(f"{field_name} 跨端一致性", False, msg)
        return False, msg

    if all(isinstance(v, (int, float)) for v in non_none):
        ref = non_none[0]
        for v in non_none[1:]:
            if abs(float(v) - float(ref)) > tolerance:
                msg = f"数值不一致: {list(normed.items())} (容差={tolerance})"
                context.add_assertion(f"{field_name} 跨端一致性", False, msg)
                return False, msg
        context.add_assertion(
            f"{field_name} 跨端一致性", True, f"数值一致: {list(normed.items())}"
        )
        return True, f"数值一致: {list(normed.items())}"

    # 统一转字符串比较（UI 文本 vs API）
    str_vals = [str(v).strip() for v in non_none]
    ref = str_vals[0]
    for v in str_vals[1:]:
        if v != ref:
            msg = f"字符串不一致: {list(normed.items())}"
            context.add_assertion(f"{field_name} 跨端一致性", False, msg)
            return False, msg
    context.add_assertion(
        f"{field_name} 跨端一致性", True, f"字符串一致: {list(normed.items())}"
    )
    return True, f"字符串一致: {list(normed.items())}"


def _normalize_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """兼容 left/right、api/web 简写。"""
    out = dict(rule)
    sources = out.get("sources")
    if not sources:
        sources = {}
        if "left" in out or "right" in out:
            if "left" in out:
                sources["left"] = out.get("left")
            if "right" in out:
                sources["right"] = out.get("right")
        for key in ("api", "web", "mobile", "desktop", "ui"):
            if key in out and key not in ("field", "label", "tolerance", "expected", "type"):
                sources[key] = out.get(key)
        out["sources"] = sources
    return out


def run_cross_end_assertions(
    context: CrossEndContext,
    assertion_rules: List[Dict[str, Any]],
) -> Tuple[int, int, List[Dict[str, Any]]]:
    """
    执行跨端断言列表。
    返回 (passed, failed, details)；失败会写入 context._assertions，evaluate_pass 会挡成功。
    """
    passed = 0
    failed = 0
    details: List[Dict[str, Any]] = []

    for rule in assertion_rules or []:
        if not isinstance(rule, dict):
            failed += 1
            details.append({
                "ok": False,
                "field": "invalid",
                "error": "断言规则必须是对象",
                "error_code": "ASSERT_RULE_INVALID",
            })
            context.add_assertion("无效断言规则", False, "断言规则必须是对象")
            continue

        rule = _normalize_rule(rule)
        field_name = str(rule.get("field") or rule.get("label") or "unknown")
        sources_spec = rule.get("sources") or {}
        declared_count = (
            len(sources_spec)
            if isinstance(sources_spec, dict)
            else (len(sources_spec) if isinstance(sources_spec, list) else 0)
        )
        tolerance = float(rule.get("tolerance", 0.01) or 0.01)
        expected = rule.get("expected", rule.get("equals"))
        val_type = str(rule.get("type") or rule.get("value_type") or "auto")

        resolved, resolve_errors = resolve_assertion_sources(context, sources_spec)
        if resolve_errors:
            failed += 1
            msg = f"{field_name}: " + "; ".join(resolve_errors)
            context.add_assertion(f"{field_name} 跨端一致性", False, msg)
            details.append({
                "ok": False,
                "field": field_name,
                "error": msg,
                "error_code": "ASSERT_SOURCE_MISSING",
                "sources_resolved": resolved,
            })
            continue

        ok, detail = assert_cross_end_consistency(
            context,
            field_name,
            resolved,
            tolerance=tolerance,
            expected=expected,
            val_type=val_type,
            declared_source_count=declared_count,
        )
        row = {
            "ok": ok,
            "field": field_name,
            "detail": detail,
            "sources_resolved": resolved,
            "error_code": None if ok else "CROSS_END_ASSERT_FAILED",
        }
        if ok:
            passed += 1
        else:
            failed += 1
            row["error"] = detail
        details.append(row)

    return passed, failed, details
