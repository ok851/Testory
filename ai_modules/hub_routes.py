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
    from modules.execution.execution_diag_bundle import build_failure_bundle, classify_failure_with_llm, merge_bundle_and_draft

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
    entry_target = ""
    upload_filename = ""
    input_kind = ""
    file_snippets = None

    if request.files and request.files.get("file"):
        f = request.files["file"]
        raw = f.read() or b""
        fname = (f.filename or "upload.txt").strip()
        upload_filename = fname
        from modules.integration.requirements_document_extract import extract_text_from_bytes

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
        entry_target = (
            request.form.get("entry_target")
            or request.form.get("app_entry")
            or request.form.get("api_base")
            or base_url
            or ""
        ).strip()
        selected_model = (request.form.get("model") or "").strip()
        input_kind = (request.form.get("input_kind") or "").strip()
    else:
        data = request.get_json(silent=True) or {}
        requirements_text = (data.get("requirements_text") or data.get("text") or "").strip()
        extra_context = (data.get("extra_context") or "").strip()
        selected_model = (data.get("model") or "").strip()
        project_id = data.get("project_id")
        platform_type = (data.get("platform_type") or "web").strip().lower()
        project_name = (data.get("project_name") or "").strip()
        base_url = (data.get("base_url") or "").strip()
        entry_target = (
            data.get("entry_target")
            or data.get("app_entry")
            or data.get("api_base")
            or base_url
            or ""
        ).strip()
        upload_filename = str(data.get("filename") or data.get("source_filename") or "").strip()
        input_kind = str(data.get("input_kind") or "").strip()
        snippets = data.get("file_snippets") if isinstance(data.get("file_snippets"), dict) else None
        if snippets:
            file_snippets = snippets
            if not requirements_text:
                parts = []
                for p, c in list(snippets.items())[:20]:
                    parts.append(f"// file: {p}\n{c}")
                requirements_text = "\n\n".join(parts)
            if not upload_filename:
                upload_filename = next(iter(snippets.keys()), "src/App.tsx")

    from ai_modules.generate.input_classify import classify_design_input, normalize_platform

    platform_type = normalize_platform(platform_type)
    kind = classify_design_input(
        filename=upload_filename,
        text=requirements_text,
        explicit_kind=input_kind,
    )

    return {
        "requirements_text": requirements_text,
        "extra_context": extra_context,
        "selected_model": selected_model,
        "file_warns": file_warns,
        "project_id": project_id,
        "platform_type": platform_type,
        "project_name": project_name,
        "base_url": base_url,
        "entry_target": entry_target or base_url,
        "upload_filename": upload_filename,
        "input_kind": kind,
        "file_snippets": file_snippets,
    }


def _drafts_from_frontend_source(
    parsed: Dict[str, Any],
    *,
    profile: Any,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any], Optional[str]]:
    """上传/粘贴前端源码：后台解析组件并由 AI/启发式直接生成草案（无独立分析入口）。"""
    from ai_modules.code_intel.ui_agent import generate_reliable_cases_from_frontend
    from ai_modules.generate.input_classify import split_frontend_snippets
    from ai_modules.code_intel.policy import resolve_use_llm

    warns: List[str] = []
    platform = parsed["platform_type"]
    if platform not in ("web", "api"):
        warns.append(
            f"上传内容识别为前端源码，已按 Web UI 自动化生成（当前平台选择={platform}）"
        )
        platform = "web"

    snippets = parsed.get("file_snippets") if isinstance(parsed.get("file_snippets"), dict) else None
    if not snippets:
        snippets = split_frontend_snippets(
            filename=parsed.get("upload_filename") or "src/Component.tsx",
            text=parsed.get("requirements_text") or "",
        )

    extra = (parsed.get("extra_context") or "").strip()
    # 用户在文本框写的补充需求：若正文即源码，extra 可空；若 multipart 里混了 requirements_text 已并入正文
    drafts, gw, meta = generate_reliable_cases_from_frontend(
        file_snippets=snippets,
        base_url=parsed.get("entry_target") or parsed.get("base_url") or "",
        extra_requirements=extra,
        profile=profile,
        use_llm=resolve_use_llm(True),
    )
    warns.extend(gw or [])
    # 对齐设计页草案字段
    out_drafts: List[Dict[str, Any]] = []
    for d in drafts or []:
        if not isinstance(d, dict):
            continue
        out_drafts.append(
            {
                "case_name": d.get("case_name") or "前端源码生成用例",
                "case_role": d.get("case_role") or "business",
                "design_method": d.get("design_method") or "前端组件识别",
                "case_url": d.get("case_url") or (parsed.get("entry_target") or ""),
                "description": d.get("description") or "",
                "precondition": d.get("precondition") or "",
                "expected_result": d.get("expected_result") or "",
                "steps": d.get("steps") or [],
                "stability_score": d.get("stability_score"),
                "covered_components": d.get("covered_components"),
                "review_status": "pending",
            }
        )
    if not out_drafts:
        return [], warns, meta, "未能从前端源码生成可用草案（缺少稳定定位线索时可补充 testid）"
    if meta.get("inventory_summary"):
        warns.insert(0, f"已识别组件：{meta.get('inventory_summary')}")
    return out_drafts, warns, meta, None


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

    all_warnings = list(parsed["file_warns"] or [])
    meta: Dict[str, Any] = {"input_kind": parsed["input_kind"]}

    if parsed["input_kind"] == "frontend_source":
        drafts, warns, fe_meta, err = _drafts_from_frontend_source(parsed, profile=profile)
        all_warnings.extend(warns)
        meta.update(fe_meta or {})
        if err:
            return {
                "success": False,
                "error": err,
                "warnings": all_warnings,
                "input_kind": "frontend_source",
                "meta": meta,
            }, 400
        return {
            "success": True,
            "drafts": drafts,
            "draft_count": len(drafts),
            "warnings": all_warnings,
            "platform_type": "web",
            "input_kind": "frontend_source",
            "meta": meta,
            "message": "已根据前端源码识别组件并生成草案（待审）",
        }, 200

    from ai_modules.generate.design_from_requirements import generate_design_drafts

    drafts, warns, err = generate_design_drafts(
        parsed["requirements_text"],
        profile,
        platform_type=parsed["platform_type"],
        base_url=parsed.get("entry_target") or parsed.get("base_url") or "",
        project_name=parsed["project_name"],
        extra_context=parsed["extra_context"],
        entry_target=parsed.get("entry_target") or parsed.get("base_url") or "",
    )
    all_warnings.extend(list(warns))
    if err:
        return {"success": False, "error": err, "warnings": all_warnings, "input_kind": parsed["input_kind"]}, 400

    return {
        "success": True,
        "drafts": drafts,
        "draft_count": len(drafts),
        "warnings": all_warnings,
        "platform_type": parsed["platform_type"],
        "input_kind": parsed["input_kind"],
        "meta": meta,
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

    from ai_modules.generate.input_classify import normalize_platform

    platform_type = normalize_platform(data.get("platform_type") or "web")
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

    # 前端源码生成的草案默认 pending
    input_kind = str(data.get("input_kind") or "").strip().lower()
    if input_kind in ("frontend_source", "frontend", "code"):
        for d in drafts:
            if isinstance(d, dict) and not d.get("review_status"):
                d["review_status"] = "pending"

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
    out["input_kind"] = input_kind or "requirements_doc"
    return out, 200
