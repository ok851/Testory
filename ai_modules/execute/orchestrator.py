# -*- coding: utf-8 -*-
"""跨端联动场景编排：加载 CrossEndPlan → 调度四端 Skill → 上下文传递 → 同步 → 恢复 → 报告。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import sys
import os
_plan_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plan")
if _plan_dir not in sys.path:
    sys.path.insert(0, _plan_dir)

from ai_modules.plan.context_bus import CrossEndContext
from ai_modules.plan.sync_manager import SyncPointManager
from ai_modules.plan.recovery_engine import RecoveryEngine, RECOVERY_RETRY, RECOVERY_SKIP, RECOVERY_ABORT
from ai_modules.plan.cross_end_assertion import run_cross_end_assertions
from ai_modules.plan.api_skill_adapter import ApiSkillAdapter


_SCENARIO_STORE = None


def _store_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "cross_platform_scenarios.json"


def _load_all() -> List[Dict[str, Any]]:
    p = _store_path()
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_all(items: List[Dict[str, Any]]) -> None:
    p = _store_path()
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_cross_platform_scenarios() -> List[Dict[str, Any]]:
    return _load_all()


def get_cross_platform_scenario(scenario_id: str) -> Optional[Dict[str, Any]]:
    sid = (scenario_id or "").strip()
    for item in _load_all():
        if str(item.get("scenario_id") or "") == sid:
            return item
    return None


def save_cross_platform_scenario(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload or {})
    sid = (data.get("scenario_id") or "").strip() or str(uuid.uuid4())[:12]
    data["scenario_id"] = sid
    if not data.get("plan") and not data.get("web_case_id"):
        return {"success": False, "error": "至少指定 plan 或 web_case_id"}
    items = _load_all()
    replaced = False
    for i, item in enumerate(items):
        if str(item.get("scenario_id") or "") == sid:
            items[i] = data
            replaced = True
            break
    if not replaced:
        items.append(data)
    _save_all(items)
    return {"success": True, "scenario": data}


def delete_cross_platform_scenario(scenario_id: str) -> Dict[str, Any]:
    sid = (scenario_id or "").strip()
    items = [x for x in _load_all() if str(x.get("scenario_id") or "") != sid]
    if len(items) == len(_load_all()):
        return {"success": False, "error": "场景不存在"}
    _save_all(items)
    return {"success": True}


def _execute_api_stage(
    stage: Dict[str, Any], context: CrossEndContext
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    adapter = ApiSkillAdapter()
    resolved_stage = context.resolve_deep(dict(stage))
    result, extracted = adapter.execute(resolved_stage, dict(context._variables))
    return result, extracted


def _execute_ui_stage(
    stage: Dict[str, Any], context: CrossEndContext
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    layer = (stage.get("layer") or "").lower()
    resolved_stage = context.resolve_deep(dict(stage))
    steps = resolved_stage.get("steps", [])
    extracted: Dict[str, Any] = {}

    result: Dict[str, Any] = {
        "ok_assert": True,
        "error": None,
        "elapsed_ms": 0,
        "stage_id": stage.get("id", ""),
        "layer": layer,
        "steps_executed": len(steps),
    }

    if not steps:
        result["error"] = "UI stage has no steps defined"
        result["ok_assert"] = False
        return result, extracted

    t0 = time.perf_counter()

    try:
        if layer == "web":
            from browser_manager import get_page
            from .web_runner import execute_single_web_step

            page = get_page()
            if page is None:
                result.setdefault("warnings", []).append(
                    "browser_manager: no active page, UI stage skipped"
                )
            else:
                for step in steps:
                    step_result = execute_single_web_step(step, page)
                    if not step_result.get("ok"):
                        result["ok_assert"] = False
                        result["error"] = step_result.get("error")
                        break
        elif layer == "mobile":
            from mobile_executor import get_mobile_executor
            executor = get_mobile_executor()
            executor.execute_steps(steps)
        elif layer == "desktop":
            from desktop_automation import sync_desktop_execute_step
            for step in steps:
                sync_desktop_execute_step(step)

    except Exception as e:
        result["ok_assert"] = False
        result["error"] = str(e)

    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result, extracted


def execute_cross_end_plan(
    plan: Dict[str, Any],
    *,
    cross_end_assertions: Optional[List[Dict[str, Any]]] = None,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:

    plan_id = plan.get("plan_id", str(uuid.uuid4())[:12])
    scenario = plan.get("scenario", "未命名场景")
    stages = plan.get("stages", [])

    if not stages:
        return {"success": False, "error": "Plan has no stages"}

    context = CrossEndContext(plan_id=plan_id, scenario=scenario)
    sync_mgr = SyncPointManager(context)
    sync_mgr.set_plan_stages(stages)
    recovery = RecoveryEngine()

    context.mark_start()

    stage_results: List[Dict[str, Any]] = []
    cleanup_stages: List[Dict[str, Any]] = []
    normal_stages: List[Dict[str, Any]] = []
    final_error: Optional[str] = None

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if stage.get("cleanup"):
            cleanup_stages.append(stage)
        else:
            normal_stages.append(stage)

    for stage in normal_stages:
        stage_id = stage.get("id", "unknown")
        layer = stage.get("layer", "api")
        depends_on = stage.get("depends_on", [])

        if not sync_mgr.acquire(stage_id, depends_on):
            result = {
                "stage_id": stage_id,
                "ok_assert": False,
                "error": f"依赖同步点未满足: {depends_on}",
                "elapsed_ms": 0,
            }
            stage_results.append(result)
            context.record_stage_result(stage_id, result)
            final_error = result["error"]
            break

        if layer == "api":
            result, extracted = _execute_api_stage(stage, context)
        else:
            result, extracted = _execute_ui_stage(stage, context)

        result["stage_id"] = stage_id
        result["layer"] = layer
        stage_results.append(result)
        context.record_stage_result(stage_id, result, extracted)

        if progress_callback:
            try:
                progress_callback(stage_id, result.get("ok_assert", False))
            except Exception:
                pass

        if not result.get("ok_assert"):
            on_failure = stage.get("on_failure", "abort")
            action = recovery.decide(stage_id, result.get("error", ""), on_failure)
            while action == RECOVERY_RETRY:
                result, extracted = _execute_api_stage(stage, context) if layer == "api" else _execute_ui_stage(stage, context)
                result["stage_id"] = stage_id
                result["layer"] = layer
                context.record_stage_result(stage_id, result, extracted)
                if result.get("ok_assert"):
                    break
                action = recovery.decide(stage_id, result.get("error", ""), on_failure)
            if not result.get("ok_assert"):
                if action == RECOVERY_SKIP:
                    continue
                final_error = result.get("error")
                break

    for cleanup_stage in cleanup_stages:
        stage_id = cleanup_stage.get("id", "cleanup")
        cleanup_layer = cleanup_stage.get("layer", "api")
        if progress_callback:
            try:
                progress_callback(f"cleanup-{stage_id}", None)
            except Exception:
                pass
        try:
            # 清理阶段按 layer 分派：API / Web / Mobile / Desktop
            if cleanup_layer == "api":
                result, _ = _execute_api_stage(cleanup_stage, context)
            else:
                result, _ = _execute_ui_stage(cleanup_stage, context)
            result["stage_id"] = stage_id
            result["layer"] = cleanup_layer
            result["cleanup"] = True
            stage_results.append(result)
            context.record_stage_result(stage_id, result)
        except Exception as e:
            stage_results.append({
                "stage_id": stage_id,
                "ok_assert": False,
                "error": str(e),
                "layer": cleanup_layer,
                "cleanup": True,
            })

    if cross_end_assertions:
        passed, failed = run_cross_end_assertions(context, cross_end_assertions)

    context.mark_finish()

    summary = context.summary()
    summary["stage_results"] = stage_results
    summary["recovery_log"] = recovery.get_recovery_log()
    summary["success"] = final_error is None and context.all_passed
    if final_error:
        summary["error"] = final_error

    return summary


def execute_cross_platform_scenario(scenario_id: str) -> Dict[str, Any]:
    sc = get_cross_platform_scenario(scenario_id)
    if not sc:
        return {"success": False, "error": "联动场景不存在"}

    plan = sc.get("plan")
    if not plan:
        return {"success": False, "error": "场景未关联 CrossEndPlan"}

    assertions = sc.get("cross_end_assertions", [])
    return execute_cross_end_plan(plan, cross_end_assertions=assertions)
