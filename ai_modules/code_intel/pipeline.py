# -*- coding: utf-8 -*-
"""CodeChange 编排：信号 → 影响分析 → 匹配 → 可选生成/触发 runs → 自愈提案。"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from logger import uat_logger

from ai_modules.code_intel.task_store import (
    create_queued_task,
    find_by_ci_run_id,
    find_recent_duplicate,
    get_task,
    update_task,
)


def public_task_view(rec: Dict[str, Any]) -> Dict[str, Any]:
    """对外视图：截断大 diff。"""
    if not rec:
        return {}
    out = dict(rec)
    diff = str(out.get("diff") or "")
    if len(diff) > 4000:
        out["diff"] = diff[:4000] + "\n…(truncated)…"
    snippets = out.get("file_snippets")
    if isinstance(snippets, dict) and snippets:
        out["file_snippets"] = {
            k: (str(v)[:500] + ("…" if len(str(v)) > 500 else ""))
            for k, v in list(snippets.items())[:10]
        }
    return out


def enqueue_code_change(
    payload: Dict[str, Any],
    *,
    db_factory: Optional[Callable[[], Any]] = None,
    run_trigger: Optional[Callable[..., Optional[str]]] = None,
    profile: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
    background: bool = True,
) -> Dict[str, Any]:
    """
    创建或复用任务。默认异步处理。
    run_trigger(case_ids, ...) -> ci_run_id | None
    """
    from ai_modules.code_intel.policy import clamp_payload, dedup_window_minutes
    from ai_modules.code_intel.repo_map import resolve_project_id

    payload = dict(payload)
    payload, policy_warns = clamp_payload(payload)

    # repo → project 自动解析
    if payload.get("project_id") is None and payload.get("repo"):
        mapped = resolve_project_id(str(payload.get("repo") or ""), str(payload.get("branch") or ""))
        if mapped:
            payload["project_id"] = mapped.get("project_id")
            if payload.get("tenant_id") is None and mapped.get("tenant_id") is not None:
                payload["tenant_id"] = mapped.get("tenant_id")
            policy_warns.append(f"已按 repo 映射 project_id={payload['project_id']}")

    git_sha = str(payload.get("git_sha") or "").strip()
    project_id = payload.get("project_id")
    mr_key = str(payload.get("mr_key") or "").strip()
    if not mr_key:
        # 从 pr_url / 描述构造弱键
        mr_key = str(payload.get("pr_url") or payload.get("mr_url") or "").strip()
    payload["mr_key"] = mr_key

    existing = find_recent_duplicate(
        git_sha=git_sha,
        project_id=project_id,
        mr_key=mr_key,
        window_minutes=dedup_window_minutes(),
    )
    if existing and existing.get("status") in ("queued", "running", "done"):
        view = public_task_view(existing)
        view["idempotent_hit"] = True
        if policy_warns:
            view["warnings"] = list(existing.get("warnings") or []) + policy_warns
        return view

    files = payload.get("changed_files") or []
    if isinstance(files, str):
        files = [f.strip() for f in files.replace(";", "\n").splitlines() if f.strip()]
    payload["changed_files"] = [str(f) for f in files if f]

    rec = create_queued_task(payload)
    if policy_warns:
        update_task(rec["task_id"], warnings=policy_warns)

    def _worker():
        try:
            process_code_change(
                rec["task_id"],
                db_factory=db_factory,
                run_trigger=run_trigger,
                profile=profile,
                use_llm=use_llm,
            )
        except Exception as e:
            uat_logger.error("[CODE_INTEL] worker failed task=%s: %s", rec["task_id"], e)
            update_task(rec["task_id"], status="failed", error=str(e)[:500])

    if background:
        t = threading.Thread(target=_worker, name=f"code-change-{rec['task_id']}", daemon=True)
        t.start()
    else:
        _worker()

    return public_task_view(get_task(rec["task_id"]) or rec)


def process_code_change(
    task_id: str,
    *,
    db_factory: Optional[Callable[[], Any]] = None,
    run_trigger: Optional[Callable[..., Optional[str]]] = None,
    profile: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    from ai_modules.code_intel.signals import extract_ui_signals
    from ai_modules.code_intel.impact import build_change_impact_report
    from ai_modules.code_intel.match_cases import (
        load_project_cases_for_match,
        match_cases_to_impact,
    )
    from ai_modules.code_intel.heal_bridge import (
        build_heal_proposals_from_run,
        build_heal_proposals_from_run_audited,
        mark_cases_at_risk_meta,
    )
    from ai_modules.code_intel.review import filter_ci_case_ids

    rec = get_task(task_id)
    if not rec:
        raise ValueError(f"task not found: {task_id}")

    update_task(task_id, status="running", error=None)
    warnings: List[str] = list(rec.get("warnings") or [])

    signals = extract_ui_signals(
        diff=str(rec.get("diff") or ""),
        changed_files=list(rec.get("changed_files") or []),
        file_snippets=rec.get("file_snippets") if isinstance(rec.get("file_snippets"), dict) else {},
    )

    # 融合前端组件清单（有 file_snippets 时）
    frontend_inventory = None
    snippets = rec.get("file_snippets") if isinstance(rec.get("file_snippets"), dict) else {}
    if snippets or str(rec.get("diff") or ""):
        try:
            from ai_modules.code_intel.frontend_parser import parse_frontend_files

            frontend_inventory = parse_frontend_files(
                snippets or {},
                diff=str(rec.get("diff") or ""),
            )
            # 把高稳 testid 并入 signals，提升匹配
            for n in frontend_inventory.get("recommended_for_automation") or []:
                tid = n.get("testid")
                if tid and tid not in (signals.get("testids") or []):
                    signals.setdefault("testids", []).append(tid)
                name = n.get("accessible_name")
                if name and name not in (signals.get("aria_labels") or []):
                    signals.setdefault("aria_labels", []).append(name)
            for r in frontend_inventory.get("routes") or []:
                if r not in (signals.get("routes") or []):
                    signals.setdefault("routes", []).append(r)
            signals["frontend_inventory_summary"] = frontend_inventory.get("summary")
            signals["stability_buckets"] = frontend_inventory.get("stability_buckets")
        except Exception as e:
            warnings.append(f"前端组件解析跳过: {str(e)[:120]}")
            uat_logger.warning("[CODE_INTEL] frontend parse skipped: %s", e)

    impact = build_change_impact_report(
        diff=str(rec.get("diff") or ""),
        changed_files=list(rec.get("changed_files") or []),
        file_snippets=snippets,
        mr_description=str(rec.get("mr_description") or ""),
        signals=signals,
        profile=profile,
        use_llm=use_llm,
    )
    if frontend_inventory:
        impact["frontend_components"] = {
            "summary": frontend_inventory.get("summary"),
            "stability_buckets": frontend_inventory.get("stability_buckets"),
            "interactive_count": len(frontend_inventory.get("interactive_nodes") or []),
            "high_stability_nodes": [
                {
                    "tag": n.get("tag"),
                    "testid": n.get("testid"),
                    "name": n.get("accessible_name"),
                    "role": n.get("semantic_role"),
                    "locator": (n.get("best_locator") or {}).get("selector_value"),
                }
                for n in (frontend_inventory.get("recommended_for_automation") or [])[:20]
            ],
        }
    if impact.get("warnings"):
        warnings.extend(impact.get("warnings") or [])

    recommended: List[int] = []
    at_risk: List[int] = []
    matches: List[Dict[str, Any]] = []
    draft_ids: List[int] = []
    draft_preview: List[Dict[str, Any]] = []
    ci_run_id = None
    heal_proposals: List[Dict[str, Any]] = []
    rollback_hint = None

    if impact.get("is_rollback"):
        rollback_hint = {
            "action": "restore_historical_cases",
            "git_sha": rec.get("git_sha"),
            "message": "回滚变更：勿生成新用例；建议按 source_commit 恢复历史用例版本后回归",
        }

    project_id = rec.get("project_id")
    db = None
    if project_id is not None and db_factory is not None:
        try:
            db = db_factory()
            cases = load_project_cases_for_match(db, int(project_id))
            matched = match_cases_to_impact(cases, impact)
            recommended = list(matched.get("recommended_case_ids") or [])
            at_risk = list(matched.get("at_risk_case_ids") or [])
            matches = list(matched.get("matches") or [])
            if matched.get("note"):
                warnings.append(str(matched["note"]))
            if matched.get("embedding_used"):
                warnings.append("匹配已叠加 embedding 相似度加权")
        except Exception as e:
            warnings.append(f"用例匹配失败: {str(e)[:160]}")
            uat_logger.warning("[CODE_INTEL] match failed: %s", e)

    want_gen = bool(rec.get("generate_drafts"))
    want_preview = want_gen or bool(impact.get("is_new_feature")) or bool(
        (impact.get("suggested_new_coverage") or [])
    )
    if want_preview and not impact.get("is_rollback"):
        try:
            from ai_modules.code_intel.generate_from_code import (
                generate_cases_from_code,
                save_code_drafts_pending,
            )

            existing_blobs: List[str] = []
            if db is not None and project_id is not None:
                try:
                    for c in load_project_cases_for_match(db, int(project_id)):
                        existing_blobs.append(
                            f"{c.get('name','')} {c.get('description','')}"
                        )
                except Exception:
                    pass

            drafts, gw = generate_cases_from_code(
                signals=signals,
                impact=impact,
                diff=str(rec.get("diff") or ""),
                base_url="",
                git_sha=str(rec.get("git_sha") or ""),
                profile=profile,
                use_llm=use_llm,
                existing_case_blobs=existing_blobs,
                file_snippets=rec.get("file_snippets") if isinstance(rec.get("file_snippets"), dict) else {},
            )
            warnings.extend(gw)
            for d in drafts:
                if isinstance(d, dict):
                    d["source_commit"] = str(rec.get("git_sha") or "")[:64]
            draft_preview = [
                {
                    "case_name": d.get("case_name"),
                    "description": (d.get("description") or "")[:300],
                    "step_count": len(d.get("steps") or []),
                    "review_status": "pending",
                }
                for d in drafts
            ]
            if want_gen and drafts and db is not None and project_id is not None:
                saved = save_code_drafts_pending(
                    db,
                    project_id=int(project_id),
                    drafts=drafts,
                    user_id=0,
                    git_sha=str(rec.get("git_sha") or ""),
                )
                draft_ids = list(saved.get("created_case_ids") or [])
                warnings.extend(saved.get("warnings") or [])
            elif want_gen and project_id is None:
                warnings.append("generate_drafts 需要 project_id 才能落库")
        except Exception as e:
            warnings.append(f"草稿生成失败: {str(e)[:160]}")
            uat_logger.warning("[CODE_INTEL] generate failed: %s", e)

    if bool(rec.get("trigger_run")) and recommended and run_trigger is not None:
        try:
            # CI 门禁排除 pending
            run_ids = recommended
            if db is not None:
                filtered = filter_ci_case_ids(db, recommended, include_pending=False)
                run_ids = filtered.get("kept") or []
                for sk in filtered.get("skipped") or []:
                    warnings.append(f"跳过未激活用例: {sk}")
            if run_ids:
                ci_run_id = run_trigger(
                    case_ids=run_ids,
                    project_id=project_id,
                    build_id=rec.get("build_id") or "",
                    git_sha=rec.get("git_sha") or "",
                    branch=rec.get("branch") or "",
                    trigger_source=rec.get("trigger_source") or "code_change",
                )
            else:
                warnings.append("推荐用例均未激活，未触发 CI run")
        except Exception as e:
            warnings.append(f"触发 CI run 失败: {str(e)[:160]}")
            uat_logger.warning("[CODE_INTEL] trigger_run failed: %s", e)

    if ci_run_id and at_risk:
        try:
            from ci_adapter import get_run, is_terminal_status

            run_rec = get_run(str(ci_run_id))
            if run_rec and is_terminal_status(run_rec.get("status")):
                heal_proposals = build_heal_proposals_from_run_audited(
                    task_id=task_id,
                    git_sha=str(rec.get("git_sha") or ""),
                    at_risk_case_ids=at_risk,
                    ci_run=run_rec,
                    db=db,
                )
        except Exception as e:
            warnings.append(f"自愈提案构建失败: {str(e)[:120]}")

    at_risk_meta = mark_cases_at_risk_meta(impact, at_risk)

    updated = update_task(
        task_id,
        status="done",
        impact=impact,
        signals=signals,
        recommended_case_ids=recommended,
        at_risk_case_ids=at_risk,
        at_risk_meta=at_risk_meta,
        matches=matches[:40],
        draft_case_ids=draft_ids,
        draft_preview=draft_preview,
        ci_run_id=ci_run_id,
        heal_proposals=heal_proposals,
        rollback_hint=rollback_hint,
        warnings=warnings,
        error=None,
    )
    _maybe_callback(updated or get_task(task_id))
    return public_task_view(updated or get_task(task_id) or {})


def attach_heal_proposals_for_run(
    task_id: str,
    db_factory: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    """在 CI run 终态后补建 heal 提案（供轮询或自动钩子）。"""
    from ai_modules.code_intel.heal_bridge import build_heal_proposals_from_run_audited
    from ci_adapter import get_run, is_terminal_status

    rec = get_task(task_id)
    if not rec:
        return {"ok": False, "error": "task not found"}
    ci_run_id = rec.get("ci_run_id")
    if not ci_run_id:
        return {"ok": False, "error": "no ci_run_id on task"}
    run_rec = get_run(str(ci_run_id))
    if not run_rec:
        return {"ok": False, "error": "ci run not found"}
    if not is_terminal_status(run_rec.get("status")):
        return {"ok": True, "pending": True, "message": "ci run 尚未终态"}

    db = db_factory() if db_factory else None
    proposals = build_heal_proposals_from_run_audited(
        task_id=task_id,
        git_sha=str(rec.get("git_sha") or ""),
        at_risk_case_ids=list(rec.get("at_risk_case_ids") or []),
        ci_run=run_rec,
        db=db,
    )
    update_task(task_id, heal_proposals=proposals)
    return {"ok": True, "heal_proposals": proposals, "count": len(proposals)}


def on_ci_run_finished(run_id: str, db_factory: Optional[Callable[[], Any]] = None) -> Optional[Dict[str, Any]]:
    """CI run 终态钩子：按 ci_run_id 找回 code-change 任务并写 heal 提案。"""
    rec = find_by_ci_run_id(str(run_id or ""))
    if not rec:
        return None
    try:
        return attach_heal_proposals_for_run(
            str(rec.get("task_id")),
            db_factory=db_factory,
        )
    except Exception as e:
        uat_logger.warning("[CODE_INTEL] on_ci_run_finished failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}


def _maybe_callback(rec: Optional[Dict[str, Any]]) -> None:
    if not rec:
        return
    url = str(rec.get("callback_url") or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        return
    try:
        import json
        import urllib.request

        body = {
            "task_id": rec.get("task_id"),
            "status": rec.get("status"),
            "git_sha": rec.get("git_sha"),
            "recommended_case_ids": rec.get("recommended_case_ids"),
            "at_risk_case_ids": rec.get("at_risk_case_ids"),
            "draft_case_ids": rec.get("draft_case_ids"),
            "ci_run_id": rec.get("ci_run_id"),
            "impact_summary": (rec.get("impact") or {}).get("summary")
            if isinstance(rec.get("impact"), dict)
            else None,
            "poll_url": rec.get("poll_url"),
            "rollback_hint": rec.get("rollback_hint"),
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        uat_logger.warning("[CODE_INTEL] callback failed: %s", e)

