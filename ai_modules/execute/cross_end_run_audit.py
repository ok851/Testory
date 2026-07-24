# -*- coding: utf-8 -*-
"""跨端 / AgentTeams 运行审计：诚实写入 run_history + 文件证据（Phase B-1 / Z1）。

原则（对齐 EXECUTION_RELIABILITY_STANDARD S1/S3）：
- 仅当执行结果 success=True 才记 status=success
- 失败时 error 非空；含稳定 error_code
- 无 case_id 也可落库（test_type=cross_end|agent_teams），不得被 orphan 清理误删
- 文件审计始终尝试写入（DB 失败不吞业务结果）
- 变量摘要脱敏，禁止把 token/password 明文写入历史
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROTECTED_TEST_TYPES = frozenset({"cross_end", "agent_teams"})
_SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|access_key|api_key|authorization|cookie|credential)",
    re.I,
)


def is_protected_history_test_type(test_type: Any) -> bool:
    return str(test_type or "").strip().lower() in _PROTECTED_TEST_TYPES


def normalize_cross_end_history_status(result: Optional[Dict[str, Any]]) -> str:
    """映射为 run_history 状态词汇：success | failed。

    门禁：仅 result.success is True → success；其余一律 failed（含 warning/skip 语义已在编排层挡掉）。
    """
    if not isinstance(result, dict):
        return "failed"
    if result.get("success") is True:
        # 双保险：gate_passed 显式 False 不得绿
        if result.get("gate_passed") is False:
            return "failed"
        return "success"
    return "failed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit_dir() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2] / "data"
    d = root / "cross_end_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def redact_vars_for_history(variables: Any, *, max_keys: int = 40) -> Dict[str, Any]:
    """写入历史的变量摘要：敏感键脱敏，截断数量。"""
    if not isinstance(variables, dict):
        return {}
    out: Dict[str, Any] = {}
    for i, (k, v) in enumerate(variables.items()):
        if i >= max_keys:
            out["__truncated__"] = True
            break
        key = str(k)
        if _SENSITIVE_KEY.search(key):
            out[key] = "***"
            continue
        try:
            from ai_modules.plan.var_extraction import is_sensitive_var_name, redact_value

            if is_sensitive_var_name(key):
                out[key] = redact_value(v)
                continue
        except Exception:
            pass
        if isinstance(v, (str, int, float, bool)) or v is None:
            s = str(v) if v is not None else ""
            out[key] = s if len(s) <= 200 else (s[:200] + "…")
        else:
            out[key] = f"<{type(v).__name__}>"
    return out


def compute_duration_sec(result: Optional[Dict[str, Any]], *, fallback: float = 0.0) -> float:
    if not isinstance(result, dict):
        return float(fallback or 0.0)
    if result.get("duration") is not None:
        try:
            return max(0.0, float(result.get("duration")))
        except (TypeError, ValueError):
            pass
    if result.get("elapsed_ms") is not None:
        try:
            return max(0.0, float(result.get("elapsed_ms")) / 1000.0)
        except (TypeError, ValueError):
            pass
    started = result.get("started_at") or ""
    finished = result.get("finished_at") or ""
    if started and finished:
        try:
            a = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            b = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
            return max(0.0, (b - a).total_seconds())
        except Exception:
            pass
    # 阶段耗时累加
    total_ms = 0.0
    for sr in result.get("stage_results") or []:
        if isinstance(sr, dict) and sr.get("elapsed_ms") is not None:
            try:
                total_ms += float(sr.get("elapsed_ms") or 0)
            except (TypeError, ValueError):
                pass
    if total_ms > 0:
        return total_ms / 1000.0
    return float(fallback or 0.0)


def build_history_error(result: Optional[Dict[str, Any]]) -> str:
    if not isinstance(result, dict):
        return "执行结果缺失"
    if result.get("success") is True and result.get("gate_passed") is not False:
        return ""
    parts: List[str] = []
    code = result.get("error_code")
    err = result.get("error") or result.get("user_hint") or ""
    if code:
        parts.append(str(code))
    if err:
        parts.append(str(err))
    if not parts:
        # 从失败阶段拼摘要
        for sr in result.get("stage_results") or []:
            if isinstance(sr, dict) and sr.get("ok_assert") is False and not sr.get("cleanup"):
                sid = sr.get("stage_id") or "?"
                parts.append(f"{sid}: {sr.get('error') or sr.get('error_code') or 'failed'}")
                break
        if result.get("assertion_failed"):
            parts.append(f"跨端断言失败 {result.get('assertion_failed')} 条")
    return " | ".join(parts) if parts else "跨端执行未通过"


def stage_results_to_step_rows(stage_results: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(stage_results, list):
        return rows
    for i, sr in enumerate(stage_results):
        if not isinstance(sr, dict):
            continue
        ok = sr.get("ok_assert")
        if sr.get("skipped_failure") or sr.get("recovery_action") == "skip":
            st = "skipped"
        elif ok is True:
            st = "success"
        else:
            st = "failed"
        elapsed = sr.get("elapsed_ms")
        try:
            dur = float(elapsed) / 1000.0 if elapsed is not None else 0.0
        except (TypeError, ValueError):
            dur = 0.0
        rows.append({
            "step_order": i + 1,
            "action": str(sr.get("layer") or sr.get("action") or "stage"),
            "selector_value": str(sr.get("stage_id") or ""),
            "input_value": str(sr.get("sync_point") or ""),
            "description": str(sr.get("label") or sr.get("stage_id") or f"stage-{i+1}"),
            "status": st,
            "error": "" if st == "success" else str(sr.get("error") or sr.get("error_code") or ""),
            "screenshot": str(sr.get("screenshot") or sr.get("screenshot_path") or ""),
            "duration": dur,
        })
    return rows


def build_audit_record(
    result: Dict[str, Any],
    *,
    plan: Optional[Dict[str, Any]] = None,
    test_type: str = "cross_end",
    user_id: str = "",
    project_id: Any = None,
    trigger_source: str = "ui",
    agent_run_id: str = "",
) -> Dict[str, Any]:
    plan = plan if isinstance(plan, dict) else {}
    status = normalize_cross_end_history_status(result)
    variables = result.get("variables") or result.get("vars") or {}
    safe_vars = redact_vars_for_history(variables)
    plan_id = result.get("plan_id") or plan.get("plan_id") or ""
    scenario = plan.get("scenario") or result.get("scenario") or plan_id or "跨端计划"
    pid: Optional[int] = None
    if project_id is not None and str(project_id).strip() != "":
        try:
            pid = int(project_id)
        except (TypeError, ValueError):
            pid = None

    meta = {
        "schema": "cross_end_run_audit/v1",
        "plan_id": plan_id,
        "scenario": scenario,
        "test_type": test_type,
        "user_id": str(user_id or ""),
        "project_id": pid,
        "trigger_source": trigger_source,
        "agent_run_id": agent_run_id or "",
        "error_code": result.get("error_code"),
        "gate_passed": result.get("gate_passed"),
        "success": result.get("success"),
        "lock": result.get("lock"),
        "assertion_passed": result.get("assertion_passed"),
        "assertion_failed": result.get("assertion_failed"),
        "skipped_failure_stages": result.get("skipped_failure_stages") or [],
        "variables": safe_vars,
        "stage_count": len(result.get("stage_results") or []),
    }
    hitl_summary = []
    for sr in result.get("stage_results") or []:
        if not isinstance(sr, dict):
            continue
        if sr.get("hitl_gate_id") or sr.get("hitl_events") or sr.get("layer") == "hitl":
            hitl_summary.append({
                "stage_id": sr.get("stage_id"),
                "gate_id": sr.get("hitl_gate_id"),
                "outcome": sr.get("hitl_outcome"),
                "events": len(sr.get("hitl_events") or []),
                "ok_assert": sr.get("ok_assert"),
            })
    if hitl_summary:
        meta["hitl"] = hitl_summary
    risk_summary = []
    for sr in result.get("stage_results") or []:
        if not isinstance(sr, dict):
            continue
        if sr.get("risk_level") or sr.get("risk_events") or sr.get("risk_approval_id"):
            risk_summary.append({
                "stage_id": sr.get("stage_id"),
                "level": sr.get("risk_level"),
                "decision": sr.get("risk_decision"),
                "approval_id": sr.get("risk_approval_id"),
                "events": len(sr.get("risk_events") or []),
                "ok_assert": sr.get("ok_assert"),
            })
    if risk_summary:
        meta["risk"] = risk_summary
    audit_id = f"cea-{uuid.uuid4().hex[:12]}"
    out = {
        "audit_id": audit_id,
        "created_at": _utc_now(),
        "status": status,
        "duration": compute_duration_sec(result),
        "error": build_history_error(result) if status != "success" else "",
        "flow_name": str(scenario)[:200],
        "test_type": test_type if is_protected_history_test_type(test_type) else "cross_end",
        "project_id": pid,
        "case_id": None,
        "extracted_text": json.dumps(meta, ensure_ascii=False),
        "expected_text": json.dumps({
            "plan_id": plan_id,
            "project_id": pid,
            "agent_run_id": agent_run_id or "",
            "audit_id": audit_id,
        }, ensure_ascii=False),
        "screenshots": _collect_screenshots(result),
        "stage_results": list(result.get("stage_results") or []),
        "meta": meta,
    }
    return out


def _collect_screenshots(result: Dict[str, Any]) -> str:
    paths: List[str] = []
    for sr in result.get("stage_results") or []:
        if not isinstance(sr, dict):
            continue
        p = sr.get("screenshot") or sr.get("screenshot_path")
        if p:
            paths.append(str(p))
    return json.dumps(paths, ensure_ascii=False) if paths else ""


def write_audit_file(record: Dict[str, Any]) -> Path:
    path = _audit_dir() / f"{record['audit_id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def persist_to_database(
    record: Dict[str, Any],
    db: Any = None,
) -> Optional[int]:
    """写入 run_history + step_results。失败返回 None，不抛到业务主路径（除非调用方要求）。"""
    if db is None:
        try:
            from database import Database

            db = Database()
        except Exception:
            return None
    try:
        hid = db.create_run_history(
            record.get("case_id"),
            record["status"],
            float(record.get("duration") or 0.0),
            record.get("error") or "",
            record.get("extracted_text") or "",
            record.get("expected_text") or "",
            test_type=record.get("test_type") or "cross_end",
            flow_name=record.get("flow_name") or "",
            project_id=record.get("project_id"),
            screenshots=record.get("screenshots") or "",
        )
    except Exception:
        return None
    if not hid:
        return None
    for row in stage_results_to_step_rows(record.get("stage_results")):
        try:
            db.create_step_result(
                int(hid),
                0,
                int(row["step_order"]),
                row["action"],
                row["selector_value"],
                row["input_value"],
                row["description"],
                row["status"],
                row.get("error") or "",
                row.get("screenshot") or "",
                float(row.get("duration") or 0.0),
            )
        except Exception:
            # 步骤写入失败不回滚整次历史（历史行已诚实存在）
            continue
    return int(hid)


def record_cross_end_execution(
    result: Dict[str, Any],
    *,
    plan: Optional[Dict[str, Any]] = None,
    test_type: str = "cross_end",
    user_id: str = "",
    project_id: Any = None,
    trigger_source: str = "ui",
    agent_run_id: str = "",
    db: Any = None,
    persist_db: bool = True,
    persist_file: bool = True,
) -> Dict[str, Any]:
    """记录一次跨端/多 Agent 执行；把 run_history_id / audit 路径写回 result。"""
    if not isinstance(result, dict):
        return {"ok": False, "error": "result 非 dict"}

    record = build_audit_record(
        result,
        plan=plan,
        test_type=test_type,
        user_id=user_id,
        project_id=project_id,
        trigger_source=trigger_source,
        agent_run_id=agent_run_id,
    )
    # 终态一致性：禁止 result.success=False 却记 success
    if result.get("success") is not True:
        record["status"] = "failed"
        if not record.get("error"):
            record["error"] = build_history_error(result)

    audit_path = ""
    if persist_file:
        try:
            audit_path = str(write_audit_file(record))
        except Exception as exc:
            audit_path = ""
            record["file_error"] = str(exc)

    history_id: Optional[int] = None
    if persist_db:
        history_id = persist_to_database(record, db=db)

    result["run_history_id"] = history_id
    result["audit_id"] = record["audit_id"]
    result["audit_path"] = audit_path or None
    result["history_status"] = record["status"]
    # 若业务 success 与历史不一致，以历史为准暴露（不应发生）
    if record["status"] == "success" and result.get("success") is not True:
        result["success"] = False
        result["history_status"] = "failed"
    return {
        "ok": True,
        "run_history_id": history_id,
        "audit_id": record["audit_id"],
        "audit_path": audit_path or None,
        "status": record["status"],
    }
