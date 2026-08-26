# -*- coding: utf-8 -*-
"""Unified assertion service for UI/API/cross-end/DB verification.

The goal is not to replace existing engines immediately, but to expose a single
verification entry point that matches the merged execution plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AssertionRequest:
    assertion_type: str
    sources: Dict[str, Any] = field(default_factory=dict)
    expected: Any = None
    tolerance: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = "L0"
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AssertionResponse:
    ok: bool
    assertion_type: str
    message: str = ""
    actual: Any = None
    expected: Any = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


def run_assertion(request: AssertionRequest) -> AssertionResponse:
    assertion_type = (request.assertion_type or "").strip().lower()

    if assertion_type == "cross_end_consistency":
        return _run_cross_end_consistency(request)

    if assertion_type == "db_scalar":
        return _run_db_scalar(request)

    if assertion_type == "manual_stub":
        return AssertionResponse(
            ok=False,
            assertion_type=assertion_type,
            message="断言类型尚未接入统一执行链路",
            actual=None,
            expected=request.expected,
            evidence=request.evidence,
            warnings=["assertion_type_not_wired"],
            meta=request.meta,
        )

    return AssertionResponse(
        ok=False,
        assertion_type=assertion_type,
        message=f"未知的统一断言类型: {request.assertion_type}",
        actual=None,
        expected=request.expected,
        evidence=request.evidence,
        warnings=["unknown_assertion_type"],
        meta=request.meta,
    )


def _run_cross_end_consistency(request: AssertionRequest) -> AssertionResponse:
    try:
        from ai_modules.plan.context_bus import CrossEndContext
        from ai_modules.plan.cross_end_assertion import assert_cross_end_consistency
    except Exception as exc:
        return AssertionResponse(
            ok=False,
            assertion_type=request.assertion_type,
            message=f"跨端断言依赖不可用: {exc}",
            evidence=request.evidence,
            meta=request.meta,
        )

    ctx = CrossEndContext(
        plan_id=str((request.meta or {}).get("plan_id") or ""),
        scenario=str((request.meta or {}).get("scenario") or ""),
    )

    ok, detail = assert_cross_end_consistency(
        ctx,
        str((request.meta or {}).get("field_name") or "value"),
        request.sources,
        tolerance=float(request.tolerance or 0.0),
        expected=request.expected,
        val_type=str((request.meta or {}).get("val_type") or "auto"),
        declared_source_count=int((request.meta or {}).get("declared_source_count") or len(request.sources)),
    )

    return AssertionResponse(
        ok=bool(ok),
        assertion_type=request.assertion_type,
        message=str(detail or ""),
        actual=request.sources,
        expected=request.expected,
        evidence=request.evidence,
        meta=request.meta,
    )


def _run_db_scalar(request: AssertionRequest) -> AssertionResponse:
    try:
        from modules.execution.db_assertion import execute_readonly_scalar_assertion
    except Exception as exc:
        return AssertionResponse(
            ok=False,
            assertion_type=request.assertion_type,
            message=f"DB断言依赖不可用: {exc}",
            evidence=request.evidence,
            meta=request.meta,
        )

    response = execute_readonly_scalar_assertion(
        sql=str((request.meta or {}).get("sql") or ""),
        params=request.meta.get("params") if isinstance(request.meta, dict) else None,
        expected=request.expected,
        connection_name=str((request.meta or {}).get("connection_name") or "default"),
        max_rows=int((request.meta.get("max_rows") or 1) if isinstance(request.meta, dict) else 1),
    )

    return AssertionResponse(
        ok=bool(response.get("ok")),
        assertion_type=request.assertion_type,
        message=str(response.get("message") or ""),
        actual=response.get("actual"),
        expected=request.expected,
        evidence={**request.evidence, "db": response},
        warnings=list(response.get("warnings") or []),
        meta=request.meta,
    )
