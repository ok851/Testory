# -*- coding: utf-8 -*-
"""运行历史 API  enrichment：门禁摘要 / 证据包句柄 / CI 构建关联。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _parse_json_obj(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_ci_meta_into_expected(
    expected_text: str = "",
    *,
    ci_run_id: str = "",
    build_id: str = "",
    trigger_source: str = "",
    git_sha: str = "",
    branch: str = "",
) -> str:
    """把 CI 关联写入 expected_text（JSON 信封；保留原非 JSON 文本）。"""
    base = _parse_json_obj(expected_text)
    if not base and (expected_text or "").strip():
        base = {"legacy_expected": str(expected_text)[:2000]}
    if ci_run_id:
        base["ci_run_id"] = str(ci_run_id)
    if build_id:
        base["build_id"] = str(build_id)
    if trigger_source:
        base["trigger_source"] = str(trigger_source)
    if git_sha:
        base["git_sha"] = str(git_sha)
    if branch:
        base["branch"] = str(branch)
    if not any(base.get(k) for k in ("ci_run_id", "build_id", "trigger_source", "audit_id", "plan_id")):
        return expected_text or ""
    return json.dumps(base, ensure_ascii=False)


def ci_meta_from_execution_context(ctx: Any) -> Dict[str, str]:
    if ctx is None:
        return {}
    extra = getattr(ctx, "extra", None) or {}
    if not isinstance(extra, dict):
        return {}
    out = {}
    for k_src, k_dst in (
        ("ci_run_id", "ci_run_id"),
        ("build_id", "build_id"),
        ("trigger_source", "trigger_source"),
        ("git_sha", "git_sha"),
        ("branch", "branch"),
    ):
        v = extra.get(k_src)
        if v is not None and str(v).strip():
            out[k_dst] = str(v).strip()
    return out


def enrich_run_history_record(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """为历史详情补充 ops_summary（HITL/Risk）与证据/CI 句柄。"""
    if not isinstance(record, dict):
        return {"ops_summary": {}, "links": {}}
    meta = _parse_json_obj(record.get("extracted_text"))
    links = _parse_json_obj(record.get("expected_text"))
    # 兼容：audit_id 也可能只在 meta
    audit_id = str(links.get("audit_id") or meta.get("audit_id") or "").strip()
    agent_run_id = str(links.get("agent_run_id") or meta.get("agent_run_id") or "").strip()
    hitl = meta.get("hitl") if isinstance(meta.get("hitl"), list) else []
    risk = meta.get("risk") if isinstance(meta.get("risk"), list) else []
    ops = {
        "test_type": record.get("test_type") or meta.get("test_type") or "web",
        "error_code": meta.get("error_code") or "",
        "gate_passed": meta.get("gate_passed"),
        "hitl": hitl,
        "risk": risk,
        "hitl_count": len(hitl),
        "risk_count": len(risk),
        "stage_count": meta.get("stage_count"),
        "trigger_source": links.get("trigger_source") or meta.get("trigger_source") or "",
    }
    link_blob = {
        "audit_id": audit_id or None,
        "agent_run_id": agent_run_id or None,
        "plan_id": links.get("plan_id") or meta.get("plan_id") or None,
        "ci_run_id": links.get("ci_run_id") or None,
        "build_id": links.get("build_id") or None,
        "git_sha": links.get("git_sha") or None,
        "branch": links.get("branch") or None,
        "trace_export_url": (
            f"/api/ai/trace-packs/export?format=zip&audit_id={audit_id}"
            if audit_id
            else (
                f"/api/ai/trace-packs/export?format=zip&run_history_id={record.get('id')}"
                if record.get("id")
                else None
            )
        ),
        "ci_run_url": (
            f"/api/ci/runs/{links.get('ci_run_id')}"
            if links.get("ci_run_id")
            else None
        ),
    }
    record = dict(record)
    record["ops_summary"] = ops
    record["links"] = link_blob
    # 列表友好预览：不要把整段 JSON 当「输出」
    if meta.get("schema") == "cross_end_run_audit/v1" or ops.get("test_type") in (
        "cross_end",
        "agent_teams",
    ):
        bits: List[str] = [str(ops.get("test_type") or "cross_end")]
        if ops.get("hitl_count"):
            bits.append(f"HITL×{ops['hitl_count']}")
        if ops.get("risk_count"):
            bits.append(f"Risk×{ops['risk_count']}")
        if link_blob.get("build_id"):
            bits.append(f"build={link_blob['build_id']}")
        record["output_preview"] = " · ".join(bits)
    elif link_blob.get("build_id") or link_blob.get("ci_run_id"):
        bits = []
        if link_blob.get("build_id"):
            bits.append(f"CI build={link_blob['build_id']}")
        if link_blob.get("ci_run_id"):
            bits.append(f"run={link_blob['ci_run_id']}")
        record["output_preview"] = " · ".join(bits)
    return record


def aggregate_ops_governance(
    records: Optional[List[Dict[str, Any]]],
    *,
    recent_limit: int = 10,
) -> Dict[str, Any]:
    """从已 enrich（或原始）历史行聚合治理看板指标。不美化：假绿相关计入 gate_blocked。"""
    rows = list(records or [])
    enriched = [enrich_run_history_record(r) if "ops_summary" not in (r or {}) else r for r in rows]

    total = len(enriched)
    with_hitl = 0
    with_risk = 0
    gate_blocked = 0
    with_evidence = 0
    with_ci = 0
    cross_end = 0
    agent_teams = 0
    error_codes: Dict[str, int] = {}
    recent_gate: List[Dict[str, Any]] = []

    for rec in enriched:
        ops = rec.get("ops_summary") if isinstance(rec.get("ops_summary"), dict) else {}
        links = rec.get("links") if isinstance(rec.get("links"), dict) else {}
        tt = str(ops.get("test_type") or rec.get("test_type") or "web").strip().lower()
        if tt == "cross_end":
            cross_end += 1
        elif tt == "agent_teams":
            agent_teams += 1
        if int(ops.get("hitl_count") or 0) > 0:
            with_hitl += 1
        if int(ops.get("risk_count") or 0) > 0:
            with_risk += 1
        gp = ops.get("gate_passed")
        code = str(ops.get("error_code") or "").strip()
        blocked = gp is False or code in (
            "RISK_APPROVAL_REQUIRED",
            "HITL_TIMEOUT",
            "HITL_CANCELLED",
            "HITL_WAIT",
        )
        if blocked:
            gate_blocked += 1
        if links.get("audit_id") or links.get("trace_export_url"):
            with_evidence += 1
        if links.get("build_id") or links.get("ci_run_id"):
            with_ci += 1
        if code:
            error_codes[code] = error_codes.get(code, 0) + 1
        if blocked and len(recent_gate) < max(1, int(recent_limit or 10)):
            recent_gate.append(
                {
                    "id": rec.get("id"),
                    "status": rec.get("status"),
                    "case_name": rec.get("case_name") or rec.get("flow_name") or "",
                    "test_type": tt,
                    "error_code": code or None,
                    "gate_passed": gp,
                    "hitl_count": ops.get("hitl_count") or 0,
                    "risk_count": ops.get("risk_count") or 0,
                    "build_id": links.get("build_id"),
                    "audit_id": links.get("audit_id"),
                    "trace_export_url": links.get("trace_export_url"),
                    "created_at": rec.get("created_at"),
                    "history_url": f"/run-history?id={rec.get('id')}" if rec.get("id") else "/run-history",
                }
            )

    top_codes = sorted(error_codes.items(), key=lambda x: (-x[1], x[0]))[:8]
    return {
        "scanned_runs": total,
        "cross_end_runs": cross_end,
        "agent_teams_runs": agent_teams,
        "with_hitl": with_hitl,
        "with_risk": with_risk,
        "gate_blocked": gate_blocked,
        "with_evidence": with_evidence,
        "with_ci": with_ci,
        "top_error_codes": [{"code": c, "count": n} for c, n in top_codes],
        "recent_gate_events": recent_gate,
        "honesty_note": "gate_blocked 含 gate_passed=false 与 HITL/Risk 阻断码；不计入通过率美化。",
    }
