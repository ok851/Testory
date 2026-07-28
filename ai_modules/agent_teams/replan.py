# -*- coding: utf-8 -*-
"""失败重规划（R16）：Verifier → Planner 回边，确定性/可测。

诚实约束：
- 重规划后必须重新执行并经 Verifier；不得因「已重规划」判绿
- 默认最多 1 次；可用环境变量 AGENT_TEAMS_MAX_REPLAN 调整
- 结合 IncidentMemory 建议仅作 plan 注释/步骤放宽提示，不伪造通过
"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional, Tuple


def max_replan_attempts() -> int:
    raw = (os.environ.get("AGENT_TEAMS_MAX_REPLAN") or "1").strip()
    try:
        return max(0, min(int(raw), 3))
    except ValueError:
        return 1


def _failed_stages(stage_results: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in stage_results or []:
        if not isinstance(s, dict):
            continue
        if s.get("cleanup"):
            continue
        if s.get("ok_assert") is False:
            out.append(s)
    return out


def build_replan_feedback(
    *,
    report: Optional[Dict[str, Any]] = None,
    execution: Optional[Dict[str, Any]] = None,
    stage_results: Optional[List[Any]] = None,
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """汇总失败上下文，供 Planner / IncidentMemory。"""
    report = report or {}
    execution = execution or {}
    fails = _failed_stages(list(stage_results or []))
    reason = (
        report.get("reason")
        or execution.get("error")
        or (errors[-1] if errors else "")
        or "验证未通过"
    )
    codes = []
    if execution.get("error_code"):
        codes.append(str(execution.get("error_code")))
    for s in fails:
        if s.get("error_code"):
            codes.append(str(s.get("error_code")))
    return {
        "reason": str(reason),
        "error_codes": list(dict.fromkeys(codes)),
        "failed_stage_ids": [str(s.get("stage_id") or s.get("id") or "") for s in fails],
        "failed_stages": fails,
        "assertion_failed": int(execution.get("assertion_failed") or 0),
    }


def propose_replan(
    plan: Dict[str, Any],
    feedback: Dict[str, Any],
    *,
    suggestions: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """基于失败反馈提出修订 plan；无有效修改则返回 (None, meta)。"""
    if not isinstance(plan, dict) or not plan.get("stages"):
        return None, {"reason": "empty_plan"}

    new_plan = copy.deepcopy(plan)
    meta: Dict[str, Any] = {"strategies": [], "suggestions_used": 0}
    stages = list(new_plan.get("stages") or [])
    changed = False
    failed_ids = set(feedback.get("failed_stage_ids") or [])
    codes = {str(c).upper() for c in (feedback.get("error_codes") or [])}

    for st in stages:
        if not isinstance(st, dict):
            continue
        sid = str(st.get("id") or st.get("stage_id") or "")
        layer = str(st.get("layer") or st.get("automation_layer") or "").lower()
        if sid and failed_ids and sid not in failed_ids:
            # 仍可对全 plan 做桌面/HITL 通用加固
            pass

        # Desktop：放宽 attach 标题
        if layer in ("desktop", "win", "windows") or str(st.get("skill") or "").find("desktop") >= 0:
            steps = st.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    act = str(step.get("action") or "").lower()
                    spec = step.get("desktop_spec") if isinstance(step.get("desktop_spec"), dict) else {}
                    if act == "attach_window":
                        tre = str(spec.get("window_title_re") or "").strip()
                        if tre.startswith("^") and tre.endswith("$") and ".*" not in tre:
                            body = tre[1:-1]
                            if body.startswith("(?i)"):
                                body = body[4:]
                            spec = dict(spec)
                            spec["window_title_re"] = f".*{body.strip('^$')}.*"
                            spec["best_match"] = True
                            step["desktop_spec"] = spec
                            changed = True
                            meta["strategies"].append("broaden_desktop_attach")
                        elif not tre and spec.get("window_title"):
                            wt = str(spec.get("window_title"))
                            spec = dict(spec)
                            spec["window_title_re"] = f".*{wt}.*"
                            spec["best_match"] = True
                            step["desktop_spec"] = spec
                            changed = True
                            meta["strategies"].append("desktop_title_to_re")

        # HITL：略增超时（仅失败相关）
        hitl = st.get("hitl")
        if isinstance(hitl, dict) and (
            "HITL_TIMEOUT" in codes or "hitl" in str(feedback.get("reason") or "").lower()
        ):
            try:
                to = float(hitl.get("timeout_s") or hitl.get("timeout") or 30)
            except (TypeError, ValueError):
                to = 30.0
            hitl = dict(hitl)
            hitl["timeout_s"] = min(to * 2, 300.0)
            hitl["_replanned"] = True
            st["hitl"] = hitl
            changed = True
            meta["strategies"].append("extend_hitl_timeout")

        # API：失败阶段标记重试提示（编排若支持 recovery）
        if layer == "api" and (not failed_ids or sid in failed_ids):
            if not st.get("on_failure"):
                st["on_failure"] = "retry"
                changed = True
                meta["strategies"].append("api_on_failure_retry")

    # 注入 incident/runbook 建议到 plan.meta（不改变成功语义）
    tips = []
    for s in suggestions or []:
        if not isinstance(s, dict):
            continue
        title = s.get("title") or s.get("id")
        body = s.get("body") or ""
        if title:
            tips.append(f"{title}: {body}"[:240])
            meta["suggestions_used"] += 1
    if tips:
        meta_blob = new_plan.get("meta") if isinstance(new_plan.get("meta"), dict) else {}
        meta_blob = dict(meta_blob)
        meta_blob["replan_tips"] = tips[:5]
        meta_blob["replan_feedback"] = {
            "reason": feedback.get("reason"),
            "error_codes": feedback.get("error_codes"),
        }
        new_plan["meta"] = meta_blob
        changed = True
        meta["strategies"].append("attach_incident_tips")

    if not changed:
        return None, {**meta, "reason": "no_replan_delta"}

    # 标记代数，避免无限循环误判
    new_plan["plan_id"] = str(new_plan.get("plan_id") or "plan") + f"-replan"
    new_plan["replan_generation"] = int(plan.get("replan_generation") or 0) + 1
    meta["strategies"] = list(dict.fromkeys(meta["strategies"]))
    return new_plan, meta
