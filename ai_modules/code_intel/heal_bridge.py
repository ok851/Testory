# -*- coding: utf-8 -*-
"""at_risk 用例 → 执行失败后自愈提案（不自动写库绿灯）。"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_heal_proposals_from_run(
    *,
    task_id: str,
    git_sha: str,
    at_risk_case_ids: List[int],
    ci_run: Optional[Dict[str, Any]],
    db: Any = None,
) -> List[Dict[str, Any]]:
    """
    根据 CI run 中失败的 at_risk 用例，生成 heal 提案。
    提案仅建议；需人工 apply，禁止假绿。
    """
    if not ci_run or not at_risk_case_ids:
        return []

    at_set = {int(x) for x in at_risk_case_ids}
    case_rows = ci_run.get("cases") or ci_run.get("case_rows") or []
    proposals: List[Dict[str, Any]] = []

    for row in case_rows:
        if not isinstance(row, dict):
            continue
        try:
            cid = int(row.get("case_id"))
        except (TypeError, ValueError):
            continue
        if cid not in at_set:
            continue
        if row.get("gate_passed") or str(row.get("ci_status") or "") == "passed":
            continue
        # 失败 → 提案
        steps = []
        if db is not None:
            try:
                steps = db.get_case_steps(cid) or []
            except Exception:
                steps = []
        analysis = None
        try:
            from ai_modules.optimize.self_heal import analyze_steps_for_self_heal

            if steps:
                analysis = analyze_steps_for_self_heal(steps)
        except Exception:
            analysis = None

        proposals.append({
            "proposal_id": f"hp-{uuid.uuid4().hex[:12]}",
            "task_id": task_id,
            "case_id": cid,
            "case_name": row.get("case_name") or "",
            "git_sha": git_sha or "",
            "status": "pending_review",
            "created_at": _now(),
            "error": (row.get("error") or "")[:2000],
            "ci_status": row.get("ci_status") or row.get("status"),
            "suggestion": (
                "用例因代码变更标记为 at_risk 且执行失败。"
                "请使用现有 Self-heal Hub / 视觉定位复核选择器后人工确认，"
                "勿将未验证修复直接纳入 CI 绿灯。"
            ),
            "self_heal_analysis": analysis,
            "applied": False,
            "marketing_claim_allowed": False,
        })
    return proposals


def mark_cases_at_risk_meta(
    impact: Dict[str, Any],
    at_risk_case_ids: List[int],
) -> Dict[str, Any]:
    """供任务记录展示的 at_risk 摘要（不改用例表）。"""
    return {
        "at_risk_case_ids": list(at_risk_case_ids),
        "risk_level": impact.get("risk_level"),
        "may_break_existing_cases": bool(impact.get("may_break_existing_cases")),
        "policy": "execute_then_heal_on_failure_pending_review",
        "auto_write_forbidden": True,
    }


def apply_heal_proposal_noop(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Phase3 MVP：不自动改步骤。返回需人工到 Self-heal Hub 处理的指引。
    """
    return {
        "ok": True,
        "applied": False,
        "proposal_id": proposal.get("proposal_id"),
        "case_id": proposal.get("case_id"),
        "message": (
            "自愈提案已确认「需人工处理」。"
            "请打开 AI Hub Self-heal 对用例步骤做 analyze/verify；"
            "平台不会因代码变更预改定位器并宣称通过。"
        ),
        "hub_hint": "/ai-hub 或 POST /api/ai/hub/heal/analyze-steps",
    }
