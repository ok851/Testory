from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .context_bus import CrossEndContext


def _norm_value(val: Any, val_type: str = "string") -> Any:
    if val is None:
        return None
    if val_type == "number":
        try:
            return float(val)
        except (ValueError, TypeError):
            return val
    if val_type == "string":
        return str(val)
    if val_type == "boolean":
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes")
        return bool(val)
    return val


def assert_cross_end_consistency(
    context: CrossEndContext,
    field_name: str,
    sources: Dict[str, Any],
    tolerance: float = 0.01,
) -> Tuple[bool, str]:
    normed: Dict[str, Any] = {}
    for src_label, src_val in sources.items():
        normed[src_label] = _norm_value(src_val)

    values = list(normed.values())
    if not values:
        return False, f"字段 '{field_name}' 无任何来源数据"

    if all(v is None for v in values):
        return False, f"字段 '{field_name}' 全部来源均为空"

    non_none = [v for v in values if v is not None]

    if all(isinstance(v, (int, float)) for v in non_none):
        if len(non_none) < 2:
            return True, f"{field_name}: 仅一个数值来源，跳过比较"
        ref = non_none[0]
        for i, v in enumerate(non_none[1:], 1):
            if abs(v - ref) > tolerance:
                context.add_assertion(
                    f"{field_name} 跨端一致性",
                    False,
                    f"数值不一致: {list(normed.items())} (容差={tolerance})",
                )
                return False, f"数值不一致: {list(normed.items())}"
        context.add_assertion(
            f"{field_name} 跨端一致性", True, f"数值一致: {list(normed.items())}"
        )
        return True, f"数值一致: {list(normed.items())}"

    if all(isinstance(v, str) for v in non_none):
        if len(non_none) < 2:
            return True, f"{field_name}: 仅一个字符串来源，跳过比较"
        ref = non_none[0]
        for i, v in enumerate(non_none[1:], 1):
            if v != ref:
                context.add_assertion(
                    f"{field_name} 跨端一致性",
                    False,
                    f"字符串不一致: {list(normed.items())}",
                )
                return False, f"字符串不一致: {list(normed.items())}"
        context.add_assertion(
            f"{field_name} 跨端一致性", True, f"字符串一致: {list(normed.items())}"
        )
        return True, f"字符串一致: {list(normed.items())}"

    first_type = type(non_none[0]).__name__
    context.add_assertion(
        f"{field_name} 跨端一致性",
        False,
        f"类型不统一 ({first_type}): {list(normed.items())}",
    )
    return False, f"类型不统一 ({first_type}): {list(normed.items())}"


def run_cross_end_assertions(
    context: CrossEndContext,
    assertion_rules: List[Dict[str, Any]],
) -> Tuple[int, int]:

    passed = 0
    failed = 0
    for rule in assertion_rules:
        field_name = rule.get("field", rule.get("label", "unknown"))
        sources = rule.get("sources", {})
        tolerance = float(rule.get("tolerance", 0.01))
        ok, detail = assert_cross_end_consistency(
            context, field_name, sources, tolerance=tolerance
        )
        if ok:
            passed += 1
        else:
            failed += 1
    return passed, failed
