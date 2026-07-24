# -*- coding: utf-8 -*-
"""testory-cross-end-qa-team：本地可加载 Spec + 三角色闭环 runner。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .roles import PlannerAgent, VerifierAgent, WebApiExecutorAgent
from .test_run_state import TestRunState, save_run

_SPEC_PATH = Path(__file__).resolve().parent / "specs" / "testory-cross-end-qa-team.json"


def load_team_spec(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or _SPEC_PATH
    if not p.is_file():
        return {
            "team_id": "testory-cross-end-qa-team",
            "name": "Testory Cross-End QA Team",
            "version": "0.1",
            "roles": [
                {"id": "Planner", "skill": "CrossEndDecompose"},
                {"id": "WebApiExecutor", "skill": "WebBrowse+ApiHttp"},
                {"id": "Verifier", "skill": "CrossEndAssert+Evidence"},
            ],
            "control_plane": "local",
            "note": "Spec file missing; using embedded default",
        }
    return json.loads(p.read_text(encoding="utf-8"))


def run_cross_end_qa_team(
    *,
    description: str = "",
    plan: Optional[Dict[str, Any]] = None,
    user_id: str = "",
    run_id: str = "",
    idempotency_key: str = "",
    project_id: Any = None,
    planner: Optional[PlannerAgent] = None,
    executor: Optional[WebApiExecutorAgent] = None,
    verifier: Optional[VerifierAgent] = None,
    persist: bool = True,
    record_history: bool = True,
) -> TestRunState:
    """最小闭环：Planner → WebApiExecutor → Verifier，全程写 TestRunState。

    默认由本函数统一以 test_type=agent_teams 写入 run_history（Executor 侧关闭跨端重复记账）。
    """
    spec = load_team_spec()
    state = TestRunState.create(
        goal=description or (plan or {}).get("scenario", ""),
        user_id=user_id,
        team_id=str(spec.get("team_id") or "testory-cross-end-qa-team"),
        idempotency_key=idempotency_key,
        run_id=run_id,
    )
    state.emit(
        "system",
        "note",
        "启动 Agent 团队闭环",
        {"team_id": state.team_id, "roles": [r.get("id") for r in (spec.get("roles") or [])]},
    )

    planner = planner or PlannerAgent()
    if executor is None:
        executor = WebApiExecutorAgent(
            record_history=False,
            project_id=project_id,
            trigger_source="agent_teams",
        )
    elif record_history and hasattr(executor, "_record_history"):
        # 避免 Executor 写 cross_end + 本函数再写 agent_teams 双记账
        executor._record_history = False
        if project_id is not None and hasattr(executor, "_project_id"):
            executor._project_id = project_id
        if hasattr(executor, "_trigger_source"):
            executor._trigger_source = "agent_teams"
    verifier = verifier or VerifierAgent()

    state = planner.run(state, description=description, plan=plan)
    if persist:
        save_run(state)
    if state.status != "failed":
        state = executor.run(state)
        if persist:
            save_run(state)
    state = verifier.run(state)
    if persist:
        save_run(state)

    if record_history:
        try:
            from ai_modules.execute.cross_end_run_audit import record_cross_end_execution

            # 合成与编排一致的 result，供历史门禁映射
            exec_blob = state.execution or {}
            synth = {
                "success": state.status == "success",
                "gate_passed": state.status == "success",
                "error": (state.report or {}).get("reason") or (state.errors[-1] if state.errors else ""),
                "error_code": exec_blob.get("error_code"),
                "plan_id": (state.plan or {}).get("plan_id") if isinstance(state.plan, dict) else "",
                "scenario": state.goal,
                "variables": dict(state.vars or {}),
                "stage_results": list(state.stage_results or []),
                "assertion_passed": exec_blob.get("assertion_passed"),
                "assertion_failed": exec_blob.get("assertion_failed"),
                "lock": exec_blob.get("lock"),
                "started_at": state.created_at,
                "finished_at": state.finished_at or state.updated_at,
            }
            audit = record_cross_end_execution(
                synth,
                plan=state.plan if isinstance(state.plan, dict) else {},
                test_type="agent_teams",
                user_id=user_id,
                project_id=project_id,
                trigger_source="agent_teams",
                agent_run_id=state.run_id,
            )
            state.emit(
                "system",
                "note",
                "已写入运行历史",
                {
                    "run_history_id": audit.get("run_history_id"),
                    "audit_id": audit.get("audit_id"),
                    "status": audit.get("status"),
                },
            )
            if persist:
                save_run(state)
        except Exception as exc:
            state.emit("system", "note", f"运行历史写入失败: {exc}")

    return state


def run_with_injected_execute(
    execute_fn: Callable[..., Dict[str, Any]],
    **kwargs: Any,
) -> TestRunState:
    """测试辅助：注入 execute_cross_end_plan。"""
    return run_cross_end_qa_team(
        executor=WebApiExecutorAgent(
            execute_fn=execute_fn,
            record_history=False,
            project_id=kwargs.get("project_id"),
            trigger_source="agent_teams",
        ),
        **kwargs,
    )
