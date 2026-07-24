# -*- coding: utf-8 -*-
"""Trace / 证据包导出（Phase B-2 / Z2 / R07）。

自研 JSON Trace（非 OTel）：一次运行可导出目录或 ZIP，含：
- manifest.json（结论、关联 id、诚实声明）
- trace.json（阶段时间线 / Agent 事件）
- report.json（Verifier 或跨端摘要）
- stage_results.json
- screenshots/index.json（存在/缺失如实标注）
- SUMMARY.md

原则：
- 不得把 failed 历史美化为 success
- 敏感字段脱敏
- 截图缺失记 missing，不算证据齐全
- 源数据不足时明确 incomplete，而不是空包假完整
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ai_modules.execute.cross_end_run_audit import (
    _audit_dir,
    is_protected_history_test_type,
    redact_vars_for_history,
)

_SAFE_ID = re.compile(r"[^a-zA-Z0-9._-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _packs_root() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2] / "data"
    d = root / "trace_packs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(s: str, fallback: str = "pack") -> str:
    t = _SAFE_ID.sub("-", str(s or "").strip())[:80]
    return t or fallback


def load_audit_record(audit_id: str) -> Optional[Dict[str, Any]]:
    aid = _safe_name(audit_id, "")
    if not aid:
        return None
    path = _audit_dir() / f"{aid}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_agent_team_state(run_id: str) -> Optional[Dict[str, Any]]:
    try:
        from ai_modules.agent_teams.test_run_state import load_run

        st = load_run(run_id)
        return st.to_dict() if st else None
    except Exception:
        return None


def _history_bundle(run_history_id: Any, db: Any = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"run_history_id": None, "detail": None, "steps": []}
    if run_history_id is None or str(run_history_id).strip() == "":
        return out
    try:
        hid = int(run_history_id)
    except (TypeError, ValueError):
        return out
    out["run_history_id"] = hid
    if db is None:
        try:
            from database import Database

            db = Database()
        except Exception:
            return out
    try:
        detail = db.get_run_history_detail(hid)
        steps = db.get_step_results(hid) if detail else []
    except Exception:
        return out
    out["detail"] = detail
    out["steps"] = steps or []
    return out


def _normalize_pack_status(
    *,
    audit: Optional[Dict[str, Any]] = None,
    history: Optional[Dict[str, Any]] = None,
    agent_state: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """返回 (status, reason)。failed 优先于 success。"""
    reasons: List[str] = []
    statuses: List[str] = []

    if isinstance(history, dict) and history.get("detail"):
        st = str(history["detail"].get("status") or "").lower()
        statuses.append(st)
        if st not in ("success", "passed"):
            reasons.append(history["detail"].get("error") or f"run_history={st}")

    if isinstance(audit, dict):
        st = str(audit.get("status") or "").lower()
        statuses.append(st)
        if st not in ("success", "passed"):
            reasons.append(audit.get("error") or f"audit={st}")

    if isinstance(agent_state, dict):
        st = str(agent_state.get("status") or "").lower()
        statuses.append(st)
        report = agent_state.get("report") or {}
        if st != "success" or report.get("passed") is False:
            reasons.append((report or {}).get("reason") or f"agent={st}")

    # 任一失败 → failed
    for st in statuses:
        if st and st not in ("success", "passed"):
            return "failed", (reasons[0] if reasons else "执行未通过")
    if not statuses:
        return "incomplete", "缺少可导出的运行源（audit / history / agent run）"
    return "success", "全部源记录为成功"


def _screenshot_index(stage_results: Any, screenshots_field: Any = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen = set()

    def _add(path: str, stage_id: str = "") -> None:
        p = str(path or "").strip()
        if not p or p in seen:
            return
        seen.add(p)
        exists = False
        try:
            exists = Path(p).is_file()
        except Exception:
            exists = False
        items.append({
            "path": p,
            "stage_id": stage_id,
            "exists": exists,
            "status": "present" if exists else "missing",
        })

    if isinstance(stage_results, list):
        for sr in stage_results:
            if not isinstance(sr, dict):
                continue
            p = sr.get("screenshot") or sr.get("screenshot_path")
            if p:
                _add(str(p), str(sr.get("stage_id") or ""))
    if screenshots_field:
        try:
            raw = json.loads(screenshots_field) if isinstance(screenshots_field, str) else screenshots_field
        except (TypeError, json.JSONDecodeError):
            raw = [screenshots_field] if screenshots_field else []
        if isinstance(raw, list):
            for p in raw:
                _add(str(p))
    return items


def _build_trace_events(
    *,
    audit: Optional[Dict[str, Any]],
    agent_state: Optional[Dict[str, Any]],
    history: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    if isinstance(agent_state, dict):
        for e in agent_state.get("events") or []:
            if isinstance(e, dict):
                events.append({
                    "source": "agent_teams",
                    "at": e.get("at"),
                    "agent": e.get("agent"),
                    "kind": e.get("kind"),
                    "message": e.get("message"),
                })
    # 阶段时间线（无 Agent 事件时仍可审计）
    stages = []
    if isinstance(audit, dict):
        stages = audit.get("stage_results") or []
    elif isinstance(agent_state, dict):
        stages = agent_state.get("stage_results") or []
    for i, sr in enumerate(stages or []):
        if not isinstance(sr, dict):
            continue
        # HITL 事件优先展开
        hitl_evs = sr.get("hitl_events")
        if isinstance(hitl_evs, list) and hitl_evs:
            for he in hitl_evs:
                if not isinstance(he, dict):
                    continue
                events.append({
                    "source": "hitl",
                    "at": he.get("at"),
                    "agent": "HitlGate",
                    "kind": he.get("kind"),
                    "message": (
                        f"{he.get('kind')}: {he.get('reason') or sr.get('hitl_prompt') or ''}"
                        f" (gate={he.get('gate_id') or sr.get('hitl_gate_id') or ''})"
                    ).strip(),
                    "stage_id": sr.get("stage_id"),
                    "gate_id": he.get("gate_id") or sr.get("hitl_gate_id"),
                    "hitl_outcome": sr.get("hitl_outcome"),
                })
        risk_evs = sr.get("risk_events")
        if isinstance(risk_evs, list) and risk_evs:
            for re in risk_evs:
                if not isinstance(re, dict):
                    continue
                events.append({
                    "source": "risk",
                    "at": re.get("at"),
                    "agent": "RiskGuard",
                    "kind": re.get("kind"),
                    "message": (
                        f"{re.get('kind')}: level={re.get('level') or sr.get('risk_level') or ''}"
                        f" approval={re.get('approval_id') or sr.get('risk_approval_id') or ''}"
                    ).strip(),
                    "stage_id": sr.get("stage_id") or re.get("stage_id"),
                    "risk_level": re.get("level") or sr.get("risk_level"),
                    "approval_id": re.get("approval_id") or sr.get("risk_approval_id"),
                })
        ok = sr.get("ok_assert")
        kind = "complete" if ok is True else ("skip" if sr.get("skipped_failure") else "fail")
        msg = f"{sr.get('stage_id') or i}: {'ok' if ok is True else (sr.get('error') or 'failed')}"
        if sr.get("hitl_outcome"):
            msg += f" [hitl={sr.get('hitl_outcome')}]"
        if sr.get("risk_level"):
            msg += f" [risk={sr.get('risk_level')}/{sr.get('risk_decision') or ''}]"
        events.append({
            "source": "stage",
            "at": None,
            "agent": "WebApiExecutor" if not agent_state else "stage",
            "kind": kind,
            "message": msg,
            "stage_id": sr.get("stage_id"),
            "elapsed_ms": sr.get("elapsed_ms"),
            "hitl_outcome": sr.get("hitl_outcome"),
            "risk_level": sr.get("risk_level"),
            "risk_decision": sr.get("risk_decision"),
        })
    if isinstance(history, dict) and history.get("steps"):
        for step in history["steps"]:
            if not isinstance(step, dict):
                continue
            events.append({
                "source": "step_results",
                "at": step.get("created_at"),
                "agent": "history",
                "kind": "complete" if step.get("status") in ("success", "passed") else "fail",
                "message": f"step#{step.get('step_order')}: {step.get('description') or step.get('action')}",
                "status": step.get("status"),
                "error": step.get("error"),
            })
    return events


def build_trace_document(
    *,
    audit_id: str = "",
    run_history_id: Any = None,
    agent_run_id: str = "",
    db: Any = None,
) -> Dict[str, Any]:
    """组装可序列化的 Trace 文档（不含 ZIP）。"""
    audit = load_audit_record(audit_id) if audit_id else None
    agent_state = load_agent_team_state(agent_run_id) if agent_run_id else None
    history = _history_bundle(run_history_id, db=db)

    # 若只给了 history，尝试从 expected_text 找回 agent/audit 关联
    if history.get("detail") and not agent_state and not audit:
        detail = history["detail"]
        try:
            meta = json.loads(detail.get("expected_text") or "{}")
        except (TypeError, json.JSONDecodeError):
            meta = {}
        if isinstance(meta, dict):
            if not agent_run_id and meta.get("agent_run_id"):
                agent_state = load_agent_team_state(str(meta["agent_run_id"]))
            if not audit_id and meta.get("plan_id"):
                # 无法直接反查 audit_id；保留 plan_id 于 manifest
                pass
        # extracted_text 可能含 audit schema
        try:
            extracted = json.loads(detail.get("extracted_text") or "{}")
            if isinstance(extracted, dict) and extracted.get("schema"):
                # 合成瘦 audit
                audit = {
                    "audit_id": f"from-history-{history['run_history_id']}",
                    "status": detail.get("status"),
                    "error": detail.get("error"),
                    "flow_name": detail.get("flow_name") or detail.get("case_name"),
                    "test_type": detail.get("test_type"),
                    "meta": extracted,
                    "stage_results": [],
                    "screenshots": detail.get("screenshots"),
                }
        except (TypeError, json.JSONDecodeError):
            pass

    status, reason = _normalize_pack_status(
        audit=audit, history=history, agent_state=agent_state
    )

    stage_results: List[Any] = []
    if isinstance(audit, dict) and audit.get("stage_results"):
        stage_results = list(audit.get("stage_results") or [])
    elif isinstance(agent_state, dict) and agent_state.get("stage_results"):
        stage_results = list(agent_state.get("stage_results") or [])

    screenshots = _screenshot_index(
        stage_results,
        (audit or {}).get("screenshots") if audit else (history.get("detail") or {}).get("screenshots"),
    )
    missing_shots = sum(1 for s in screenshots if s.get("status") == "missing")

    report: Dict[str, Any]
    if isinstance(agent_state, dict) and agent_state.get("report"):
        report = dict(agent_state["report"])
    else:
        report = {
            "passed": status == "success",
            "reason": reason,
            "evidence_level": (
                "missing" if status != "success"
                else ("weak" if missing_shots or not screenshots else "strong")
            ),
            "source": "cross_end_audit" if audit else ("history" if history.get("detail") else "unknown"),
        }

    # 证据完整性：失败且无阶段/无事件 → incomplete 标记
    events = _build_trace_events(audit=audit, agent_state=agent_state, history=history)
    completeness = "complete"
    if status == "incomplete":
        completeness = "incomplete"
    elif not events and not stage_results and not history.get("steps"):
        completeness = "incomplete"
        if status == "success":
            # 无证据却 success → 降级诚实性
            status = "failed"
            reason = "成功结论缺少阶段/事件证据，证据包拒绝记绿"
            report["passed"] = False
            report["reason"] = reason
            report["evidence_level"] = "missing"

    vars_blob = {}
    if isinstance(agent_state, dict):
        vars_blob = redact_vars_for_history(agent_state.get("vars") or {})
    elif isinstance(audit, dict):
        meta = audit.get("meta") or {}
        if isinstance(meta, dict):
            vars_blob = dict(meta.get("variables") or {})

    test_type = (
        (audit or {}).get("test_type")
        or (agent_state and "agent_teams")
        or ((history.get("detail") or {}).get("test_type"))
        or "cross_end"
    )
    if not is_protected_history_test_type(test_type) and agent_state:
        test_type = "agent_teams"

    pack_id = _safe_name(
        audit_id
        or agent_run_id
        or (f"rh-{run_history_id}" if run_history_id is not None else "")
        or f"trace-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )

    manifest = {
        "schema": "testory.trace_pack/v1",
        "pack_id": pack_id,
        "generated_at": _utc_now(),
        "status": status,
        "reason": reason,
        "completeness": completeness,
        "test_type": test_type,
        "refs": {
            "audit_id": audit_id or (audit or {}).get("audit_id"),
            "run_history_id": history.get("run_history_id"),
            "agent_run_id": agent_run_id or (agent_state or {}).get("run_id"),
            "plan_id": ((audit or {}).get("meta") or {}).get("plan_id")
            or ((agent_state or {}).get("plan") or {}).get("plan_id"),
        },
        "counts": {
            "events": len(events),
            "stages": len(stage_results),
            "history_steps": len(history.get("steps") or []),
            "screenshots_present": sum(1 for s in screenshots if s.get("exists")),
            "screenshots_missing": missing_shots,
        },
        "honesty": {
            "no_false_green": True,
            "note": "status 综合 audit/history/agent；缺证据不得记 success",
        },
    }

    return {
        "manifest": manifest,
        "trace": {
            "schema": "testory.json_trace/v1",
            "pack_id": pack_id,
            "events": events,
        },
        "report": report,
        "stage_results": stage_results,
        "variables": vars_blob,
        "screenshots": screenshots,
        "history": {
            "run_history_id": history.get("run_history_id"),
            "detail": history.get("detail"),
            "steps": history.get("steps") or [],
        },
        "agent_state": {
            "run_id": (agent_state or {}).get("run_id"),
            "status": (agent_state or {}).get("status"),
            "agents_seen": (agent_state or {}).get("agents_seen"),
            "events": (agent_state or {}).get("events"),
        } if agent_state else None,
        "audit": {
            "audit_id": (audit or {}).get("audit_id"),
            "status": (audit or {}).get("status"),
            "error": (audit or {}).get("error"),
            "flow_name": (audit or {}).get("flow_name"),
            "meta": (audit or {}).get("meta"),
        } if audit else None,
    }


def _write_summary_md(doc: Dict[str, Any]) -> str:
    m = doc["manifest"]
    lines = [
        f"# Trace Pack `{m.get('pack_id')}`",
        "",
        f"- status: **{m.get('status')}**",
        f"- completeness: {m.get('completeness')}",
        f"- reason: {m.get('reason')}",
        f"- test_type: {m.get('test_type')}",
        f"- refs: `{json.dumps(m.get('refs'), ensure_ascii=False)}`",
        "",
        "## Counts",
        "",
        json.dumps(m.get("counts"), ensure_ascii=False, indent=2),
        "",
        "## Report",
        "",
        "```json",
        json.dumps(doc.get("report"), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Events (head)",
        "",
    ]
    for e in (doc.get("trace") or {}).get("events") or []:
        lines.append(
            f"- [{e.get('source')}] {e.get('agent')}/{e.get('kind')}: {e.get('message')}"
        )
    lines.append("")
    return "\n".join(lines)


def write_trace_pack_dir(doc: Dict[str, Any], out_dir: Optional[Path] = None) -> Path:
    pack_id = _safe_name((doc.get("manifest") or {}).get("pack_id") or "pack")
    root = out_dir or (_packs_root() / pack_id)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "screenshots").mkdir(exist_ok=True)

    def _dump(name: str, obj: Any) -> None:
        (root / name).write_text(
            json.dumps(obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    _dump("manifest.json", doc["manifest"])
    _dump("trace.json", doc["trace"])
    _dump("report.json", doc["report"])
    _dump("stage_results.json", doc.get("stage_results") or [])
    _dump("variables.json", doc.get("variables") or {})
    _dump("screenshots/index.json", doc.get("screenshots") or [])
    _dump("history.json", doc.get("history") or {})
    # 汇总 HITL 事件
    hitl_flat: List[Dict[str, Any]] = []
    for sr in doc.get("stage_results") or []:
        if isinstance(sr, dict):
            for he in sr.get("hitl_events") or []:
                if isinstance(he, dict):
                    row = dict(he)
                    row["stage_id"] = sr.get("stage_id")
                    row["hitl_outcome"] = sr.get("hitl_outcome")
                    hitl_flat.append(row)
    if hitl_flat:
        _dump("hitl_events.json", hitl_flat)
    risk_flat: List[Dict[str, Any]] = []
    for sr in doc.get("stage_results") or []:
        if not isinstance(sr, dict):
            continue
        for re in sr.get("risk_events") or []:
            if isinstance(re, dict):
                row = dict(re)
                row["stage_id"] = sr.get("stage_id")
                row["risk_level"] = sr.get("risk_level")
                row["risk_decision"] = sr.get("risk_decision")
                risk_flat.append(row)
    if risk_flat:
        _dump("risk_events.json", risk_flat)
    if doc.get("agent_state"):
        _dump("agent_state.json", doc["agent_state"])
    if doc.get("audit"):
        _dump("audit.json", doc["audit"])
    (root / "SUMMARY.md").write_text(_write_summary_md(doc), encoding="utf-8")

    # 复制存在的截图（失败则记入 index，已在 screenshots 状态）
    for item in doc.get("screenshots") or []:
        if not item.get("exists"):
            continue
        src = Path(item["path"])
        try:
            dest = root / "screenshots" / src.name
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
            item["packed_as"] = f"screenshots/{src.name}"
        except Exception as exc:
            item["exists"] = False
            item["status"] = "missing"
            item["copy_error"] = str(exc)
    _dump("screenshots/index.json", doc.get("screenshots") or [])
    return root


def zip_trace_pack(pack_dir: Path, zip_path: Optional[Path] = None) -> Path:
    pack_dir = Path(pack_dir)
    if zip_path is None:
        zip_path = pack_dir.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(pack_dir.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(pack_dir)).replace("\\", "/"))
    return zip_path


def export_trace_pack(
    *,
    audit_id: str = "",
    run_history_id: Any = None,
    agent_run_id: str = "",
    db: Any = None,
    out_dir: Optional[Path] = None,
    make_zip: bool = True,
) -> Dict[str, Any]:
    """导出证据包目录（及可选 ZIP）。"""
    if not audit_id and run_history_id is None and not agent_run_id:
        return {
            "ok": False,
            "error": "请提供 audit_id / run_history_id / agent_run_id 之一",
            "error_code": "TRACE_SOURCE_REQUIRED",
        }

    doc = build_trace_document(
        audit_id=audit_id,
        run_history_id=run_history_id,
        agent_run_id=agent_run_id,
        db=db,
    )
    status = (doc.get("manifest") or {}).get("status")
    if status == "incomplete" and (doc.get("manifest") or {}).get("completeness") == "incomplete":
        # 仍写出包，便于排障；ok=False 提示调用方
        pack_dir = write_trace_pack_dir(doc, out_dir=out_dir)
        zip_p = zip_trace_pack(pack_dir) if make_zip else None
        return {
            "ok": False,
            "error": (doc.get("manifest") or {}).get("reason") or "证据不完整",
            "error_code": "TRACE_INCOMPLETE",
            "status": status,
            "pack_id": (doc.get("manifest") or {}).get("pack_id"),
            "pack_dir": str(pack_dir),
            "zip_path": str(zip_p) if zip_p else None,
            "manifest": doc["manifest"],
        }

    pack_dir = write_trace_pack_dir(doc, out_dir=out_dir)
    zip_p = zip_trace_pack(pack_dir) if make_zip else None
    return {
        "ok": True,
        "status": status,
        "pack_id": (doc.get("manifest") or {}).get("pack_id"),
        "pack_dir": str(pack_dir),
        "zip_path": str(zip_p) if zip_p else None,
        "manifest": doc["manifest"],
        "download_name": f"{(doc.get('manifest') or {}).get('pack_id')}.zip",
    }
