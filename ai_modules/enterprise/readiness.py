# -*- coding: utf-8 -*-
"""企业运营就绪清单（SLA 叙事的轻量替代）。

不输出「SLA 已达标」绿灯；仅列出可核查项（审计/CI/农场/多 Agent/证据）。
商务 SLA 由合同约定，平台侧给的是就绪证据与诚实 disclaimer。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List


def _check(cid: str, ok: bool, detail: str, severity: str = "warn") -> Dict[str, Any]:
    return {
        "id": cid,
        "ok": bool(ok),
        "detail": detail,
        "severity": "info" if ok else severity,
    }


def enterprise_ops_readiness() -> Dict[str, Any]:
    """聚合企业可运营检查项。"""
    checks: List[Dict[str, Any]] = []

    # License / 功能（不强行要求 enforce）
    try:
        from modules.auth.license_manager import license_manager

        feats = {
            "parallel_execution": license_manager.check_feature_available("parallel_execution"),
            "audit_log": license_manager.check_feature_available("audit_log"),
            "ci_integration": license_manager.check_feature_available("ci_integration"),
            "customer_audit_export": license_manager.check_feature_available("customer_audit_export"),
            "sso": license_manager.check_feature_available("sso"),
        }
        for k, v in feats.items():
            checks.append(
                _check(
                    f"feature_{k}",
                    v,
                    f"能力 {k}={'可用' if v else '不可用（试用未开或档位不足）'}",
                    severity="warn",
                )
            )
    except Exception as e:
        checks.append(_check("license_manager", False, f"无法读取 License: {e}", severity="fail"))

    # 农场
    try:
        from ai_modules.enterprise.execution_farm import dispatch_readiness

        farm = dispatch_readiness()
        checks.append(
            _check(
                "farm_dispatch",
                bool(farm.get("dispatch_ready")),
                farm.get("disclaimer") or "农场调度检查",
                severity="warn",
            )
        )
        for c in farm.get("checks") or []:
            if isinstance(c, dict):
                checks.append(
                    _check(
                        f"farm_{c.get('id')}",
                        bool(c.get("ok")),
                        str(c.get("detail") or ""),
                        severity="warn",
                    )
                )
    except Exception as e:
        checks.append(_check("farm", False, str(e), severity="warn"))

    # AgentTeams Spec
    try:
        from ai_modules.agent_teams import load_team_spec
        from ai_modules.agent_teams.sdk_bridge import assert_five_roles_in_spec, sdk_available

        spec = load_team_spec()
        missing = assert_five_roles_in_spec(spec)
        checks.append(
            _check(
                "agent_teams_five_roles",
                not missing,
                "五角色齐全" if not missing else f"缺失: {missing}",
                severity="fail",
            )
        )
        checks.append(
            _check(
                "agent_teams_sdk",
                True,
                f"官方 SDK 可导入={sdk_available()}（缺失时用本地控制面，属预期）",
                severity="info",
            )
        )
    except Exception as e:
        checks.append(_check("agent_teams", False, str(e), severity="fail"))

    # 执行模式
    mode = (os.environ.get("DESKTOP_EXECUTION_MODE") or "inprocess").strip()
    checks.append(
        _check(
            "desktop_mode",
            True,
            f"DESKTOP_EXECUTION_MODE={mode}（remote 才走农场节点调度）",
            severity="info",
        )
    )

    sla_evidence = None
    sla_alerts = None
    try:
        from ai_modules.enterprise.sla_evidence import summarize_sla_evidence

        sla_evidence = summarize_sla_evidence(limit=50)
        checks.append(
            _check(
                "sla_evidence",
                True,
                (
                    f"样本={sla_evidence.get('sample_count')} "
                    f"p50={sla_evidence.get('latency_ms_p50')} "
                    f"sla_claim={sla_evidence.get('sla_claim')}"
                ),
                severity="info",
            )
        )
    except Exception as e:
        checks.append(_check("sla_evidence", False, str(e), severity="warn"))

    try:
        from ai_modules.enterprise.sla_alerts import evaluate_sla_alerts

        sla_alerts = evaluate_sla_alerts(limit=50)
        checks.append(
            _check(
                "sla_alerts",
                not bool(sla_alerts.get("has_warning")),
                (
                    f"has_warning={sla_alerts.get('has_warning')} "
                    f"sla_met={sla_alerts.get('sla_met')}"
                ),
                severity="warn",
            )
        )
    except Exception as e:
        checks.append(_check("sla_alerts", False, str(e), severity="warn"))

    try:
        from ai_modules.enterprise.farm_jobs import jobs_summary

        js = jobs_summary()
        queued = int((js.get("counts") or {}).get("queued") or 0)
        checks.append(
            _check(
                "farm_queue",
                True,
                f"queued={queued} total={js.get('job_count')}",
                severity="info",
            )
        )
    except Exception as e:
        checks.append(_check("farm_queue", False, str(e), severity="warn"))

    fail_n = sum(1 for c in checks if not c.get("ok") and c.get("severity") == "fail")
    warn_n = sum(1 for c in checks if not c.get("ok") and c.get("severity") == "warn")
    ok_n = sum(1 for c in checks if c.get("ok"))

    return {
        "ok": True,
        "sla_claim": False,
        "phase_bc_closed": True,
        "counts": {"ok": ok_n, "warn": warn_n, "fail": fail_n, "total": len(checks)},
        "checks": checks,
        "sla_evidence": sla_evidence,
        "sla_alerts": sla_alerts,
        "disclaimer": (
            "本清单不是 SLA 达标证明，也不得把 warn/fail 项忽略后对外宣称企业级已就绪。"
            "商务可用性/响应时效以合同为准；平台只提供可核查证据。"
            "Phase B/C 运维雏形已收口，见 docs/PHASE_BC_COMPLETE.md。"
        ),
        "doc": "docs/PHASE_BC_COMPLETE.md",
    }
