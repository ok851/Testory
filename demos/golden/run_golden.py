# -*- coding: utf-8 -*-
"""Golden 回归（R17）：离线诚实性门禁（G1–G18）。

用法::
    python demos/golden/run_golden.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ok(name: str) -> None:
    print(f"  PASS  {name}")


def _fail(name: str, msg: str) -> None:
    raise AssertionError(f"{name}: {msg}")


def case_g1_hitl_timeout_not_success() -> None:
    """G1: HITL 超时不得记为 True/success。"""
    from agent_hitl import open_hitl_gate, wait_hitl_gate

    open_hitl_gate("golden-hitl", reason="captcha")
    ok = wait_hitl_gate("golden-hitl", timeout_s=0.25, poll_interval_s=0.05)
    if ok is True:
        _fail("G1", "超时后 wait_hitl_gate 仍为 True")
    _ok("G1 HITL timeout honesty")


def case_g2_cross_end_schema() -> None:
    """G2: stage/cross_end 结果可归一到契约 Schema。"""
    from ai_modules.execute.result_schema import (
        CROSS_END_SCHEMA,
        STAGE_SCHEMA,
        normalize_cross_end_result,
        normalize_stage_result,
    )

    stage = normalize_stage_result(
        {"ok_assert": True, "extracted": {"order_id": "ORD-1"}, "warnings": []}
    )
    if stage.get("schema") != STAGE_SCHEMA:
        _fail("G2", f"stage schema 异常: {stage.get('schema')}")
    if stage.get("ok_assert") is not True:
        _fail("G2", "stage ok_assert 丢失")

    bad_stage = normalize_stage_result({"ok_assert": False, "error": "boom"})
    if bad_stage.get("ok_assert") is not False:
        _fail("G2", "失败 stage 被洗绿")

    ce = normalize_cross_end_result(
        {
            "success": False,
            "gate_passed": False,
            "stage_results": [bad_stage],
        }
    )
    if ce.get("schema") != CROSS_END_SCHEMA:
        _fail("G2", f"cross_end schema 异常: {ce.get('schema')}")
    if ce.get("success") is True:
        _fail("G2", "失败 cross_end 被洗绿")
    _ok("G2 cross_end schema")


def case_g3_desktop_uia_heal() -> None:
    """G3: 有限 UIA 自愈可提案；重试失败不假绿。"""
    os.environ["DESKTOP_RUNTIME_HEAL"] = "1"
    from ai_modules.optimize.desktop_runtime_heal import (
        propose_healed_desktop_step,
        run_desktop_step_with_optional_heal,
    )

    step = {
        "action": "click",
        "selector_value": json.dumps(
            {
                "element_snapshot": {
                    "selector": {
                        "anchor_props": "Button",
                        "key_candidates": [
                            {"property": "automation_id", "value": "old_id", "match": "equals"},
                            {"property": "uia-name", "value": "提交", "match": "equals"},
                        ],
                        "parent_chain": [],
                    }
                }
            },
            ensure_ascii=False,
        ),
    }
    healed, meta = propose_healed_desktop_step(step)
    if healed is None or "drop_automation_id_prefer_name" not in (meta.get("strategies") or []):
        _fail("G3", f"未提出 UIA 放宽策略: {meta}")

    calls = {"n": 0}

    def _exec(_s):
        calls["n"] += 1
        return {"status": "failed", "error": "miss", "verified": False}

    result, hmeta = run_desktop_step_with_optional_heal(step, execute_fn=_exec)
    if calls["n"] != 2:
        _fail("G3", f"应重试 1 次，实际执行 {calls['n']} 次")
    if hmeta.get("heal_succeeded") or result.get("status") in ("success", "ok", "passed"):
        _fail("G3", "自愈失败被洗绿")
    _ok("G3 desktop limited UIA heal")


def case_g4_enterprise_feature_entitlement() -> None:
    """G4: 企业档即使旧 features 缺键，目录能力仍可用；拒绝文案不含运维变量。"""
    from license_manager import LicenseManager, LicenseType

    os.environ["LICENSE_ENFORCE_FEATURES"] = "1"
    os.environ["DEPLOYMENT_MODE"] = "server"

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "license.key"
        lm = LicenseManager(license_file=str(path))
        lm._migrate_legacy_license_file = lambda: None  # type: ignore
        key = lm.generate_license(
            LicenseType.ENTERPRISE,
            issued_to="Golden",
            expires_days=7,
            license_id="lic_golden",
        )
        info = lm.validate_license(key)["info"]
        info.features = [f for f in (info.features or []) if f != "customer_audit_export"]
        lm._cached_license = info
        if not lm.check_feature_available("customer_audit_export"):
            _fail("G4", "企业档未合并目录能力 customer_audit_export")

        lm._cached_license = lm._create_default_free_license()
        denied = lm.build_feature_denied_message("customer_audit_export")
        if "LICENSE_ENFORCE" in denied:
            _fail("G4", "拒绝文案暴露运维变量")
    _ok("G4 enterprise feature entitlement")


def case_g5_recovery_skip_blocks_success() -> None:
    """G5: RECOVERY_SKIP 默认不得让 evaluate_pass 通过。"""
    from ai_modules.plan.context_bus import CrossEndContext

    ctx = CrossEndContext(plan_id="golden", scenario="skip")
    ctx.record_stage_result(
        "a",
        {"ok_assert": False, "error": "x", "skipped_failure": True, "recovery_action": "skip"},
    )
    ctx.record_stage_result("b", {"ok_assert": True})
    if ctx.evaluate_pass(ignore_skipped_failures=False) is not False:
        _fail("G5", "默认应挡掉 skipped failure")
    if ctx.evaluate_pass(ignore_skipped_failures=True) is not True:
        _fail("G5", "显式 ignore 时应可通过")
    _ok("G5 recovery_skip blocks success")


def case_g6_incident_memory() -> None:
    """G6: IncidentMemory / Runbook 可检索且不因命中判绿。"""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        from ai_modules.memory.incident_memory import search_runbooks, suggest_for_failure

        hits = search_runbooks("HITL_TIMEOUT", limit=3)
        if not hits:
            _fail("G6", "内置 runbook 未命中 HITL_TIMEOUT")
        tips = suggest_for_failure(error_code="DESKTOP_NO_SESSION", error_message="gateway down")
        if not tips:
            _fail("G6", "suggest_for_failure 无结果")
    _ok("G6 incident memory / runbook")


def case_g7_replan_verifier_to_planner() -> None:
    """G7: Verifier 失败后重规划再验证；仍失败不得假绿。"""
    import tempfile

    from ai_modules.agent_teams.team_runner import run_with_injected_execute

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        os.environ["AGENT_TEAMS_MAX_REPLAN"] = "1"
        plan = {
            "plan_id": "g7",
            "stages": [
                {
                    "id": "desk-1",
                    "layer": "desktop",
                    "steps": [
                        {
                            "action": "attach_window",
                            "desktop_spec": {"window_title_re": "^Exact$"},
                        }
                    ],
                }
            ],
        }

        def always_fail(p, **kwargs):
            return {
                "success": False,
                "error": "nope",
                "error_code": "DESKTOP_SOFT_FAIL",
                "stage_results": [{"stage_id": "desk-1", "ok_assert": False}],
                "assertion_failed": 0,
                "assertion_passed": 0,
            }

        state = run_with_injected_execute(
            always_fail,
            plan=plan,
            persist=False,
            record_history=False,
            allow_replan=True,
            max_replan=1,
        )
        if state.status == "success":
            _fail("G7", "重规划后仍失败却变绿")
        if int(getattr(state, "replan_count", 0) or 0) < 1:
            _fail("G7", "未触发重规划")
    _ok("G7 verifier→planner replan honesty")


def case_g8_five_roles() -> None:
    """G8: AgentTeams Spec ≥5 角色且 SDK bridge 可导出。"""
    from ai_modules.agent_teams import load_team_spec
    from ai_modules.agent_teams.sdk_bridge import assert_five_roles_in_spec

    missing = assert_five_roles_in_spec(load_team_spec())
    if missing:
        _fail("G8", f"Spec 缺少角色: {missing}")
    _ok("G8 five agent roles in Spec")


def case_g9_skill_promote_honesty() -> None:
    """G9: 失败运行不可沉淀 Skill；成功可写草稿。"""
    from ai_modules.skills.promote_from_run import promote_plan_to_skill_draft

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        plan = {
            "scenario": "g9",
            "stages": [{"id": "s1", "layer": "api", "steps": [{"action": "http_get"}]}],
        }
        _p, bad = promote_plan_to_skill_draft(plan, success=False)
        if bad.get("ok"):
            _fail("G9", "失败 plan 被允许沉淀")
        if bad.get("error_code") != "PROMOTE_REQUIRES_SUCCESS":
            _fail("G9", f"错误码异常: {bad.get('error_code')}")
        path, good = promote_plan_to_skill_draft(plan, success=True)
        if not good.get("ok") or not path:
            _fail("G9", "成功 plan 未能沉淀")
    _ok("G9 skill promote honesty")


def case_g10_farm_probe_not_parallel_pass() -> None:
    """G10: 农场探测失败不假称为并行成功；摘要含诚实 disclaimer。"""
    from ai_modules.enterprise.execution_farm import farm_summary, probe_node, register_node

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        node = register_node(name="g10", base_url="http://127.0.0.1:9")
        r = probe_node(node["node_id"], timeout_s=0.4)
        if r.get("ok") is True:
            _fail("G10", "不可达节点被标为探测成功")
        if r.get("error_code") != "NODE_UNREACHABLE":
            _fail("G10", f"期望 NODE_UNREACHABLE，得到 {r.get('error_code')}")
        summary = farm_summary()
        if "disclaimer" not in summary:
            _fail("G10", "farm_summary 缺少 disclaimer")
        if summary.get("node_count") != 1:
            _fail("G10", "节点未登记")
    _ok("G10 farm probe honesty")


def case_g11_dispatch_readiness_not_sla() -> None:
    """G11: 无在线节点时不得 dispatch_ready；ops readiness 不得 sla_claim。"""
    from ai_modules.enterprise.execution_farm import dispatch_readiness, register_node
    from ai_modules.enterprise.readiness import enterprise_ops_readiness

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        os.environ["DESKTOP_EXECUTION_MODE"] = "inprocess"
        os.environ.pop("DESKTOP_AGENT_GATEWAY_SECRET", None)
        register_node(name="g11", base_url="http://127.0.0.1:9")
        ready = dispatch_readiness()
        if ready.get("dispatch_ready") is True:
            _fail("G11", "未探测成功却 dispatch_ready")
        ops = enterprise_ops_readiness()
        if ops.get("sla_claim") is True:
            _fail("G11", "ops readiness 宣称 SLA 达标")
    _ok("G11 dispatch readiness / no SLA claim")


def case_g12_farm_gateway_and_mcp_live() -> None:
    """G12: 无 opt-in 不用农场 URL；live 无 URL 不得假绿。"""
    from ai_modules.enterprise.execution_farm import register_node
    from ai_modules.enterprise.gateway_resolve import resolve_desktop_gateway
    from testory_mcp.gateway_live import mcp_live_demo

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        os.environ.pop("DESKTOP_AGENT_GATEWAY_URL", None)
        os.environ["DESKTOP_FARM_GATEWAY"] = "0"
        register_node(name="g12", base_url="http://127.0.0.1:18766")
        r = resolve_desktop_gateway()
        if r.get("source") == "farm" or r.get("base_url"):
            _fail("G12", "未 opt-in 却解析出农场 URL")
        demo = mcp_live_demo(try_step=False)
        if (demo.get("health") or {}).get("ok") is True:
            _fail("G12", "无 Gateway URL 却 health.ok")
        if demo.get("honesty", {}).get("health_ok_means_case_pass") is True:
            _fail("G12", "honesty 标志被写坏")
    _ok("G12 farm gateway opt-in / MCP live honesty")


def case_g13_farm_jobs_and_sdk_export() -> None:
    """G13: 不支持的 job 不得成功；noop 成功仍 case_pass_claimed=false；SDK 导出可落盘。"""
    from ai_modules.agent_teams.sdk_bridge import export_sdk_events_bundle
    from ai_modules.agent_teams.test_run_state import TestRunState
    from ai_modules.enterprise.farm_jobs import enqueue_job

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        bad = enqueue_job(job_type="run_all_cases", auto_run=True)
        if bad.get("ok"):
            _fail("G13", "不支持的 job_type 被接受")
        good = enqueue_job(job_type="noop", auto_run=True)
        if not good.get("ok"):
            _fail("G13", "noop 应成功")
        if good.get("case_pass_claimed") is True:
            _fail("G13", "作业成功却宣称用例通过")
        st = TestRunState.create(goal="g13")
        st.emit(agent="Planner", kind="note", message="n")
        st.set_status("failed")
        exported = export_sdk_events_bundle(st, out_dir=Path(td) / "sdk")
        if not (Path(td) / "sdk" / "sdk_events.json").is_file():
            _fail("G13", "SDK 事件未写出")
        if exported["payload"].get("case_pass_claimed") is True:
            _fail("G13", "SDK 导出宣称用例通过")
    _ok("G13 farm jobs / SDK export honesty")


def case_g14_fanout_not_suite_pass() -> None:
    """G14: fan-out 无节点失败；有节点但不可达时不得 parallel_suite_pass_claimed。"""
    from ai_modules.enterprise.execution_farm import register_node
    from ai_modules.enterprise.farm_batch import run_probe_fanout

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        empty = run_probe_fanout()
        if empty.get("parallel_suite_pass_claimed"):
            _fail("G14", "空农场却宣称并行套件通过")
        if empty.get("error_code") != "NO_NODES":
            _fail("G14", "空农场应 NO_NODES")
        register_node(name="g14", base_url="http://127.0.0.1:9")
        batch = run_probe_fanout(auto_run=True)
        if batch.get("parallel_suite_pass_claimed") or batch.get("case_pass_claimed"):
            _fail("G14", "fan-out 宣称用例/套件通过")
        if batch.get("all_nodes_reachable") is True:
            _fail("G14", "不可达节点被标为全可达")
    _ok("G14 fan-out honesty")


def case_g15_sla_and_sdk_runtime() -> None:
    """G15: SLA 证据 sla_claim=false；SDK runtime 未安装不得宣称已接入。"""
    from ai_modules.agent_teams.sdk_runtime import try_official_sdk_runtime
    from ai_modules.enterprise.sla_evidence import record_metric, summarize_sla_evidence

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        record_metric(kind="g15", ok=True, latency_ms=5)
        s = summarize_sla_evidence()
        if s.get("sla_claim") is True:
            _fail("G15", "SLA 证据宣称达标")
        rt = try_official_sdk_runtime(None)
        if rt.get("multi_agent_sdk_runtime_claimed") is True:
            _fail("G15", "未安装 SDK 却宣称 runtime 已接入")
        if rt.get("error_code") != "SDK_NOT_INSTALLED" and rt.get("ok") is True:
            # ok=True 仅当真安装了 SDK；本机默认应未安装
            pass
        if not rt.get("ok") and rt.get("error_code") != "SDK_NOT_INSTALLED":
            _fail("G15", f"期望 SDK_NOT_INSTALLED，得到 {rt.get('error_code')}")
    _ok("G15 SLA evidence / SDK runtime probe")


def case_g16_remote_farm_dispatch_gate() -> None:
    """G16: remote+force 无在线节点 → FARM_DISPATCH_NOT_READY；gate=0 可跳过。"""
    from ai_modules.execute.farm_dispatch_gate import check_farm_dispatch_gate
    from ai_modules.enterprise.execution_farm import register_node

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        os.environ["DESKTOP_EXECUTION_MODE"] = "remote"
        os.environ["DESKTOP_AGENT_GATEWAY_SECRET"] = "x"
        os.environ["DESKTOP_AGENT_GATEWAY_URL"] = "http://127.0.0.1:8766"
        os.environ["DESKTOP_FARM_DISPATCH_GATE"] = "force"
        register_node(name="g16", base_url="http://127.0.0.1:8766")
        blocked = check_farm_dispatch_gate()
        if blocked.get("ok"):
            _fail("G16", "force 模式未探测成功节点却放行")
        if blocked.get("error_code") != "FARM_DISPATCH_NOT_READY":
            _fail("G16", f"期望 FARM_DISPATCH_NOT_READY，得到 {blocked.get('error_code')}")
        if blocked.get("case_pass_claimed"):
            _fail("G16", "门禁结果宣称用例通过")
        os.environ["DESKTOP_FARM_DISPATCH_GATE"] = "0"
        skipped = check_farm_dispatch_gate()
        if not skipped.get("ok") or not skipped.get("skipped"):
            _fail("G16", "GATE=0 应跳过农场门禁")
    _ok("G16 remote farm dispatch gate")


def case_g17_worker_drain_and_sla_alerts() -> None:
    """G17: Worker drain 不宣称套件通过；SLA 告警 sla_met 恒 false。"""
    from ai_modules.enterprise.farm_jobs import enqueue_job
    from ai_modules.enterprise.farm_worker import drain_queued_jobs
    from ai_modules.enterprise.sla_alerts import evaluate_sla_alerts
    from ai_modules.enterprise.sla_evidence import record_metric

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        os.environ["SLA_ALERT_MIN_SAMPLES"] = "2"
        os.environ["SLA_ALERT_LATENCY_P95_MS"] = "1"
        enqueue_job(job_type="noop", auto_run=False)
        drained = drain_queued_jobs(limit=5)
        if drained.get("parallel_suite_pass_claimed") or drained.get("case_pass_claimed"):
            _fail("G17", "Worker 宣称用例/套件通过")
        if drained.get("drained") != 1 or drained.get("succeeded") != 1:
            _fail("G17", "noop 队列未正确 drain")
        record_metric(kind="g17", ok=True, latency_ms=50)
        record_metric(kind="g17", ok=True, latency_ms=80)
        al = evaluate_sla_alerts()
        if al.get("sla_met") is True or al.get("sla_claim") is True:
            _fail("G17", "SLA 告警宣称达标")
    _ok("G17 farm worker / SLA alerts honesty")


def case_g18_phase_bc_closed() -> None:
    """G18: Phase B/C 收口标记；无 Webhook URL 时不假宣称已推送/已达标。"""
    from ai_modules.enterprise.readiness import enterprise_ops_readiness
    from ai_modules.enterprise.sla_webhook import maybe_post_sla_webhook

    with tempfile.TemporaryDirectory() as td:
        os.environ["UAT_DATA_DIR"] = td
        os.environ.pop("SLA_ALERT_WEBHOOK_URL", None)
        ops = enterprise_ops_readiness()
        if not ops.get("phase_bc_closed"):
            _fail("G18", "ops-readiness 未标记 phase_bc_closed")
        if ops.get("sla_claim") is True:
            _fail("G18", "收口后仍宣称 SLA")
        wh = maybe_post_sla_webhook(force=True)
        if wh.get("posted") is True:
            _fail("G18", "无 URL 却 posted=true")
        if wh.get("sla_met") is True:
            _fail("G18", "webhook 宣称 sla_met")
    _ok("G18 Phase B/C closed / webhook honesty")


CASES = [
    ("G1", case_g1_hitl_timeout_not_success),
    ("G2", case_g2_cross_end_schema),
    ("G3", case_g3_desktop_uia_heal),
    ("G4", case_g4_enterprise_feature_entitlement),
    ("G5", case_g5_recovery_skip_blocks_success),
    ("G6", case_g6_incident_memory),
    ("G7", case_g7_replan_verifier_to_planner),
    ("G8", case_g8_five_roles),
    ("G9", case_g9_skill_promote_honesty),
    ("G10", case_g10_farm_probe_not_parallel_pass),
    ("G11", case_g11_dispatch_readiness_not_sla),
    ("G12", case_g12_farm_gateway_and_mcp_live),
    ("G13", case_g13_farm_jobs_and_sdk_export),
    ("G14", case_g14_fanout_not_suite_pass),
    ("G15", case_g15_sla_and_sdk_runtime),
    ("G16", case_g16_remote_farm_dispatch_gate),
    ("G17", case_g17_worker_drain_and_sla_alerts),
    ("G18", case_g18_phase_bc_closed),
]


def main() -> int:
    print("Golden honesty suite (R17)")
    failed = []
    for cid, fn in CASES:
        try:
            fn()
        except Exception as e:
            failed.append((cid, str(e)))
            print(f"  FAIL  {cid}: {e}")
    print("")
    if failed:
        print(f"RESULT: FAILED ({len(failed)}/{len(CASES)})")
        return 1
    print(f"RESULT: PASSED ({len(CASES)}/{len(CASES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
