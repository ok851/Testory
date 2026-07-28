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
_STORE_LOCK = __import__("threading").RLock()


def _repo_root() -> Path:
    """仓库根目录：ai_modules/execute/orchestrator.py → parents[2]。"""
    return Path(__file__).resolve().parents[2]


def _store_path() -> Path:
    """场景持久化路径：优先 UAT_DATA_DIR，否则仓库内 data/（禁止落到仓库外）。"""
    env_dir = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env_dir:
        data_dir = Path(env_dir).expanduser().resolve()
    else:
        data_dir = (_repo_root() / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "cross_platform_scenarios.json"


def _evaluate_stage_risk_gate(
    stage: Dict[str, Any],
    *,
    plan: Optional[Dict[str, Any]] = None,
    user_id: str = "",
) -> Dict[str, Any]:
    """L2 RiskGuard 门禁：无审批不得执行。返回可并入 stage_result 的字典。"""
    from ai_modules.security.risk_guard import evaluate_stage_risk

    decision = evaluate_stage_risk(stage, plan=plan, user_id=user_id)
    payload: Dict[str, Any] = {
        "risk_level": decision.level,
        "risk_decision": decision.decision,
        "risk_events": list(decision.events or []),
    }
    if decision.approval_id:
        payload["risk_approval_id"] = decision.approval_id
    if decision.ok:
        payload["ok"] = True
        return payload
    payload["ok"] = False
    payload["error"] = decision.error or "RiskGuard 拒绝执行"
    payload["error_code"] = decision.error_code or "RISK_DENIED"
    return payload


def scenario_store_info() -> Dict[str, Any]:
    """供前端/运维查看场景文件落盘位置（不含敏感内容）。"""
    path = _store_path()
    env_set = bool((os.environ.get("UAT_DATA_DIR") or "").strip())
    return {
        "path": str(path),
        "data_dir": str(path.parent),
        "using_uat_data_dir": env_set,
        "exists": path.is_file(),
        "hint": (
            "当前使用环境变量 UAT_DATA_DIR 存储跨端场景，请纳入备份。"
            if env_set
            else "未设置 UAT_DATA_DIR 时，场景保存在项目 data/ 目录，建议定期备份该文件。"
        ),
    }


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


def normalize_scenario_id(scenario_id: Any) -> str:
    """统一为非空字符串 ID（禁止把 UUID 当成 int 路由）。"""
    sid = str(scenario_id or "").strip()
    return sid


def coerce_scenario_record(item: Any) -> Optional[Dict[str, Any]]:
    """读路径归一：始终提供 scenario_id / id / name / plan 等公开字段。"""
    if not isinstance(item, dict):
        return None
    out = dict(item)
    sid = normalize_scenario_id(out.get("scenario_id") or out.get("id"))
    if not sid:
        return None
    out["scenario_id"] = sid
    out["id"] = sid  # 前端兼容别名

    plan = out.get("plan")
    if isinstance(out.get("plan_json"), str) and not isinstance(plan, dict):
        try:
            plan = json.loads(out["plan_json"])
        except (json.JSONDecodeError, TypeError):
            plan = None
    if not isinstance(plan, dict):
        plan = {}
    out["plan"] = plan

    name = (
        out.get("name")
        or plan.get("scenario")
        or plan.get("name")
        or f"场景-{sid[:8]}"
    )
    out["name"] = str(name)

    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    out["stages"] = stages
    out["stage_count"] = len(stages)

    if not out.get("created_at"):
        out["created_at"] = out.get("updated_at") or ""
    return out


def list_cross_platform_scenarios() -> List[Dict[str, Any]]:
    with _STORE_LOCK:
        raw = _load_all()
    out: List[Dict[str, Any]] = []
    for item in raw:
        coerced = coerce_scenario_record(item)
        if coerced:
            out.append(coerced)
    return out


def get_cross_platform_scenario(scenario_id: str) -> Optional[Dict[str, Any]]:
    sid = normalize_scenario_id(scenario_id)
    if not sid:
        return None
    with _STORE_LOCK:
        for item in _load_all():
            coerced = coerce_scenario_record(item)
            if coerced and coerced["scenario_id"] == sid:
                return coerced
    return None


def save_cross_platform_scenario(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload or {})
    # 接受 scenario_id / id 任一；空则新生成 UUID 片段
    sid = normalize_scenario_id(data.get("scenario_id") or data.get("id"))
    if not sid:
        sid = str(uuid.uuid4())[:12]
    data["scenario_id"] = sid
    data["id"] = sid

    name_val = data.get("name")
    if name_val is None and isinstance(data.get("plan"), dict):
        name_val = data["plan"].get("scenario") or data["plan"].get("name")
    if name_val:
        data["name"] = str(name_val)
        if isinstance(data.get("plan"), dict):
            data["plan"].setdefault("scenario", str(name_val))
            data["plan"].setdefault("name", str(name_val))

    if not data.get("plan") and not data.get("web_case_id"):
        return {"success": False, "error": "至少指定 plan 或 web_case_id"}

    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    with _STORE_LOCK:
        items = _load_all()
        replaced = False
        for i, item in enumerate(items):
            prev = coerce_scenario_record(item) or {}
            if prev.get("scenario_id") == sid:
                if not data.get("created_at"):
                    data["created_at"] = prev.get("created_at") or now
                data["updated_at"] = now
                items[i] = data
                replaced = True
                break
        if not replaced:
            data.setdefault("created_at", now)
            data["updated_at"] = now
            items.append(data)
        _save_all(items)

    coerced = coerce_scenario_record(data) or data
    return {"success": True, "scenario": coerced, "scenario_id": sid}


def delete_cross_platform_scenario(scenario_id: str) -> Dict[str, Any]:
    sid = normalize_scenario_id(scenario_id)
    if not sid:
        return {"success": False, "error": "scenario_id 不能为空"}
    with _STORE_LOCK:
        items = _load_all()
        new_items = []
        found = False
        for x in items:
            prev = coerce_scenario_record(x)
            if prev and prev["scenario_id"] == sid:
                found = True
                continue
            new_items.append(x)
        if not found:
            return {"success": False, "error": "场景不存在"}
        _save_all(new_items)
    return {"success": True, "scenario_id": sid}


def _execute_api_stage(
    stage: Dict[str, Any], context: CrossEndContext
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    adapter = ApiSkillAdapter()
    resolved_stage = context.resolve_deep(dict(stage))
    result, extracted = adapter.execute(resolved_stage, dict(context._variables))
    return result, extracted


def _stage_requests_hermes(
    stage: Dict[str, Any],
    plan: Optional[Dict[str, Any]] = None,
) -> bool:
    """显式 opt-in 才走 Hermes；默认 classic skill runner（X7 防静默假接入）。"""
    if not isinstance(stage, dict):
        return False
    if stage.get("use_hermes") is False:
        return False
    exe = str(stage.get("executor") or stage.get("runner") or "").strip().lower()
    if exe in ("hermes", "agent", "ai"):
        return True
    if stage.get("use_hermes") is True:
        return True
    if isinstance(plan, dict):
        default_exe = str(
            plan.get("default_ui_executor") or plan.get("ui_executor") or ""
        ).strip().lower()
        if default_exe in ("hermes", "agent", "ai"):
            return True
    return False


def _execute_ui_stage(
    stage: Dict[str, Any],
    context: CrossEndContext,
    *,
    plan: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """执行 Web/Mobile/Desktop UI 阶段。默认失败，全部步骤显式成功才 ok_assert=True。

    支持 vars_to_store / extract / 步骤 store_as；必选变量抽不到则阶段失败。
    当 stage.executor=hermes / use_hermes / plan.default_ui_executor=hermes 时走 Hermes（不可用则失败，不静默回退）。
    """
    from ai_modules.plan.var_extraction import (
        collect_extraction_rules,
        extract_web_variables,
        merge_step_extractions,
        validate_required_extractions,
    )

    layer = (stage.get("layer") or "").strip().lower()
    resolved_stage = context.resolve_deep(dict(stage))
    steps = resolved_stage.get("steps", [])
    extracted: Dict[str, Any] = {}
    rules = collect_extraction_rules(resolved_stage)
    wants_hermes = _stage_requests_hermes(resolved_stage, plan)

    result: Dict[str, Any] = {
        "ok_assert": False,
        "error": None,
        "elapsed_ms": 0,
        "stage_id": stage.get("id", ""),
        "layer": layer,
        "executor": "hermes" if wants_hermes else "classic",
        "steps_executed": 0,
        "steps_planned": len(steps) if isinstance(steps, list) else 0,
    }

    if not isinstance(steps, list):
        steps = []

    has_nl = bool(
        (resolved_stage.get("description") or resolved_stage.get("step") or "").strip()
        or (isinstance(resolved_stage.get("action"), dict) and resolved_stage.get("action"))
    )

    # 允许「仅抽取」Web 阶段；Hermes 可用自然语言描述代替 steps
    if not steps and not rules and not (wants_hermes and has_nl):
        result["error"] = "UI stage has no steps defined"
        result["error_code"] = "NO_STEPS"
        return result, extracted
    if not steps and layer != "web" and not (wants_hermes and has_nl):
        result["error"] = "UI stage has no steps defined"
        result["error_code"] = "NO_STEPS"
        return result, extracted

    if layer not in ("web", "mobile", "desktop", "android"):
        result["error"] = f"不支持的 UI layer: {layer or '(empty)'}"
        result["error_code"] = "UNSUPPORTED_LAYER"
        return result, extracted

    t0 = time.perf_counter()
    executed = 0
    step_results: List[Dict[str, Any]] = []

    try:
        if wants_hermes:
            from ai_modules.execute.hermes_stage_executor import (
                hermes_execute_available,
                hermes_execute_stage,
            )

            plat = "mobile" if layer in ("mobile", "android") else layer
            if not hermes_execute_available(plat):
                result["error"] = (
                    "本阶段指定了 Hermes 执行器，但 Gateway 未配置或健康检查未通过，"
                    "不会静默改用经典步骤执行"
                )
                result["error_code"] = "HERMES_UNAVAILABLE"
                result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                try:
                    from ai_modules.plan.user_facing_errors import enrich_result_with_user_hint
                    enrich_result_with_user_hint(result)
                except Exception:
                    pass
                return result, extracted

            h_result, h_extracted = hermes_execute_stage(resolved_stage, context, plat)
            if isinstance(h_result, dict):
                result.update(h_result)
            result["executor"] = "hermes"
            result["stage_id"] = stage.get("id", "")
            result["layer"] = layer
            if isinstance(h_extracted, dict):
                extracted.update(h_extracted)
            if rules:
                missing = validate_required_extractions(rules, extracted)
                if missing:
                    result["ok_assert"] = False
                    result["error"] = (
                        f"Hermes 阶段变量抽取失败（必选缺失）: {', '.join(missing)}"
                    )
                    result["error_code"] = "VAR_EXTRACT_MISSING"
            result["steps_executed"] = result.get("steps_executed") or (
                len(steps) if result.get("ok_assert") else 0
            )
            result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            result["extracted"] = dict(extracted)
            if not result.get("error_code") and result.get("ok_assert") is False:
                result["error_code"] = result.get("error_code") or "HERMES_FAILED"
            try:
                from ai_modules.plan.user_facing_errors import enrich_result_with_user_hint
                enrich_result_with_user_hint(result)
            except Exception:
                pass
            return result, extracted

        if layer == "web":
            from browser_manager import get_page
            from .web_runner import execute_single_web_step

            page = get_page()
            if page is None:
                result["error"] = "无可用浏览器页面（browser_manager.get_page() 为空），Web 阶段不得跳过当绿"
                result["error_code"] = "NO_BROWSER_PAGE"
                result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                return result, extracted

            steps_ok = True
            skipped_count = 0
            for step in steps:
                if not isinstance(step, dict):
                    result["error"] = "Web 步骤格式无效（非 dict）"
                    result["error_code"] = "INVALID_STEP"
                    steps_ok = False
                    break
                step_result = execute_single_web_step(step, page)
                step_results.append(step_result if isinstance(step_result, dict) else {})
                executed += 1
                if step_result.get("skipped"):
                    skipped_count += 1
                    continue
                if not step_result.get("ok"):
                    result["error"] = step_result.get("error") or "Web 步骤执行失败"
                    result["error_code"] = step_result.get("error_code") or "WEB_STEP_FAILED"
                    result["failed_action"] = step_result.get("action") or step.get("action")
                    steps_ok = False
                    break

            if steps_ok:
                # 全部为 allow_skip 跳过且无变量抽取 → 不得当绿（Y3）
                if (
                    steps
                    and skipped_count == len(step_results)
                    and skipped_count > 0
                    and not rules
                    and not bool(stage.get("allow_skip"))
                ):
                    result["error"] = "Web 阶段全部步骤被跳过，未实际执行，不得当绿"
                    result["error_code"] = "ALL_STEPS_SKIPPED"
                    result["ok_assert"] = False
                else:
                    from_steps = merge_step_extractions(step_results, steps)
                    extracted.update(from_steps)
                    if rules:
                        page_vars, missing = extract_web_variables(page, rules)
                        # 步骤已写入的变量优先生效；补齐其余
                        for k, v in page_vars.items():
                            if k not in extracted:
                                extracted[k] = v
                        missing = validate_required_extractions(rules, extracted)
                        if missing:
                            result["error"] = f"变量抽取失败（必选缺失）: {', '.join(missing)}"
                            result["error_code"] = "VAR_EXTRACT_MISSING"
                            result["ok_assert"] = False
                        else:
                            result["ok_assert"] = True
                    else:
                        result["ok_assert"] = True

        elif layer in ("mobile", "android"):
            from mobile_executor import get_mobile_executor
            from mobile_automation import validate_mobile_step_result

            executor = get_mobile_executor()
            step_results = executor.execute_steps(steps)
            if not isinstance(step_results, list):
                result["error"] = "移动端 execute_steps 返回无效结果"
            elif not step_results and steps:
                result["error"] = "移动端未执行任何步骤"
            else:
                for i, step_res in enumerate(step_results):
                    executed += 1
                    action = ""
                    if i < len(steps) and isinstance(steps[i], dict):
                        action = str(steps[i].get("action") or "")
                    try:
                        validate_mobile_step_result(step_res, action or (step_res or {}).get("action") or "")
                    except Exception as ve:
                        result["error"] = str(ve)
                        result["failed_action"] = action
                        break
                else:
                    if len(step_results) < len(steps):
                        last = step_results[-1] if step_results else {}
                        result["error"] = (last or {}).get("error") or (
                            f"移动端仅完成 {len(step_results)}/{len(steps)} 步"
                        )
                    else:
                        extracted.update(merge_step_extractions(step_results, steps))
                        if rules:
                            missing = validate_required_extractions(rules, extracted)
                            if missing:
                                result["error"] = (
                                    f"移动端变量抽取失败（必选缺失，需步骤 store_as 或可抽取结果）: "
                                    f"{', '.join(missing)}"
                                )
                                result["error_code"] = "VAR_EXTRACT_MISSING"
                            else:
                                result["ok_assert"] = True
                        else:
                            result["ok_assert"] = True

        elif layer == "desktop":
            from step_executor import validate_desktop_step_result
            from ai_modules.execute.desktop_preflight import check_desktop_preflight
            from ai_modules.optimize.desktop_runtime_heal import (
                run_desktop_step_with_optional_heal,
            )

            pre = check_desktop_preflight()
            result["desktop_preflight"] = {
                "ok": bool(pre.get("ok")),
                "mode": pre.get("mode"),
                "detail": pre.get("detail"),
            }
            if pre.get("farm_dispatch") is not None:
                result["desktop_preflight"]["farm_dispatch"] = pre.get("farm_dispatch")
            if not pre.get("ok"):
                result["error"] = pre.get("error") or "桌面会话不可用"
                result["error_code"] = pre.get("error_code") or "DESKTOP_NO_SESSION"
                result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                try:
                    from ai_modules.plan.user_facing_errors import enrich_result_with_user_hint

                    enrich_result_with_user_hint(result)
                except Exception:
                    pass
                return result, extracted

            heal_events = []
            for step in steps:
                if not isinstance(step, dict):
                    result["error"] = "Desktop 步骤格式无效（非 dict）"
                    break
                action = str(step.get("action") or "")
                desk, heal_meta = run_desktop_step_with_optional_heal(step)
                step_results.append(desk if isinstance(desk, dict) else {})
                executed += 1
                if isinstance(heal_meta, dict) and heal_meta.get("heal_attempted"):
                    heal_events.append({
                        "action": action,
                        **{k: heal_meta.get(k) for k in (
                            "heal_succeeded", "strategies", "reason", "proposal"
                        )},
                    })
                    result.setdefault("evidence", [])
                    if isinstance(result["evidence"], list):
                        result["evidence"].append({
                            "type": "desktop_heal",
                            "action": action,
                            "succeeded": bool(heal_meta.get("heal_succeeded")),
                            "strategies": heal_meta.get("strategies") or [],
                        })
                try:
                    validate_desktop_step_result(desk, action)
                except Exception as ve:
                    result["error"] = str(ve)
                    result["error_code"] = "DESKTOP_STEP_FAILED"
                    result["failed_action"] = action
                    if heal_events:
                        result["desktop_heal_events"] = heal_events
                    break
                st = str((desk or {}).get("status") or "").strip().lower()
                if st not in ("success", "ok", "passed"):
                    result["error"] = (
                        (desk or {}).get("error")
                        or (desk or {}).get("warning")
                        or f"桌面步骤 status={st!r} 不得当绿"
                    )
                    result["error_code"] = "DESKTOP_SOFT_FAIL"
                    result["failed_action"] = action
                    if heal_events:
                        result["desktop_heal_events"] = heal_events
                    break
                # 桌面步骤可带回 extracted_text / store_as
                if isinstance(desk, dict) and desk.get("extracted_text") is not None:
                    sa = (desk.get("store_as") or step.get("store_as") or "").strip()
                    if sa:
                        extracted[sa] = desk.get("extracted_text")
            else:
                extracted.update(merge_step_extractions(step_results, steps))
                if heal_events:
                    result["desktop_heal_events"] = heal_events
                if rules:
                    missing = validate_required_extractions(rules, extracted)
                    if missing:
                        result["error"] = (
                            f"桌面变量抽取失败（必选缺失）: {', '.join(missing)}"
                        )
                        result["error_code"] = "VAR_EXTRACT_MISSING"
                    else:
                        result["ok_assert"] = True
                else:
                    result["ok_assert"] = True

    except Exception as e:
        result["ok_assert"] = False
        result["error"] = str(e)

    result["steps_executed"] = executed
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    result["extracted"] = dict(extracted)
    if result["ok_assert"] and executed <= 0 and not (layer == "web" and rules):
        result["ok_assert"] = False
        result["error"] = result.get("error") or "UI 阶段未执行任何步骤，不得当绿"
    try:
        from ai_modules.plan.user_facing_errors import enrich_result_with_user_hint

        enrich_result_with_user_hint(result)
    except Exception:
        pass
    return result, extracted


def _stage_requests_hitl(stage: Dict[str, Any], layer: str) -> bool:
    """判断阶段是否需要 HITL（专用层或显式 hitl/wait_for_human，显式 false 除外）。"""
    if layer in ("hitl", "human"):
        return True

    def _truthy_flag(val: Any) -> bool:
        if val is False or val is None:
            return False
        if isinstance(val, (int, float)) and val == 0:
            return False
        if isinstance(val, str) and val.strip().lower() in ("", "0", "false", "no", "n", "off"):
            return False
        # True / 非空 dict / 非空字符串 prompt 均视为需要
        return True

    if "hitl" in stage:
        return _truthy_flag(stage.get("hitl"))
    if "wait_for_human" in stage:
        return _truthy_flag(stage.get("wait_for_human"))
    return False


def _parse_hitl_stage_config(stage: Dict[str, Any]) -> Tuple[str, float, str]:
    """从 stage 解析 HITL prompt / timeout / hint。"""
    hitl_cfg = stage.get("hitl") if "hitl" in stage else stage.get("wait_for_human")
    prompt = (
        stage.get("prompt")
        or stage.get("description")
        or stage.get("label")
        or "等待人工确认"
    )
    hint = str(stage.get("hint") or "")
    timeout_s = stage.get("timeout_s", stage.get("timeout", 300))
    if isinstance(hitl_cfg, dict):
        prompt = hitl_cfg.get("prompt") or hitl_cfg.get("reason") or prompt
        hint = str(hitl_cfg.get("hint") or hint)
        if hitl_cfg.get("timeout_s") is not None:
            timeout_s = hitl_cfg.get("timeout_s")
        elif hitl_cfg.get("timeout") is not None:
            timeout_s = hitl_cfg.get("timeout")
    elif isinstance(hitl_cfg, str) and hitl_cfg.strip() and hitl_cfg.strip().lower() not in (
        "1",
        "true",
        "yes",
        "y",
    ):
        prompt = hitl_cfg.strip()
    try:
        timeout_f = float(timeout_s)
    except (TypeError, ValueError):
        timeout_f = 300.0
    return str(prompt), timeout_f, hint


def _execute_hitl_stage(
    stage: Dict[str, Any],
    sync_mgr: SyncPointManager,
    *,
    user_id: str = "",
    plan_id: str = "",
    as_dedicated_layer: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """执行 HITL 阻塞门禁。超时/取消 → ok_assert=False。事件写入 hitl_events 供 Trace。"""
    from agent_hitl import get_hitl_events, hitl_outcome_from_events

    prompt, timeout_s, hint = _parse_hitl_stage_config(stage)
    stage_id = stage.get("id") or "hitl"
    gate_id = (
        str(stage.get("gate_id") or "").strip()
        or f"cross_end:{plan_id or 'plan'}:{stage_id}"
    )
    t0 = time.perf_counter()
    since_ts = time.time()
    result: Dict[str, Any] = {
        "ok_assert": False,
        "error": None,
        "elapsed_ms": 0,
        "stage_id": stage_id,
        "layer": "hitl",
        "hitl_gate_id": gate_id,
        "hitl_prompt": prompt,
    }
    try:
        ok = sync_mgr.wait_for_human(
            prompt,
            timeout_s=timeout_s,
            gate_id=gate_id,
            user_id=user_id,
            hint=hint,
            poll_interval_s=float(stage.get("poll_interval_s") or 0.2),
        )
        result["ok_assert"] = bool(ok)
        if not ok:
            result["error"] = f"HITL 超时或已取消（timeout_s={timeout_s}）"
            result["error_code"] = "HITL_TIMEOUT_OR_CANCEL"
    except Exception as e:
        result["ok_assert"] = False
        result["error"] = str(e)
        result["error_code"] = "HITL_ERROR"
    events = get_hitl_events(gate_id=gate_id, since_ts=since_ts - 0.05, limit=100)
    outcome = hitl_outcome_from_events(events)
    if result.get("ok_assert") and outcome == "unknown":
        outcome = "resumed"
    if (not result.get("ok_assert")) and outcome == "unknown":
        outcome = "timeout_or_cancel"
    result["hitl_events"] = events
    result["hitl_outcome"] = outcome
    if outcome == "timed_out":
        result["error_code"] = "HITL_TIMEOUT"
        if not result.get("ok_assert"):
            result["error"] = f"HITL 超时（timeout_s={timeout_s}）"
    elif outcome == "cancelled":
        result["error_code"] = "HITL_CANCELLED"
        if not result.get("ok_assert"):
            result["error"] = "HITL 已取消"
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if not as_dedicated_layer:
        result["hitl_pregate"] = True
    return result, {}


def execute_cross_end_plan(
    plan: Dict[str, Any],
    *,
    cross_end_assertions: Optional[List[Dict[str, Any]]] = None,
    progress_callback: Optional[Callable] = None,
    user_id: str = "",
    acquire_lock: bool = True,
    lock_timeout_sec: float = 120.0,
    project_id: Any = None,
    record_history: bool = True,
    trigger_source: str = "ui",
) -> Dict[str, Any]:
    """执行跨端计划。默认获取本机 execution_lock；忙则失败，ImportError 不得绕过。

    record_history=True 时诚实写入 run_history（test_type=cross_end）与文件审计。
    """
    plan_id = (plan or {}).get("plan_id") or str(uuid.uuid4())[:12]
    uid = str(user_id or (plan or {}).get("user_id") or "").strip()
    owner = f"cross_end:{plan_id}:user:{uid or 'anon'}"
    pid = project_id if project_id is not None else (plan or {}).get("project_id")

    def _audit(out: Dict[str, Any]) -> Dict[str, Any]:
        if not record_history or not isinstance(out, dict):
            return out
        try:
            from ai_modules.execute.cross_end_run_audit import record_cross_end_execution

            record_cross_end_execution(
                out,
                plan=plan if isinstance(plan, dict) else {},
                test_type="cross_end",
                user_id=uid,
                project_id=pid,
                trigger_source=trigger_source,
            )
        except Exception:
            # 审计失败不得改变业务结论，也不得假绿
            out.setdefault("audit_error", "run_history 写入失败")
        return out

    def _run() -> Dict[str, Any]:
        return _execute_cross_end_plan_impl(
            plan,
            cross_end_assertions=cross_end_assertions,
            progress_callback=progress_callback,
            user_id=uid,
        )

    if not acquire_lock:
        out = _run()
        out["lock"] = "skipped"
        return _audit(out)

    try:
        from execution_lock import ExecutionLockError, execution_guard
    except ImportError:
        return _audit({
            "success": False,
            "error": "execution_lock 模块不可用，拒绝跨端执行（防无锁假跑）",
            "error_code": "EXECUTION_LOCK_UNAVAILABLE",
            "lock": "unavailable",
            "plan_id": plan_id,
            "stage_results": [],
        })

    try:
        timeout = float(lock_timeout_sec)
    except (TypeError, ValueError):
        timeout = 120.0
    if timeout < 0:
        timeout = 0.0

    try:
        with execution_guard(owner=owner, timeout_sec=timeout, required=True) as held:
            out = _run()
            out["lock"] = "held" if held else "missed"
            out["lock_owner"] = owner
            return _audit(out)
    except ExecutionLockError as e:
        return _audit({
            "success": False,
            "error": str(e) or "本机已有自动化任务在执行，请稍后再试。",
            "error_code": "EXECUTION_LOCK_BUSY",
            "lock": "busy",
            "lock_owner": owner,
            "plan_id": plan_id,
            "stage_results": [],
        })


def _execute_cross_end_plan_impl(
    plan: Dict[str, Any],
    *,
    cross_end_assertions: Optional[List[Dict[str, Any]]] = None,
    progress_callback: Optional[Callable] = None,
    user_id: str = "",
) -> Dict[str, Any]:

    plan_id = plan.get("plan_id", str(uuid.uuid4())[:12])
    scenario = plan.get("scenario", "未命名场景")
    stages = plan.get("stages", [])

    if not stages:
        return {"success": False, "error": "Plan has no stages"}

    context = CrossEndContext(plan_id=plan_id, scenario=scenario)
    # 企业样例 / 计划预置：种子变量（如 api_order_id），供 Desktop 核对
    seed = plan.get("variables") or plan.get("initial_variables") or {}
    if isinstance(seed, dict):
        for k, v in seed.items():
            key = str(k or "").strip()
            if key:
                context.set_variable(key, v)
    sync_mgr = SyncPointManager(context)
    sync_mgr.set_plan_stages(stages)
    recovery = RecoveryEngine()
    uid = str(user_id or plan.get("user_id") or "").strip()

    context.mark_start()

    stage_results: List[Dict[str, Any]] = []
    cleanup_stages: List[Dict[str, Any]] = []
    normal_stages: List[Dict[str, Any]] = []
    final_error: Optional[str] = None
    skipped_failure_stages: List[str] = []
    # 显式策略：仅当 plan.allow_skipped_failures=true 时，RECOVERY_SKIP 可不挡总成功
    allow_skipped_failures = bool(
        plan.get("allow_skipped_failures")
        or plan.get("allow_recovery_skip_success")
    )

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if stage.get("cleanup"):
            cleanup_stages.append(stage)
        else:
            normal_stages.append(stage)

    for stage in normal_stages:
        stage_id = stage.get("id", "unknown")
        layer = (stage.get("layer") or "api").strip().lower()
        depends_on = stage.get("depends_on", [])

        if not sync_mgr.acquire(stage_id, depends_on):
            result = {
                "stage_id": stage_id,
                "ok_assert": False,
                "error": f"依赖同步点未满足: {depends_on}",
                "error_code": "DEPENDS_ON_UNSATISFIED",
                "elapsed_ms": 0,
            }
            stage_results.append(result)
            context.record_stage_result(stage_id, result)
            final_error = result["error"]
            break

        # Y1: vars_to_read / wait_for / data_sync / api_state_sync / state_sync / time_sync
        sync_gate = sync_mgr.run_pre_stage_syncs(stage)
        if not sync_gate.get("ok"):
            result = {
                "stage_id": stage_id,
                "ok_assert": False,
                "error": sync_gate.get("error") or "同步门禁失败",
                "error_code": sync_gate.get("error_code") or "SYNC_FAILED",
                "syncs": sync_gate.get("syncs") or [],
                "elapsed_ms": 0,
            }
            stage_results.append(result)
            context.record_stage_result(stage_id, result)
            final_error = result["error"]
            break

        # R10: RiskGuard L0/L1/L2 — L2 无令牌不得执行
        risk_gate = _evaluate_stage_risk_gate(stage, plan=plan, user_id=uid)
        if not risk_gate.get("ok"):
            result = {
                "stage_id": stage_id,
                "layer": layer,
                "ok_assert": False,
                "error": risk_gate.get("error"),
                "error_code": risk_gate.get("error_code"),
                "risk_level": risk_gate.get("risk_level"),
                "risk_decision": risk_gate.get("risk_decision"),
                "risk_approval_id": risk_gate.get("risk_approval_id"),
                "risk_events": risk_gate.get("risk_events") or [],
                "elapsed_ms": 0,
            }
            stage_results.append(result)
            context.record_stage_result(stage_id, result)
            final_error = result["error"]
            break

        # 阶段级 HITL 预门禁：hitl: true | {prompt, timeout_s} | layer=hitl/human
        need_hitl_stage = _stage_requests_hitl(stage, layer)
        if need_hitl_stage:
            result, extracted = _execute_hitl_stage(
                stage,
                sync_mgr,
                user_id=uid,
                plan_id=plan_id,
                as_dedicated_layer=layer in ("hitl", "human"),
            )
            # 若仅为预门禁且通过，继续执行原 layer；专用 hitl 层则本阶段结束
            if layer in ("hitl", "human") or not result.get("ok_assert"):
                result["stage_id"] = stage_id
                result["layer"] = layer if layer in ("hitl", "human") else (result.get("layer") or layer)
                result["risk_level"] = risk_gate.get("risk_level")
                result["risk_decision"] = risk_gate.get("risk_decision")
                stage_results.append(result)
                context.record_stage_result(stage_id, result, extracted)
                if progress_callback:
                    try:
                        progress_callback(stage_id, result.get("ok_assert", False))
                    except Exception:
                        pass
                if not result.get("ok_assert"):
                    final_error = result.get("error") or "HITL 未通过"
                    break
                if layer in ("hitl", "human"):
                    continue
            # 预门禁通过且非专用层：落入下方正常执行

        if layer == "api":
            result, extracted = _execute_api_stage(stage, context)
        elif layer in ("hitl", "human"):
            # 已在上方处理
            continue
        else:
            result, extracted = _execute_ui_stage(stage, context, plan=plan)

        result["stage_id"] = stage_id
        result["layer"] = layer
        result["risk_level"] = risk_gate.get("risk_level")
        result["risk_decision"] = risk_gate.get("risk_decision")
        if risk_gate.get("risk_approval_id"):
            result["risk_approval_id"] = risk_gate.get("risk_approval_id")
        if risk_gate.get("risk_events"):
            result["risk_events"] = risk_gate.get("risk_events")
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
                if layer == "api":
                    result, extracted = _execute_api_stage(stage, context)
                else:
                    result, extracted = _execute_ui_stage(stage, context, plan=plan)
                result["stage_id"] = stage_id
                result["layer"] = layer
                result["risk_level"] = risk_gate.get("risk_level")
                result["risk_decision"] = risk_gate.get("risk_decision")
                context.record_stage_result(stage_id, result, extracted)
                # 同步刷新 stage_results 末条
                if stage_results and stage_results[-1].get("stage_id") == stage_id:
                    stage_results[-1] = result
                else:
                    stage_results.append(result)
                if result.get("ok_assert"):
                    break
                action = recovery.decide(stage_id, result.get("error", ""), on_failure)
            if not result.get("ok_assert"):
                if action == RECOVERY_SKIP:
                    result["recovery_action"] = RECOVERY_SKIP
                    result["skipped_failure"] = True
                    result["error_code"] = result.get("error_code") or "RECOVERY_SKIP"
                    if stage_results and stage_results[-1].get("stage_id") == stage_id:
                        stage_results[-1] = result
                    context.record_stage_result(stage_id, result, extracted)
                    skipped_failure_stages.append(stage_id)
                    # 不设 final_error，继续后续阶段；总成功由门禁判定
                    continue
                final_error = result.get("error")
                result["recovery_action"] = RECOVERY_ABORT
                if stage_results and stage_results[-1].get("stage_id") == stage_id:
                    stage_results[-1] = result
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
            # R10: cleanup 同样走 RiskGuard（显式 L2 无令牌则跳过执行并记失败）
            risk_gate = _evaluate_stage_risk_gate(cleanup_stage, plan=plan, user_id=uid)
            if not risk_gate.get("ok"):
                stage_results.append({
                    "stage_id": stage_id,
                    "ok_assert": False,
                    "error": risk_gate.get("error"),
                    "error_code": risk_gate.get("error_code"),
                    "layer": cleanup_layer,
                    "cleanup": True,
                    "risk_level": risk_gate.get("risk_level"),
                    "risk_decision": risk_gate.get("risk_decision"),
                    "risk_approval_id": risk_gate.get("risk_approval_id"),
                    "risk_events": risk_gate.get("risk_events") or [],
                })
                context.record_stage_result(stage_id, stage_results[-1])
                continue
            # 清理阶段按 layer 分派：API / Web / Mobile / Desktop
            if cleanup_layer == "api":
                result, _ = _execute_api_stage(cleanup_stage, context)
            else:
                result, _ = _execute_ui_stage(cleanup_stage, context, plan=plan)
            result["stage_id"] = stage_id
            result["layer"] = cleanup_layer
            result["cleanup"] = True
            result["risk_level"] = risk_gate.get("risk_level")
            result["risk_decision"] = risk_gate.get("risk_decision")
            if risk_gate.get("risk_approval_id"):
                result["risk_approval_id"] = risk_gate.get("risk_approval_id")
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

    # 断言来源：显式参数 > plan.cross_end_assertions > plan.assertions
    assertion_rules: List[Dict[str, Any]] = []
    if isinstance(cross_end_assertions, list) and cross_end_assertions:
        assertion_rules = [r for r in cross_end_assertions if isinstance(r, dict)]
    else:
        for key in ("cross_end_assertions", "assertions"):
            raw = plan.get(key)
            if isinstance(raw, list) and raw:
                assertion_rules = [r for r in raw if isinstance(r, dict)]
                break

    assertion_passed = 0
    assertion_failed = 0
    assertion_details: List[Dict[str, Any]] = []
    if assertion_rules:
        assertion_passed, assertion_failed, assertion_details = run_cross_end_assertions(
            context, assertion_rules
        )

    context.mark_finish()

    recovery_log = recovery.get_recovery_log()
    skipped_ids = list(dict.fromkeys(skipped_failure_stages + recovery.skipped_stage_ids()))

    # 门禁：默认跳过失败不得当绿；cleanup 失败不挡（best-effort）；需显式 allow_skipped_failures
    passed_gate = context.evaluate_pass(
        ignore_skipped_failures=allow_skipped_failures,
        ignore_cleanup_failures=True,
    )
    success = final_error is None and passed_gate
    if success and skipped_ids and not allow_skipped_failures:
        # 双保险：即便 evaluate 漏标，有 skip 列表也不得绿
        success = False
    if assertion_failed > 0:
        success = False
        if not final_error:
            final_error = f"跨端断言失败 {assertion_failed} 条"

    summary = context.summary()
    summary["stage_results"] = stage_results
    summary["recovery_log"] = recovery_log
    summary["skipped_failure_stages"] = skipped_ids
    summary["allow_skipped_failures"] = allow_skipped_failures
    summary["gate_passed"] = passed_gate and assertion_failed == 0
    summary["success"] = success
    summary["assertion_passed"] = assertion_passed
    summary["assertion_failed"] = assertion_failed
    summary["assertion_details"] = assertion_details
    if final_error:
        summary["error"] = final_error
        # 透传首个失败阶段的 error_code，便于前端友好提示
        if not summary.get("error_code"):
            if assertion_failed > 0:
                summary["error_code"] = "CROSS_END_ASSERT_FAILED"
            else:
                for sr in stage_results:
                    if isinstance(sr, dict) and sr.get("ok_assert") is False and sr.get("error_code"):
                        summary["error_code"] = sr.get("error_code")
                        break
    elif assertion_failed > 0:
        summary["error"] = f"跨端断言失败 {assertion_failed} 条"
        summary["error_code"] = "CROSS_END_ASSERT_FAILED"
    elif skipped_ids and not allow_skipped_failures and not success:
        summary["error"] = (
            "存在 RECOVERY_SKIP 跳过的失败阶段，默认不得当成功: "
            + ", ".join(skipped_ids)
        )
        summary["error_code"] = "RECOVERY_SKIP_BLOCKS_SUCCESS"

    try:
        from ai_modules.plan.user_facing_errors import enrich_result_with_user_hint

        enrich_result_with_user_hint(summary)
    except Exception:
        pass
    try:
        from ai_modules.execute.result_schema import normalize_cross_end_result

        summary = normalize_cross_end_result(summary)
    except Exception:
        pass
    return summary


def execute_cross_platform_scenario(
    scenario_id: str,
    *,
    user_id: str = "",
    acquire_lock: bool = True,
    lock_timeout_sec: float = 120.0,
) -> Dict[str, Any]:
    sc = get_cross_platform_scenario(scenario_id)
    if not sc:
        return {"success": False, "error": "联动场景不存在"}

    plan = sc.get("plan")
    if not plan:
        return {"success": False, "error": "场景未关联 CrossEndPlan"}

    assertions = (
        sc.get("cross_end_assertions")
        or (plan.get("cross_end_assertions") if isinstance(plan, dict) else None)
        or (plan.get("assertions") if isinstance(plan, dict) else None)
        or []
    )
    return execute_cross_end_plan(
        plan,
        cross_end_assertions=assertions if isinstance(assertions, list) else [],
        user_id=user_id,
        acquire_lock=acquire_lock,
        lock_timeout_sec=lock_timeout_sec,
    )
