# -*- coding: utf-8 -*-
"""Code-change 任务可观测指标聚合。"""

from __future__ import annotations

from typing import Any, Dict, List

from ai_modules.code_intel.task_store import list_tasks


def collect_metrics(limit: int = 200) -> Dict[str, Any]:
    rows = list_tasks(limit=max(1, min(int(limit or 200), 500)))
    by_status: Dict[str, int] = {}
    risk: Dict[str, int] = {}
    total_rec = 0
    total_risk = 0
    total_drafts = 0
    total_heal = 0
    llm_heuristic = 0
    llm_ok = 0
    with_run = 0
    idempotent = 0

    for r in rows:
        st = str(r.get("status") or "unknown")
        by_status[st] = by_status.get(st, 0) + 1
        impact = r.get("impact") if isinstance(r.get("impact"), dict) else {}
        rl = str(impact.get("risk_level") or "unknown")
        risk[rl] = risk.get(rl, 0) + 1
        total_rec += len(r.get("recommended_case_ids") or [])
        total_risk += len(r.get("at_risk_case_ids") or [])
        total_drafts += len(r.get("draft_case_ids") or [])
        total_heal += len(r.get("heal_proposals") or [])
        src = str(impact.get("analysis_source") or "")
        if src == "heuristic":
            llm_heuristic += 1
        elif src == "llm":
            llm_ok += 1
        if r.get("ci_run_id"):
            with_run += 1
        if r.get("idempotent_hit"):
            idempotent += 1

    n = len(rows) or 1
    return {
        "sample_size": len(rows),
        "by_status": by_status,
        "by_risk_level": risk,
        "totals": {
            "recommended_case_refs": total_rec,
            "at_risk_case_refs": total_risk,
            "draft_cases": total_drafts,
            "heal_proposals": total_heal,
            "tasks_with_ci_run": with_run,
        },
        "analysis_source": {
            "llm": llm_ok,
            "heuristic": llm_heuristic,
            "heuristic_ratio": round(llm_heuristic / n, 3),
        },
        "avg_recommended_per_task": round(total_rec / n, 2),
        "avg_at_risk_per_task": round(total_risk / n, 2),
    }
