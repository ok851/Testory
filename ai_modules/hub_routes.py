# -*- coding: utf-8 -*-
"""AI Hub 薄路由层：供 app.py 注册 /api/ai/hub/*。"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple


def hub_heal_analyze_steps(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    from ai_modules.optimize.self_heal import analyze_steps_for_self_heal

    analysis = analyze_steps_for_self_heal(steps)
    return {"success": True, "analysis": analysis}


def hub_heal_diagnose_text(error_message: str, step_summary: str = "", url: str = "") -> Dict[str, Any]:
    from execution_diag_bundle import build_failure_bundle, classify_failure_with_llm, merge_bundle_and_draft

    bundle = build_failure_bundle(
        {"description": (step_summary or "")[:2000]},
        (error_message or "")[:8000],
        {
            "diagnostics": {"url": (url or "")[:500]},
            "domSignals": {},
            "recent_browser_events": [],
        },
    )
    draft, warns = classify_failure_with_llm(bundle, force=False)
    out = merge_bundle_and_draft(bundle, draft)
    out["warnings"] = warns
    return {"success": True, "diagnosis": out}


def _parse_design_request(request) -> Dict[str, Any]:
    """解析 design preview/save 的 JSON 或 multipart。"""
    requirements_text = ""
    extra_context = ""
    selected_model = ""
    file_warns: List[str] = []
    project_id = None
    platform_type = "web"
    project_name = ""
    base_url = ""

    if request.files and request.files.get("file"):
        f = request.files["file"]
        raw = f.read() or b""
        fname = (f.filename or "upload.txt").strip()
        from requirements_document_extract import extract_text_from_bytes

        requirements_text, file_warns = extract_text_from_bytes(fname, raw)
        requirements_text = (requirements_text or "").strip()
        extra_form_text = (request.form.get("requirements_text") or "").strip()
        if extra_form_text:
            requirements_text = (
                (requirements_text + "\n\n" + extra_form_text).strip()
                if requirements_text
                else extra_form_text
            )
        project_id = request.form.get("project_id")
        platform_type = (request.form.get("platform_type") or "web").strip().lower()
        project_name = (request.form.get("project_name") or "").strip()
        base_url = (request.form.get("base_url") or "").strip()
        selected_model = (request.form.get("model") or "").strip()
    else:
        data = request.get_json(silent=True) or {}
        requirements_text = (data.get("requirements_text") or data.get("text") or "").strip()
        extra_context = (data.get("extra_context") or "").strip()
        selected_model = (data.get("model") or "").strip()
        project_id = data.get("project_id")
        platform_type = (data.get("platform_type") or "web").strip().lower()
        project_name = (data.get("project_name") or "").strip()
        base_url = (data.get("base_url") or "").strip()

    return {
        "requirements_text": requirements_text,
        "extra_context": extra_context,
        "selected_model": selected_model,
        "file_warns": file_warns,
        "project_id": project_id,
        "platform_type": platform_type,
        "project_name": project_name,
        "base_url": base_url,
    }


def hub_design_preview(
    request,
    *,
    resolve_profile_fn,
    get_active_model_fn,
) -> Tuple[Dict[str, Any], int]:
    """POST /api/ai/hub/design/preview — 仅生成草案，不写库。"""
    parsed = _parse_design_request(request)
    if not parsed["requirements_text"]:
        return {"success": False, "error": "requirements_text 为空或未上传可解析文件"}, 400

    mid = parsed["selected_model"] or get_active_model_fn()
    profile, _legacy = resolve_profile_fn(mid)
    from ai_modules.generate.design_from_requirements import generate_design_drafts

    drafts, warns, err = generate_design_drafts(
        parsed["requirements_text"],
        profile,
        platform_type=parsed["platform_type"],
        base_url=parsed["base_url"],
        project_name=parsed["project_name"],
        extra_context=parsed["extra_context"],
    )
    all_warnings = list(parsed["file_warns"]) + list(warns)
    if err:
        return {"success": False, "error": err, "warnings": all_warnings}, 400

    return {
        "success": True,
        "drafts": drafts,
        "draft_count": len(drafts),
        "warnings": all_warnings,
        "platform_type": parsed["platform_type"],
    }, 200


def hub_design_save(
    request,
    *,
    db,
    user_id: int,
    check_project_access_fn,
) -> Tuple[Dict[str, Any], int]:
    """POST /api/ai/hub/design/save — 将选中草案落库。"""
    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")
    if not project_id:
        return {"success": False, "error": "project_id不能为空"}, 400

    if not check_project_access_fn(user_id, project_id, "editor"):
        return {"success": False, "error": "无权限在此项目创建用例"}, 403

    platform_type = (data.get("platform_type") or "web").strip().lower()
    drafts_in = data.get("drafts")
    if not isinstance(drafts_in, list) or not drafts_in:
        return {"success": False, "error": "drafts 不能为空"}, 400

    selected = data.get("selected_indices")
    if isinstance(selected, list) and selected:
        try:
            idx_set = {int(i) for i in selected}
            drafts = [drafts_in[i] for i in sorted(idx_set) if 0 <= i < len(drafts_in)]
        except (TypeError, ValueError):
            drafts = drafts_in
    else:
        drafts = drafts_in

    if not drafts:
        return {"success": False, "error": "未选中任何草案"}, 400

    batch_id = (data.get("batch_id") or "").strip() or uuid.uuid4().hex[:12]
    from ai_modules.generate.design_from_requirements import save_design_drafts_to_project

    out = save_design_drafts_to_project(
        db,
        project_id=int(project_id),
        platform_type=platform_type,
        drafts=drafts,
        user_id=user_id,
        batch_id=batch_id,
    )
    out["batch_id"] = batch_id
    return out, 200
