# -*- coding: utf-8 -*-
"""SLA 阈值告警（运营提示，非合同达标判定）。

环境变量（可选）：
- ``SLA_ALERT_LATENCY_P95_MS``：超过则 warning（默认 5000）
- ``SLA_ALERT_FAIL_RATIO``：失败率超过则 warning（默认 0.5，需样本≥3）
- ``SLA_ALERT_MIN_SAMPLES``：最少样本（默认 3）

``sla_met`` / ``sla_claim`` 恒为 false。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from .sla_evidence import summarize_sla_evidence


def _fenv(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def evaluate_sla_alerts(*, limit: int = 200) -> Dict[str, Any]:
    summary = summarize_sla_evidence(limit=limit)
    min_samples = int(_fenv("SLA_ALERT_MIN_SAMPLES", 3))
    p95_limit = _fenv("SLA_ALERT_LATENCY_P95_MS", 5000)
    fail_ratio_limit = _fenv("SLA_ALERT_FAIL_RATIO", 0.5)

    alerts: List[Dict[str, Any]] = []
    sample_count = int(summary.get("sample_count") or 0)
    fail_n = int(summary.get("fail_count") or 0)
    ok_n = int(summary.get("ok_count") or 0)
    p95 = summary.get("latency_ms_p95")

    if sample_count < min_samples:
        alerts.append(
            {
                "level": "info",
                "code": "INSUFFICIENT_SAMPLES",
                "message": f"样本不足（{sample_count}/{min_samples}），不触发阈值告警",
            }
        )
    else:
        if p95 is not None and float(p95) > p95_limit:
            alerts.append(
                {
                    "level": "warning",
                    "code": "LATENCY_P95_HIGH",
                    "message": f"p95={p95}ms 超过阈值 {p95_limit}ms",
                    "value": p95,
                    "threshold": p95_limit,
                }
            )
        total = max(1, ok_n + fail_n)
        ratio = fail_n / total
        if ratio > fail_ratio_limit:
            alerts.append(
                {
                    "level": "warning",
                    "code": "FAIL_RATIO_HIGH",
                    "message": f"失败率={ratio:.2f} 超过阈值 {fail_ratio_limit}",
                    "value": round(ratio, 4),
                    "threshold": fail_ratio_limit,
                }
            )

    has_warning = any(a.get("level") == "warning" for a in alerts)
    return {
        "ok": True,
        "sla_claim": False,
        "sla_met": False,
        "has_warning": has_warning,
        "alerts": alerts,
        "thresholds": {
            "latency_p95_ms": p95_limit,
            "fail_ratio": fail_ratio_limit,
            "min_samples": min_samples,
        },
        "summary": {
            "sample_count": sample_count,
            "ok_count": ok_n,
            "fail_count": fail_n,
            "latency_ms_p50": summary.get("latency_ms_p50"),
            "latency_ms_p95": p95,
        },
        "disclaimer": (
            "告警仅为运维提示，不构成合同 SLA 达标或未达标证明；"
            "sla_met/sla_claim 恒为 false。"
        ),
        "doc": "docs/ENTERPRISE_OPS_SLA.md",
    }
