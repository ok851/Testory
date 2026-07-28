# -*- coding: utf-8 -*-
"""SLA 证据指标（非达标证明）。

记录探测/作业延迟样本，供运营查看；``sla_claim`` 恒为 false。
商务可用性以合同为准。
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _root() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        base = Path(env).expanduser().resolve()
    else:
        base = Path(__file__).resolve().parents[2] / "data"
    d = base / "enterprise_metrics"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store() -> Path:
    return _root() / "sla_evidence.jsonl"


def record_metric(
    *,
    kind: str,
    ok: bool,
    latency_ms: Optional[float] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": (kind or "unknown").strip()[:64],
        "ok": bool(ok),
        "latency_ms": None if latency_ms is None else round(float(latency_ms), 2),
        "meta": dict(meta or {}),
        "sla_claim": False,
    }
    path = _store()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def list_metrics(limit: int = 100) -> List[Dict[str, Any]]:
    path = _store()
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(rows) >= max(1, min(int(limit or 100), 500)):
            break
    return rows


def summarize_sla_evidence(limit: int = 200) -> Dict[str, Any]:
    rows = list_metrics(limit=limit)
    latencies = [
        float(r["latency_ms"])
        for r in rows
        if r.get("latency_ms") is not None
    ]
    ok_n = sum(1 for r in rows if r.get("ok"))
    fail_n = sum(1 for r in rows if not r.get("ok"))
    p50 = statistics.median(latencies) if latencies else None
    p95 = None
    if len(latencies) >= 2:
        ordered = sorted(latencies)
        idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
        p95 = ordered[idx]
    elif latencies:
        p95 = latencies[0]

    return {
        "ok": True,
        "sla_claim": False,
        "sample_count": len(rows),
        "ok_count": ok_n,
        "fail_count": fail_n,
        "latency_ms_p50": p50,
        "latency_ms_p95": p95,
        "recent": rows[:20],
        "disclaimer": (
            "本摘要是运维证据样本，不是 SLA 达标证明；"
            "不得因 p50/p95 或 ok_count 对外宣称合同 SLA 已满足。"
        ),
        "doc": "docs/ENTERPRISE_OPS_SLA.md",
    }
