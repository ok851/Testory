# -*- coding: utf-8 -*-
"""客户向审计交付包：时间窗内运行索引 + 治理摘要 + 关键失败 Trace 子包。

与单次 ``trace_pack`` 互补：本包面向「交给客户/审计」的批量证据，不美化通过率。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai_modules.execute.history_ops_summary import (
    aggregate_ops_governance,
    enrich_run_history_record,
)

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_name(s: str, fallback: str = "audit") -> str:
    t = _SAFE.sub("-", str(s or "").strip())[:72]
    return t or fallback


def _packs_root() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2] / "data"
    d = root / "customer_audit_packs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fetch_history_rows(
    *,
    project_id: Any = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    case_category: Optional[str] = None,
    scan_limit: int = 500,
    db: Any = None,
) -> List[Dict[str, Any]]:
    from modules.integration.test_report import TestReportGenerator

    gen = TestReportGenerator(db=db) if db is not None else TestReportGenerator()
    # 复用治理查询：含无 case 跨端
    import sqlite3

    conn = sqlite3.connect(gen.db.db_path)
    cursor = conn.cursor()
    where_clause, params = gen._build_report_filters(
        int(project_id) if project_id not in (None, "") else None,
        start_date,
        end_date,
        case_category,
        include_orphan_runs=True,
    )
    limit = max(1, min(int(scan_limit or 500), 2000))
    cursor.execute(
        f"""
        SELECT
            rh.id,
            rh.status,
            rh.error,
            rh.extracted_text,
            rh.expected_text,
            COALESCE(rh.test_type, 'web') AS test_type,
            COALESCE(NULLIF(rh.flow_name, ''), tc.name, '') AS case_name,
            rh.created_at,
            rh.duration,
            rh.flow_name,
            rh.project_id
        FROM run_history rh
        LEFT JOIN test_cases tc ON rh.case_id = tc.id
        WHERE {where_clause}
        ORDER BY rh.id DESC
        LIMIT ?
        """,
        list(params) + [limit],
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "status": r[1],
            "error": r[2] or "",
            "extracted_text": r[3] or "",
            "expected_text": r[4] or "",
            "test_type": r[5] or "web",
            "case_name": r[6] or "",
            "created_at": r[7],
            "duration": r[8],
            "flow_name": r[9] or "",
            "project_id": r[10],
        }
        for r in rows
    ]


def _index_entry(rec: Dict[str, Any]) -> Dict[str, Any]:
    en = enrich_run_history_record(dict(rec))
    ops = en.get("ops_summary") or {}
    links = en.get("links") or {}
    st = str(rec.get("status") or "").lower()
    passed = st in ("success", "passed")
    blocked = ops.get("gate_passed") is False or str(ops.get("error_code") or "") in (
        "RISK_APPROVAL_REQUIRED",
        "HITL_TIMEOUT",
        "HITL_CANCELLED",
        "HITL_WAIT",
        "DESKTOP_NO_SESSION",
        "DESKTOP_SOFT_FAIL",
        "DESKTOP_STEP_FAILED",
    )
    return {
        "run_history_id": rec.get("id"),
        "status": rec.get("status"),
        "passed": passed,
        "gate_blocked": bool(blocked),
        "case_name": rec.get("case_name") or rec.get("flow_name") or "",
        "test_type": ops.get("test_type") or rec.get("test_type"),
        "created_at": rec.get("created_at"),
        "duration": rec.get("duration"),
        "error_code": ops.get("error_code") or None,
        "error": (rec.get("error") or "")[:500],
        "hitl_count": ops.get("hitl_count") or 0,
        "risk_count": ops.get("risk_count") or 0,
        "build_id": links.get("build_id"),
        "ci_run_id": links.get("ci_run_id"),
        "audit_id": links.get("audit_id"),
        "trace_export_url": links.get("trace_export_url"),
    }


def _write_customer_readme(
    *,
    pack_id: str,
    filters: Dict[str, Any],
    governance: Dict[str, Any],
    index_count: int,
    embedded: int,
    honesty_note: str,
) -> str:
    lines = [
        f"# Testory 客户审计交付包 `{pack_id}`",
        "",
        "## 用途",
        "",
        "本 ZIP 供客户 / 内审查阅选定时间窗内的执行诚实性证据：",
        "- `index.json`：每条运行的结论、门禁、CI 构建号（失败不剔除）",
        "- `governance.json`：HITL / Risk / 证据 / CI 汇总",
        "- `auth_events.json`：同时间窗登录 / SSO / 注销审计（失败登录亦保留）",
        "- `runs/`：优先附带**门禁阻断或失败**运行的 Trace 子包（截图缺失如实标注）",
        "- `CUSTOMER_README.md`：本说明",
        "",
        "## 筛选条件",
        "",
        "```json",
        json.dumps(filters, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 汇总（不美化）",
        "",
        f"- 索引运行数: **{index_count}**",
        f"- 跨端: {governance.get('cross_end_runs', 0)} · AgentTeams: {governance.get('agent_teams_runs', 0)}",
        f"- 含 HITL: {governance.get('with_hitl', 0)} · 含 Risk: {governance.get('with_risk', 0)}",
        f"- 门禁阻断: **{governance.get('gate_blocked', 0)}**",
        f"- 可导出证据: {governance.get('with_evidence', 0)} · 关联 CI: {governance.get('with_ci', 0)}",
        f"- 内嵌 Trace 子包: {embedded}",
        "",
        "## 诚实声明",
        "",
        honesty_note,
        "",
        "status=failed / gate_blocked 不会被改写为 success。",
        "单次证据细节见 `runs/<run_history_id>/`；也可在平台 `/run-history` 按 id 复查。",
        "",
    ]
    return "\n".join(lines)


def build_customer_audit_pack(
    *,
    project_id: Any = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    case_category: Optional[str] = None,
    scan_limit: int = 500,
    embed_limit: int = 15,
    db: Any = None,
    out_dir: Optional[Path] = None,
    make_zip: bool = True,
) -> Dict[str, Any]:
    """构建客户审计包目录（及可选 ZIP）。"""
    rows = _fetch_history_rows(
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        case_category=case_category,
        scan_limit=scan_limit,
        db=db,
    )
    index = [_index_entry(r) for r in rows]
    governance = aggregate_ops_governance(rows, recent_limit=20)
    filters = {
        "project_id": project_id,
        "start_date": start_date,
        "end_date": end_date,
        "case_category": case_category or "all",
        "scan_limit": scan_limit,
        "embed_limit": embed_limit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    passed = sum(1 for x in index if x.get("passed"))
    failed = sum(1 for x in index if not x.get("passed"))
    pack_id = _safe_name(
        f"customer-audit-{project_id or 'all'}-{_utc_stamp()}",
        "customer-audit",
    )
    root = Path(out_dir) if out_dir else (_packs_root() / pack_id)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    runs_dir = root / "runs"
    runs_dir.mkdir(exist_ok=True)

    # 优先嵌入：门禁阻断 → 失败 → 其余
    def _prio(item: Dict[str, Any]) -> tuple:
        return (
            0 if item.get("gate_blocked") else 1,
            0 if not item.get("passed") else 1,
            -(item.get("run_history_id") or 0),
        )

    embed_candidates = sorted(index, key=_prio)
    embedded: List[Dict[str, Any]] = []
    embed_cap = max(0, min(int(embed_limit or 15), 50))

    if embed_cap and db is None:
        try:
            from database import Database

            db = Database()
        except Exception:
            db = None

    from ai_modules.execute.trace_pack import export_trace_pack

    for item in embed_candidates:
        if len(embedded) >= embed_cap:
            break
        if not (item.get("gate_blocked") or not item.get("passed") or item.get("audit_id")):
            # 成功且无 audit 也可跳过；仍允许有 audit_id 的成功附带少量
            if item.get("passed") and not item.get("audit_id"):
                continue
        hid = item.get("run_history_id")
        aid = item.get("audit_id") or ""
        sub = runs_dir / str(hid or _safe_name(aid or "run"))
        try:
            exported = export_trace_pack(
                audit_id=str(aid or ""),
                run_history_id=hid,
                db=db,
                out_dir=sub,
                make_zip=False,
            )
            embedded.append(
                {
                    "run_history_id": hid,
                    "audit_id": aid or None,
                    "pack_ok": bool(exported.get("ok")),
                    "status": exported.get("status"),
                    "error_code": exported.get("error_code"),
                    "relative_dir": f"runs/{sub.name}",
                }
            )
        except Exception as exc:
            embedded.append(
                {
                    "run_history_id": hid,
                    "audit_id": aid or None,
                    "pack_ok": False,
                    "error": str(exc)[:200],
                }
            )

    honesty = (
        governance.get("honesty_note")
        or "门禁阻断与失败均保留在索引中；不得将 incomplete/failed 改写为 success。"
    )
    manifest = {
        "schema": "testory.customer_audit_pack/v1",
        "pack_id": pack_id,
        "filters": filters,
        "counts": {
            "indexed_runs": len(index),
            "passed": passed,
            "failed_or_other": failed,
            "gate_blocked": governance.get("gate_blocked", 0),
            "embedded_traces": len(embedded),
            "truncated": len(rows) >= scan_limit,
        },
        "honesty": {"no_false_green": True, "note": honesty},
        "governance_ref": "governance.json",
        "index_ref": "index.json",
    }

    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "index.json").write_text(
        json.dumps({"runs": index}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "governance.json").write_text(
        json.dumps(governance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "embedded.json").write_text(
        json.dumps(embedded, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "CUSTOMER_README.md").write_text(
        _write_customer_readme(
            pack_id=pack_id,
            filters=filters,
            governance=governance,
            index_count=len(index),
            embedded=len(embedded),
            honesty_note=honesty,
        ),
        encoding="utf-8",
    )

    # 登录 / SSO 审计串联（同时间窗）
    try:
        from modules.auth.auth_audit import list_auth_audit_events

        auth_events = list_auth_audit_events(
            start_date=start_date,
            end_date=end_date,
            limit=min(500, scan_limit),
            db=db,
        )
    except Exception:
        auth_events = []
    (root / "auth_events.json").write_text(
        json.dumps(
            {
                "target_type": "auth",
                "count": len(auth_events),
                "events": auth_events,
                "note": "含 LOGIN_SUCCESS/FAILURE、SSO/LDAP、LOGOUT、REGISTER；失败登录亦保留。",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest["counts"]["auth_events"] = len(auth_events)
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    zip_path = None
    if make_zip:
        zip_path = root.with_suffix(".zip")
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(root)).replace("\\", "/"))

    return {
        "ok": True,
        "pack_id": pack_id,
        "pack_dir": str(root),
        "zip_path": str(zip_path) if zip_path else None,
        "download_name": f"{pack_id}.zip",
        "manifest": manifest,
        "indexed_runs": len(index),
        "embedded_traces": len(embedded),
    }
