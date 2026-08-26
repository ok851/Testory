# -*- coding: utf-8 -*-
"""GOAI Demo：合成故事 → 三角色闭环 → 写出 artifacts。

用法（仓库根目录）:
  # R06 下单失败一致性
  python demos/goai-agentteams/run_demo.py
  python demos/goai-agentteams/run_demo.py --variant mismatch

  # R08/R09/R10 门禁故事（HITL + Desktop 模拟 + L2）
  python demos/goai-agentteams/run_demo.py --story demos/goai-agentteams/input/guards_story.json --variant pass
  python demos/goai-agentteams/run_demo.py --suite guards --variant hitl_timeout

  python demos/goai-agentteams/run_demo.py --mode live   # 真实编排（需环境）

默认 --mode simulate：不访问外网/浏览器；门禁故事会真实调用 HitlGate / RiskGuard /
validate_desktop_step_result（不启动桌面应用）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# 仓库根
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_DEMO_DIR = Path(__file__).resolve().parent
_INPUT_ORDER = _DEMO_DIR / "input" / "order_fail_story.json"
_INPUT_GUARDS = _DEMO_DIR / "input" / "guards_story.json"
_SAMPLES = _DEMO_DIR / "samples" / "output"
_DEFAULT_ARTIFACTS = _ROOT / "artifacts" / "goai-agentteams"

_ORDER_VARIANTS = ("consistent", "mismatch")
_GUARDS_VARIANTS = ("pass", "hitl_timeout", "l2_denied", "desktop_softfail")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_story(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _story_kind(story: Dict[str, Any]) -> str:
    sid = str(story.get("story_id") or "")
    if "guard" in sid or sid.startswith("guards"):
        return "guards"
    return "order"


def simulate_execute(variant: str = "consistent") -> Any:
    """返回可注入 WebApiExecutor 的 execute_fn（下单失败故事）。"""

    def _execute(plan: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        order_id = "ORD-DEMO-404"
        api_status = "failed"
        if variant == "mismatch":
            ui_text = "下单成功"
            assert_details = [
                {"name": "order_id_present", "passed": True},
                {"name": "api_status_is_failed", "passed": True},
                {
                    "name": "ui_shows_fail_copy",
                    "passed": False,
                    "error": "期望包含「下单失败」，实际「下单成功」",
                },
            ]
            assertion_failed = 1
            assertion_passed = 2
            success = False
            error = "跨端断言失败 1 条"
            error_code = "CROSS_END_ASSERT_FAILED"
            web_ok = False
            web_err = "assert_text 未匹配: 下单失败"
        else:
            ui_text = "下单失败"
            assert_details = [
                {"name": "order_id_present", "passed": True},
                {"name": "api_status_is_failed", "passed": True},
                {"name": "ui_shows_fail_copy", "passed": True},
            ]
            assertion_failed = 0
            assertion_passed = 3
            success = True
            error = None
            error_code = None
            web_ok = True
            web_err = None

        stage_results: List[Dict[str, Any]] = [
            {
                "stage_id": "stage-api-order",
                "layer": "api",
                "ok_assert": True,
                "elapsed_ms": 12,
                "risk_level": "L0",
                "risk_decision": "allow",
                "extracted": {"order_id": order_id, "order_status": api_status},
            },
            {
                "stage_id": "stage-web-order",
                "layer": "web",
                "ok_assert": web_ok,
                "elapsed_ms": 45,
                "error": web_err,
                "error_code": None if web_ok else "ASSERT_TEXT_MISMATCH",
                "risk_level": "L1",
                "risk_decision": "allow",
                "extracted": {"ui_status_text": ui_text},
                "screenshot_path": f"artifacts/screenshots/{order_id}-web.png",
            },
        ]
        out: Dict[str, Any] = {
            "success": success,
            "gate_passed": success,
            "plan_id": (plan or {}).get("plan_id"),
            "variables": {
                "order_id": order_id,
                "order_status": api_status,
                "ui_status_text": ui_text,
            },
            "stage_results": stage_results,
            "assertion_passed": assertion_passed,
            "assertion_failed": assertion_failed,
            "assertion_details": assert_details,
            "simulate": True,
            "simulate_variant": variant,
        }
        if error:
            out["error"] = error
            out["error_code"] = error_code
        from ai_modules.execute.result_schema import normalize_cross_end_result

        return normalize_cross_end_result(out)

    return _execute


def _run_hitl_stage(variant: str, stage: Dict[str, Any]) -> Dict[str, Any]:
    """真实调用 HitlGate（非假常量）。"""
    from modules.ai.agent_hitl import (
        get_hitl_events,
        hitl_outcome_from_events,
        open_hitl_gate,
        resume_hitl_gate,
        wait_hitl_gate,
    )

    gate_id = str(stage.get("gate_id") or "goai-demo-captcha")
    prompt = str(stage.get("prompt") or "请完成验证码")
    hint = str(stage.get("hint") or "")
    since = time.time()
    t0 = time.perf_counter()

    open_hitl_gate(gate_id, reason=prompt, hint=hint, user_id="goai-demo", scope="demo")

    if variant == "hitl_timeout":
        ok = wait_hitl_gate(
            gate_id,
            timeout_s=0.35,
            poll_interval_s=0.05,
            reason=prompt,
            hint=hint,
            user_id="goai-demo",
            scope="demo",
        )
    else:
        def _resume() -> None:
            time.sleep(0.08)
            resume_hitl_gate(gate_id)

        threading.Thread(target=_resume, daemon=True).start()
        ok = wait_hitl_gate(
            gate_id,
            timeout_s=float(stage.get("timeout_s") or 5),
            poll_interval_s=0.05,
            reason=prompt,
            hint=hint,
            user_id="goai-demo",
            scope="demo",
        )

    events = get_hitl_events(gate_id=gate_id, since_ts=since - 0.05, limit=100)
    outcome = hitl_outcome_from_events(events)
    if ok and outcome == "unknown":
        outcome = "resumed"
    if (not ok) and outcome == "unknown":
        outcome = "timed_out"
    result: Dict[str, Any] = {
        "stage_id": stage.get("id") or "stage-hitl-captcha",
        "layer": "hitl",
        "ok_assert": bool(ok),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "hitl_gate_id": gate_id,
        "hitl_prompt": prompt,
        "hitl_events": events,
        "hitl_outcome": outcome,
        "risk_level": "L1",
        "risk_decision": "allow",
    }
    if not ok:
        result["error"] = "HITL 超时或已取消"
        result["error_code"] = "HITL_TIMEOUT" if outcome == "timed_out" else "HITL_TIMEOUT_OR_CANCEL"
    return result


def _run_desktop_stage(variant: str, stage: Dict[str, Any]) -> Dict[str, Any]:
    """用生产闸门 validate_desktop_step_result 校验模拟步骤（不启真机）。"""
    from modules.execution.step_executor import validate_desktop_step_result

    t0 = time.perf_counter()
    action = "get_text"
    if variant == "desktop_softfail":
        desk: Dict[str, Any] = {
            "status": "warning",
            "warning": "控件未命中",
            "error": "ERP 订单号未读到",
            "extracted_text": None,
        }
    else:
        desk = {
            "status": "success",
            "extracted_text": "ORD-DEMO-404",
            "store_as": "erp_order_id",
        }

    result: Dict[str, Any] = {
        "stage_id": stage.get("id") or "stage-desktop-erp",
        "layer": "desktop",
        "ok_assert": False,
        "elapsed_ms": 0,
        "risk_level": "L1",
        "risk_decision": "allow",
        "step_results": [desk],
        "simulate_desktop": True,
    }
    try:
        validate_desktop_step_result(desk, action)
    except Exception as exc:
        result["error"] = str(exc)
        result["error_code"] = "DESKTOP_STEP_FAILED"
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return result

    st = str(desk.get("status") or "").strip().lower()
    # 与编排器一致：warning 不得当绿
    if st not in ("success", "ok", "passed"):
        result["error"] = desk.get("error") or desk.get("warning") or f"桌面步骤 status={st!r} 不得当绿"
        result["error_code"] = "DESKTOP_SOFT_FAIL"
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return result

    result["ok_assert"] = True
    result["extracted"] = {"erp_order_id": desk.get("extracted_text")}
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


def _run_l2_stage(variant: str, stage: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """真实 RiskGuard：pass 变体先审批再放行；l2_denied 无令牌拒绝。"""
    from ai_modules.security.risk_guard import approve_risk, evaluate_stage_risk

    t0 = time.perf_counter()
    stage_local = dict(stage)
    if variant != "l2_denied":
        # 先触发 pending，再批准，把 token 写回阶段
        boot = evaluate_stage_risk(stage_local, plan=plan, user_id="goai-demo")
        if boot.approval_id:
            ok, token = approve_risk(boot.approval_id, approver="goai-demo-lead")
            if ok and token:
                stage_local["approval_token"] = token
        decision = evaluate_stage_risk(stage_local, plan=plan, user_id="goai-demo")
    else:
        decision = evaluate_stage_risk(stage_local, plan=plan, user_id="goai-demo")

    result: Dict[str, Any] = {
        "stage_id": stage.get("id") or "stage-l2-clear",
        "layer": "api",
        "ok_assert": bool(decision.ok),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "risk_level": decision.level,
        "risk_decision": decision.decision,
        "risk_events": list(decision.events or []),
        "simulate_l2": True,
    }
    if decision.approval_id:
        result["risk_approval_id"] = decision.approval_id
    if decision.ok:
        # 合成「清理成功」——仅在已审批后
        result["extracted"] = {"cleared": True}
    else:
        result["error"] = decision.error
        result["error_code"] = decision.error_code
    return result


def simulate_guards_execute(variant: str = "pass") -> Any:
    """门禁故事：HITL / Desktop 闸门 / RiskGuard 真实接线。"""

    def _execute(plan: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        from modules.ai.agent_hitl import reset_hitl_state_for_tests
        from ai_modules.security.risk_guard import reset_risk_guard_for_tests

        reset_hitl_state_for_tests()
        reset_risk_guard_for_tests()

        stages = {str(s.get("id")): s for s in (plan or {}).get("stages") or [] if isinstance(s, dict)}
        order_id = "ORD-DEMO-404"
        api_status = "failed"
        ui_text = "下单失败"
        stage_results: List[Dict[str, Any]] = []
        variables: Dict[str, Any] = {
            "order_id": order_id,
            "order_status": api_status,
        }

        # 1) API
        stage_results.append({
            "stage_id": "stage-api-order",
            "layer": "api",
            "ok_assert": True,
            "elapsed_ms": 10,
            "risk_level": "L0",
            "risk_decision": "allow",
            "extracted": {"order_id": order_id, "order_status": api_status},
        })

        # 2) HITL
        hitl_stage = stages.get("stage-hitl-captcha") or {
            "id": "stage-hitl-captcha",
            "gate_id": "goai-demo-captcha",
            "prompt": "请在浏览器完成验证码后继续",
            "timeout_s": 5,
        }
        hitl_res = _run_hitl_stage(variant, hitl_stage)
        stage_results.append(hitl_res)
        if not hitl_res.get("ok_assert"):
            return _guards_fail_out(plan, stage_results, variables, hitl_res, variant)

        # 3) Web
        stage_results.append({
            "stage_id": "stage-web-order",
            "layer": "web",
            "ok_assert": True,
            "elapsed_ms": 40,
            "risk_level": "L1",
            "risk_decision": "allow",
            "extracted": {"ui_status_text": ui_text},
            "screenshot_path": f"artifacts/screenshots/{order_id}-web.png",
        })
        variables["ui_status_text"] = ui_text

        # 4) Desktop
        desk_stage = stages.get("stage-desktop-erp") or {"id": "stage-desktop-erp"}
        desk_res = _run_desktop_stage(variant, desk_stage)
        stage_results.append(desk_res)
        if not desk_res.get("ok_assert"):
            return _guards_fail_out(plan, stage_results, variables, desk_res, variant)
        variables["erp_order_id"] = (desk_res.get("extracted") or {}).get("erp_order_id")

        # 5) L2
        l2_stage = stages.get("stage-l2-clear") or {
            "id": "stage-l2-clear",
            "risk_level": "L2",
            "risk_action": "clear_data",
            "layer": "api",
        }
        l2_res = _run_l2_stage(variant, l2_stage, plan or {})
        stage_results.append(l2_res)
        if not l2_res.get("ok_assert"):
            return _guards_fail_out(plan, stage_results, variables, l2_res, variant)

        assert_details = [
            {"name": "order_id_present", "passed": True},
            {"name": "api_status_is_failed", "passed": True},
            {"name": "ui_shows_fail_copy", "passed": True},
            {"name": "erp_matches_order", "passed": True},
        ]
        from ai_modules.execute.result_schema import normalize_cross_end_result

        return normalize_cross_end_result({
            "success": True,
            "gate_passed": True,
            "plan_id": (plan or {}).get("plan_id"),
            "variables": variables,
            "stage_results": stage_results,
            "assertion_passed": 4,
            "assertion_failed": 0,
            "assertion_details": assert_details,
            "simulate": True,
            "simulate_variant": variant,
            "simulate_suite": "guards",
        })

    return _execute


def _guards_fail_out(
    plan: Optional[Dict[str, Any]],
    stage_results: List[Dict[str, Any]],
    variables: Dict[str, Any],
    failed: Dict[str, Any],
    variant: str,
) -> Dict[str, Any]:
    from ai_modules.execute.result_schema import normalize_cross_end_result

    return normalize_cross_end_result({
        "success": False,
        "gate_passed": False,
        "plan_id": (plan or {}).get("plan_id"),
        "variables": variables,
        "stage_results": stage_results,
        "assertion_passed": 0,
        "assertion_failed": 0,
        "assertion_details": [],
        "error": failed.get("error") or "阶段失败",
        "error_code": failed.get("error_code") or "STAGE_FAILED",
        "simulate": True,
        "simulate_variant": variant,
        "simulate_suite": "guards",
    })


def write_artifacts(state: Any, out_dir: Path, story: Dict[str, Any]) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}

    state_path = out_dir / "test_run_state.json"
    state_path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["state"] = state_path

    report = state.report or {}
    report_path = out_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["report"] = report_path

    timeline = {
        "generated_at": _utc(),
        "story_id": story.get("story_id"),
        "run_id": state.run_id,
        "status": state.status,
        "agents_seen": state.agent_kinds_seen(),
        "events": state.events,
    }
    tl_path = out_dir / "timeline.json"
    tl_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["timeline"] = tl_path

    lines = [
        f"# GOAI AgentTeams Demo — {story.get('title')}",
        "",
        f"- run_id: `{state.run_id}`",
        f"- status: **{state.status}**",
        f"- agents: {', '.join(state.agent_kinds_seen())}",
        f"- evidence_level: {(state.report or {}).get('evidence_level')}",
        f"- reason: {(state.report or {}).get('reason')}",
        "",
        "## Events",
        "",
    ]
    for e in state.events:
        lines.append(
            f"- `{e.get('at')}` **{e.get('agent')}**/{e.get('kind')}: {e.get('message')}"
        )
    lines.append("")
    # 证据摘要
    hitl_n = sum(1 for x in state.evidence if x.get("kind") == "hitl")
    risk_n = sum(1 for x in state.evidence if x.get("kind") == "risk")
    lines.extend([
        "## Evidence kinds",
        "",
        f"- hitl: {hitl_n}",
        f"- risk: {risk_n}",
        f"- total_evidence: {len(state.evidence)}",
        "",
    ])
    summary_path = out_dir / "SUMMARY.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    paths["summary"] = summary_path
    return paths


def run_demo(
    *,
    mode: str = "simulate",
    variant: str = "consistent",
    artifacts_dir: Path | None = None,
    story_path: Path | None = None,
    suite: str | None = None,
) -> Dict[str, Any]:
    from ai_modules.agent_teams.roles import WebApiExecutorAgent
    from ai_modules.agent_teams.team_runner import run_cross_end_qa_team

    if suite == "guards":
        path = story_path or _INPUT_GUARDS
    else:
        path = story_path or _INPUT_ORDER

    story = load_story(path)
    kind = _story_kind(story) if suite is None else suite
    if kind == "guards" and variant in _ORDER_VARIANTS:
        variant = "pass"
    if kind == "order" and variant in _GUARDS_VARIANTS:
        raise SystemExit(
            f"variant={variant} 仅用于 guards 故事；请加 --suite guards 或 --story guards_story.json"
        )

    plan = story.get("plan")
    if not isinstance(plan, dict) or not plan.get("stages"):
        raise SystemExit("input story missing plan.stages")

    artifacts_root = artifacts_dir or _DEFAULT_ARTIFACTS
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = artifacts_root / f"{kind}-{variant}-{run_stamp}"

    kwargs: Dict[str, Any] = {
        "description": story.get("goal") or story.get("description") or "",
        "plan": plan,
        "user_id": "goai-demo",
        "idempotency_key": f"goai-demo-{kind}-{variant}-{run_stamp}",
        "persist": True,
    }

    if mode == "simulate":
        if kind == "guards":
            exec_fn = simulate_guards_execute(variant=variant)
        else:
            exec_fn = simulate_execute(variant=variant)
        kwargs["executor"] = WebApiExecutorAgent(
            execute_fn=exec_fn,
            record_history=False,
            trigger_source="demo",
        )
    elif mode != "live":
        raise SystemExit(f"unknown mode: {mode} (use simulate|live)")

    state = run_cross_end_qa_team(**kwargs)
    paths = write_artifacts(state, out_dir, story)

    trace_info: Dict[str, Any] = {}
    try:
        from ai_modules.execute.trace_pack import export_trace_pack

        trace_info = export_trace_pack(
            agent_run_id=state.run_id,
            out_dir=out_dir / "trace_pack",
            make_zip=True,
        )
        if trace_info.get("zip_path"):
            paths["trace_zip"] = Path(trace_info["zip_path"])
        if trace_info.get("pack_dir"):
            paths["trace_pack"] = Path(trace_info["pack_dir"])
    except Exception as exc:
        trace_info = {"ok": False, "error": str(exc)}

    if mode == "simulate" and kind == "order" and variant == "consistent":
        _SAMPLES.mkdir(parents=True, exist_ok=True)
        for name in ("report.json", "timeline.json", "test_run_state.json", "SUMMARY.md"):
            src = out_dir / name
            if src.is_file():
                (_SAMPLES / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    expect = (
        (story.get("simulate") or {}).get(variant) or {}
    ).get("expect_run_status")
    ok_expect = expect is None or state.status == expect

    return {
        "ok": ok_expect,
        "mode": mode,
        "suite": kind,
        "variant": variant,
        "run_id": state.run_id,
        "status": state.status,
        "expect_run_status": expect,
        "agents_seen": state.agent_kinds_seen(),
        "report": state.report,
        "artifacts_dir": str(out_dir),
        "paths": {k: str(v) for k, v in paths.items()},
        "evidence_kinds": sorted({e.get("kind") for e in state.evidence if e.get("kind")}),
        "trace_pack": {
            "ok": trace_info.get("ok"),
            "status": trace_info.get("status"),
            "pack_dir": trace_info.get("pack_dir"),
            "zip_path": trace_info.get("zip_path"),
            "error": trace_info.get("error"),
        },
    }


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="GOAI AgentTeams Demo")
    p.add_argument("--mode", choices=("simulate", "live"), default="simulate")
    p.add_argument(
        "--suite",
        choices=("order", "guards"),
        default=None,
        help="order=下单失败；guards=HITL+Desktop+L2",
    )
    p.add_argument(
        "--variant",
        default="consistent",
        help="order: consistent|mismatch；guards: pass|hitl_timeout|l2_denied|desktop_softfail",
    )
    p.add_argument("--artifacts-dir", type=Path, default=None)
    p.add_argument("--story", type=Path, default=None)
    args = p.parse_args(argv)

    allowed = set(_ORDER_VARIANTS) | set(_GUARDS_VARIANTS)
    if args.variant not in allowed:
        print(f"unknown variant: {args.variant}; allowed={sorted(allowed)}", file=sys.stderr)
        return 2

    result = run_demo(
        mode=args.mode,
        variant=args.variant,
        artifacts_dir=args.artifacts_dir,
        story_path=args.story,
        suite=args.suite,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        print(
            f"\n[FAIL] status={result.get('status')} "
            f"expected={result.get('expect_run_status')}",
            file=sys.stderr,
        )
        return 1
    print(f"\n[OK] artifacts → {result.get('artifacts_dir')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
