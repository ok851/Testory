# -*- coding: utf-8 -*-
"""从结构化场景批量生成 Android 移动端用例。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _scenario_goal(sc: Dict[str, Any]) -> Tuple[str, str, str, str, str, str]:
    sid = str(sc.get("id") or "").strip() or "auto"
    title = str(sc.get("title") or "").strip() or f"场景 {sid}"
    pre = sc.get("preconditions") or []
    hs = sc.get("high_level_steps") or []
    er = sc.get("expected_results") or []
    if isinstance(pre, str):
        pre = [pre]
    if isinstance(hs, str):
        hs = [hs]
    if isinstance(er, str):
        er = [er]
    pre_t = "\n".join(str(x) for x in pre if str(x).strip())
    hs_t = "\n".join(f"- {x}" for x in hs if str(x).strip())
    er_t = "\n".join(str(x) for x in er if str(x).strip())
    goal = (
        f"Android 移动端测试场景：{title}\n"
        f"场景标识：{sid}\n"
        + (f"前置条件：\n{pre_t}\n\n" if pre_t else "")
        + (f"步骤概要：\n{hs_t}\n\n" if hs_t else "")
        + (f"期望：\n{er_t}" if er_t else "")
    ).strip()
    desc = f"[REQ:{sid}] {title}"
    if len(desc) > 3900:
        desc = desc[:3897] + "..."
    return sid, title, goal, desc, pre_t, er_t


def batch_mobile_cases_from_scenarios(
    db: Any,
    *,
    project_id: int,
    scenarios: List[Dict[str, Any]],
    project_name: str = "",
    profile: Optional[Dict[str, Any]] = None,
    legacy_model: str = "",
    memory_context_fn=None,
    user_id: int = 0,
    max_scenarios: int = 15,
    fill_steps_fn=None,
    normalize_fn=None,
    step_to_db_kwargs_fn=None,
    audit_fn=None,
) -> Dict[str, Any]:
    """批量创建 UI 用例（automation_layer=android 步骤）。"""
    from ai_local_inference import local_ai_service

    created_ids: List[int] = []
    warnings: List[str] = []
    limits = None
    try:
        from license_manager import license_manager, LicenseType

        license_info = license_manager.get_current_license()
        limits = license_manager.get_limits()
    except Exception:
        license_info = None
        limits = {"max_cases_per_project": -1}

    for sc in scenarios[:max_scenarios]:
        if not isinstance(sc, dict):
            continue
        sid, title, goal, desc, pre_t, er_t = _scenario_goal(sc)
        if limits and limits.get("max_cases_per_project", -1) != -1:
            if db.get_project_case_count(project_id) >= limits["max_cases_per_project"]:
                warnings.append(f"已达项目用例上限，停止在 {len(created_ids)} 条")
                break

        mem_ctx = memory_context_fn(user_id, goal, probe_url="", project_name=project_name) if memory_context_fn else None
        try:
            generated = local_ai_service.generate_case_and_steps(
                goal,
                project_name,
                model=legacy_model,
                profile=profile,
                memory_context=mem_ctx or None,
                platform_type="android",
            )
        except ValueError as e:
            warnings.append(f"场景 {sid} 跳过：{e}")
            continue

        if license_info and getattr(license_info, "license_type", None) == LicenseType.FREE.value:
            db.increment_created_cases(user_id)

        steps = generated.get("steps") or []
        if fill_steps_fn:
            fill_steps_fn(steps, goal, "", None)
        if normalize_fn:
            generated, norm_warns = normalize_fn(generated)
            warnings.extend(norm_warns or [])

        case_id = db.create_test_case_v2(
            project_id,
            (title[:200] if title else f"AI-M-{sid}")[:200],
            generated.get("case_url", "") or "",
            desc,
            pre_t[:2000] if pre_t else generated.get("precondition", ""),
            er_t[:2000] if er_t else generated.get("expected_result", ""),
            case_type="ui",
        )
        for idx, step in enumerate(steps, start=1):
            st = dict(step)
            st["automation_layer"] = st.get("automation_layer") or "android"
            kw = step_to_db_kwargs_fn(st, case_id, idx) if step_to_db_kwargs_fn else {}
            if kw:
                db.create_test_step(**kw)
        if audit_fn:
            audit_fn({**generated, "scenario_id": sid, "platform": "android"})
        created_ids.append(case_id)

    return {
        "success": True,
        "created_case_ids": created_ids,
        "count": len(created_ids),
        "warnings": warnings,
        "platform": "android",
    }
