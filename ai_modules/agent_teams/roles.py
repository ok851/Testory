# -*- coding: utf-8 -*-
"""三角色 Agent：Planner / WebApiExecutor / Verifier（包装现有跨端能力）。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .test_run_state import TestRunState


class PlannerAgent:
    """接收 NL 或已有 plan → 产出 CrossEndPlan（包装 CrossEndPlanDecomposer）。"""

    role = "Planner"

    def __init__(self, decomposer: Any = None):
        self._decomposer = decomposer

    def _get_decomposer(self) -> Any:
        if self._decomposer is not None:
            return self._decomposer
        from ai_modules.plan.plan_decomposer import CrossEndPlanDecomposer

        self._decomposer = CrossEndPlanDecomposer()
        return self._decomposer

    def run(
        self,
        state: TestRunState,
        *,
        description: str = "",
        plan: Optional[Dict[str, Any]] = None,
    ) -> TestRunState:
        state.set_status("planning")
        state.emit(self.role, "dispatch", "规划跨端任务图")

        if isinstance(plan, dict) and plan.get("stages"):
            state.plan = plan
            state.emit(
                self.role,
                "complete",
                "使用调用方提供的 plan（跳过 LLM）",
                {"plan_id": plan.get("plan_id"), "stages": len(plan.get("stages") or [])},
            )
            return state

        goal = (description or state.goal or "").strip()
        if not goal:
            state.errors.append("Planner: description/plan 均为空")
            state.emit(self.role, "fail", "缺少目标描述与 plan")
            state.set_status("failed")
            return state

        try:
            result = self._get_decomposer().decompose_sync(goal)
        except Exception as exc:
            state.errors.append(f"Planner: {exc}")
            state.emit(self.role, "fail", str(exc))
            state.set_status("failed")
            return state

        if not result.get("ok") or not isinstance(result.get("plan"), dict):
            err = (result.get("warnings") or ["分解失败"])[0]
            state.errors.append(f"Planner: {err}")
            state.emit(self.role, "fail", str(err), {"warnings": result.get("warnings")})
            state.set_status("failed")
            return state

        state.plan = result["plan"]
        state.emit(
            self.role,
            "complete",
            "已生成 CrossEndPlan",
            {
                "plan_id": state.plan.get("plan_id"),
                "stages": len(state.plan.get("stages") or []),
                "warnings": result.get("warnings") or [],
            },
        )
        return state


class WebApiExecutorAgent:
    """执行 plan（包装 execute_cross_end_plan），结果写回共享状态。"""

    role = "WebApiExecutor"

    def __init__(
        self,
        execute_fn: Optional[Callable[..., Dict[str, Any]]] = None,
        *,
        record_history: bool = True,
        project_id: Any = None,
        trigger_source: str = "ui",
    ):
        self._execute_fn = execute_fn
        self._record_history = bool(record_history)
        self._project_id = project_id
        self._trigger_source = trigger_source or "ui"

    def _get_execute(self) -> Callable[..., Dict[str, Any]]:
        if self._execute_fn is not None:
            return self._execute_fn
        from ai_modules.execute.orchestrator import execute_cross_end_plan

        return execute_cross_end_plan

    def run(self, state: TestRunState) -> TestRunState:
        if state.status == "failed":
            return state
        plan = state.plan
        if not isinstance(plan, dict) or not plan.get("stages"):
            state.errors.append("WebApiExecutor: plan.stages 为空")
            state.emit(self.role, "fail", "无可执行 plan")
            state.set_status("failed")
            return state

        state.set_status("executing")
        state.emit(
            self.role,
            "dispatch",
            "执行跨端阶段",
            {"plan_id": plan.get("plan_id"), "stages": len(plan.get("stages") or [])},
        )

        assertions = (
            plan.get("cross_end_assertions")
            or plan.get("assertions")
            or []
        )
        if not isinstance(assertions, list):
            assertions = []

        try:
            result = self._get_execute()(
                plan,
                cross_end_assertions=assertions,
                user_id=state.user_id,
                record_history=self._record_history,
                project_id=self._project_id,
                trigger_source=self._trigger_source,
            )
        except TypeError:
            # 注入的假 execute 可能不接受新关键字
            try:
                result = self._get_execute()(
                    plan,
                    cross_end_assertions=assertions,
                    user_id=state.user_id,
                )
            except Exception as exc:
                state.errors.append(f"WebApiExecutor: {exc}")
                state.emit(self.role, "fail", str(exc))
                state.set_status("failed")
                return state
        except Exception as exc:
            state.errors.append(f"WebApiExecutor: {exc}")
            state.emit(self.role, "fail", str(exc))
            state.set_status("failed")
            return state

        if not isinstance(result, dict):
            state.errors.append("WebApiExecutor: 执行结果非 dict")
            state.emit(self.role, "fail", "执行结果非法")
            state.set_status("failed")
            return state

        state.execution = {
            "success": bool(result.get("success")),
            "error": result.get("error"),
            "error_code": result.get("error_code"),
            "gate_passed": result.get("gate_passed"),
            "assertion_passed": result.get("assertion_passed"),
            "assertion_failed": result.get("assertion_failed"),
            "user_hint": result.get("user_hint"),
            "lock": result.get("lock"),
            "run_history_id": result.get("run_history_id"),
        }
        state.stage_results = list(result.get("stage_results") or [])
        # 共享 vars：优先 variables / vars
        vars_blob = result.get("variables") or result.get("vars") or {}
        if isinstance(vars_blob, dict):
            state.vars.update(vars_blob)
        # 证据索引：截图 / 错误 / 断言明细
        for sr in state.stage_results:
            if not isinstance(sr, dict):
                continue
            sid = sr.get("stage_id") or "unknown"
            if sr.get("screenshot") or sr.get("screenshot_path"):
                state.evidence.append({
                    "kind": "screenshot",
                    "stage_id": sid,
                    "path": sr.get("screenshot") or sr.get("screenshot_path"),
                })
            if sr.get("hitl_events") or sr.get("hitl_gate_id"):
                state.evidence.append({
                    "kind": "hitl",
                    "stage_id": sid,
                    "gate_id": sr.get("hitl_gate_id"),
                    "outcome": sr.get("hitl_outcome"),
                    "events": sr.get("hitl_events") or [],
                })
            if sr.get("risk_level") or sr.get("risk_events") or sr.get("risk_approval_id"):
                state.evidence.append({
                    "kind": "risk",
                    "stage_id": sid,
                    "level": sr.get("risk_level"),
                    "decision": sr.get("risk_decision"),
                    "approval_id": sr.get("risk_approval_id"),
                    "events": sr.get("risk_events") or [],
                })
            if sr.get("ok_assert") is False:
                state.evidence.append({
                    "kind": "stage_failure",
                    "stage_id": sid,
                    "error": sr.get("error"),
                    "error_code": sr.get("error_code"),
                })
        for ad in result.get("assertion_details") or []:
            if isinstance(ad, dict):
                state.evidence.append({
                    "kind": "assertion",
                    "passed": bool(ad.get("passed") or ad.get("ok")),
                    "detail": ad,
                })

        ok = bool(result.get("success"))
        if ok:
            state.emit(
                self.role,
                "complete",
                "阶段执行完成",
                {"stages": len(state.stage_results), "success": True},
            )
        else:
            err = result.get("error") or "执行未通过门禁"
            state.errors.append(f"WebApiExecutor: {err}")
            state.emit(
                self.role,
                "complete",
                f"执行结束（未通过）: {err}",
                {
                    "success": False,
                    "error_code": result.get("error_code"),
                    "stages": len(state.stage_results),
                },
            )
            # 不在此设 failed：交给 Verifier 统一出报告与终态
        return state


class VerifierAgent:
    """断言聚合 + 证据等级报告；唯一决定 success/failed 终态。"""

    role = "Verifier"

    def run(self, state: TestRunState) -> TestRunState:
        if state.status == "failed" and not state.execution and not state.stage_results:
            # Planner 已失败：仍产出报告
            state.set_status("verifying")
            state.emit(self.role, "dispatch", "汇总失败报告")
            report = self._build_report(state, passed=False, reason="规划阶段失败")
            state.report = report
            state.emit(self.role, "complete", "报告已生成（规划失败）", {"passed": False})
            state.set_status("failed")
            return state

        state.set_status("verifying")
        state.emit(self.role, "dispatch", "聚合断言与证据")

        exec_ok = bool((state.execution or {}).get("success"))
        stage_fails = [
            s for s in state.stage_results
            if isinstance(s, dict) and s.get("ok_assert") is False and not s.get("cleanup")
        ]
        assert_failed = int((state.execution or {}).get("assertion_failed") or 0)

        # 诚实门禁：仅当执行 success 且无未通过阶段/断言时通过
        passed = exec_ok and not stage_fails and assert_failed == 0
        if not state.stage_results and not state.execution:
            passed = False
            reason = "无执行结果，不得判绿"
        elif not exec_ok:
            reason = (state.execution or {}).get("error") or "执行门禁未通过"
        elif stage_fails:
            reason = f"{len(stage_fails)} 个阶段断言失败"
        elif assert_failed > 0:
            reason = f"跨端断言失败 {assert_failed} 条"
        else:
            reason = "全部阶段与断言通过"

        report = self._build_report(state, passed=passed, reason=reason)
        state.report = report
        state.emit(
            self.role,
            "complete",
            f"验证{'通过' if passed else '未通过'}: {reason}",
            {"passed": passed, "evidence_level": report.get("evidence_level")},
        )
        state.set_status("success" if passed else "failed")
        if not passed and reason not in state.errors:
            state.errors.append(f"Verifier: {reason}")
        return state

    def _build_report(
        self,
        state: TestRunState,
        *,
        passed: bool,
        reason: str,
    ) -> Dict[str, Any]:
        strong = 0
        weak = 0
        missing = 0
        for ev in state.evidence:
            if not isinstance(ev, dict):
                missing += 1
                continue
            kind = ev.get("kind")
            if kind == "assertion":
                if ev.get("passed"):
                    strong += 1
                else:
                    missing += 1
            elif kind == "screenshot":
                weak += 1
            elif kind == "stage_failure":
                missing += 1
            else:
                weak += 1

        if not state.evidence:
            if state.stage_results and passed:
                weak += 1  # 仅有阶段 ok，无断言/截图 → 弱证据
            else:
                missing += 1

        if strong > 0 and missing == 0:
            level = "strong"
        elif strong > 0 or (weak > 0 and passed):
            level = "weak"
        else:
            level = "missing"

        return {
            "passed": passed,
            "reason": reason,
            "evidence_level": level,
            "evidence_counts": {
                "strong": strong,
                "weak": weak,
                "missing": missing,
            },
            "stages_total": len(state.stage_results),
            "stages_failed": len([
                s for s in state.stage_results
                if isinstance(s, dict) and s.get("ok_assert") is False and not s.get("cleanup")
            ]),
            "assertion_passed": (state.execution or {}).get("assertion_passed"),
            "assertion_failed": (state.execution or {}).get("assertion_failed"),
            "agents_seen": state.agent_kinds_seen(),
            "run_id": state.run_id,
        }
