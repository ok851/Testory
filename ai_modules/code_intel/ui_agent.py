# -*- coding: utf-8 -*-
"""前端 UI Agent：解析组件 → 应用测试知识 → 生成可靠稳定自动化用例。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from logger import uat_logger

from ai_modules.code_intel.frontend_parser import (
    inventory_to_prompt_block,
    parse_frontend_files,
)
from ai_modules.code_intel.test_knowledge import (
    AUTOMATION_KNOWLEDGE_PROMPT,
    step_from_locator,
)

UI_AGENT_CASE_SCHEMA = """{
  "cases": [
    {
      "case_name": "string",
      "case_role": "business|login_feature",
      "design_method": "前端组件分析+稳定定位",
      "case_url": "string",
      "description": "string",
      "precondition": "string",
      "expected_result": "string",
      "stability_score": "high|medium|low",
      "covered_components": ["testid or name"],
      "steps": [
        {
          "action": "navigate|click|input|wait|assert|select",
          "selector_type": "css|text",
          "selector_value": "string",
          "input_value": "string",
          "description": "string",
          "automation_layer": "web",
          "locator_stability": "high|medium|low",
          "locator_strategy": "testid|role_name|aria_label|text|..."
        }
      ]
    }
  ],
  "warnings": ["string"]
}"""


def analyze_frontend_ui(
    *,
    file_snippets: Optional[Dict[str, Any]] = None,
    diff: str = "",
) -> Dict[str, Any]:
    """仅分析：返回组件清单与自动化推荐节点。"""
    inventory = parse_frontend_files(file_snippets or {}, diff=diff or "")
    return {
        "ok": True,
        "inventory": inventory,
        "prompt_block": inventory_to_prompt_block(inventory),
    }


def _heuristic_cases_from_inventory(
    inventory: Dict[str, Any],
    *,
    base_url: str = "",
    git_sha: str = "",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """无 LLM 时：对高/中稳定性节点生成可执行冒烟 + 表单流。"""
    warns: List[str] = []
    drafts: List[Dict[str, Any]] = []
    nodes = list(inventory.get("recommended_for_automation") or [])[:20]
    routes = list(inventory.get("routes") or [])
    entry = base_url or (routes[0] if routes else "")
    sha_note = f" source_commit={git_sha}" if git_sha else ""

    if not nodes:
        warns.append("未识别到高/中稳定性可交互节点，无法启发式生成可靠用例")
        return [], warns

    # 1) 每个高稳按钮/链接一条点击冒烟
    for n in nodes:
        loc = n.get("best_locator")
        if not loc or loc.get("stability") not in ("high", "medium"):
            continue
        role = n.get("semantic_role")
        if role not in ("button", "link", "tab", "menuitem", "checkbox", "switch"):
            continue
        name = n.get("accessible_name") or n.get("testid") or n.get("tag")
        steps: List[Dict[str, Any]] = []
        if entry:
            steps.append({
                "action": "navigate",
                "selector_type": "",
                "selector_value": "",
                "input_value": entry if entry.startswith("http") or entry.startswith("/") else f"/{entry}",
                "description": "打开相关页面",
                "automation_layer": "web",
                "locator_stability": "high",
                "locator_strategy": "route",
            })
        steps.append(step_from_locator(
            action="click",
            locator=loc,
            accessible_name=str(name),
        ))
        steps.append({
            "action": "assert",
            "selector_type": loc.get("selector_type") if loc.get("selector_type") != "role" else "css",
            "selector_value": loc.get("selector_value") if loc.get("selector_type") != "role" else (
                f'[data-testid="{n["testid"]}"]' if n.get("testid") else ""
            ),
            "input_value": "",
            "description": f"断言目标仍可定位或页面已响应（{name}）",
            "automation_layer": "web",
            "compare_type": "visible",
            "locator_stability": loc.get("stability"),
            "locator_strategy": loc.get("strategy"),
        })
        # fix assert selector for role→text
        if loc.get("selector_type") == "role" and n.get("accessible_name"):
            steps[-1]["selector_type"] = "text"
            steps[-1]["selector_value"] = n["accessible_name"]

        drafts.append({
            "case_name": f"组件冒烟: {name}"[:200],
            "case_role": "business",
            "design_method": "前端组件分析+稳定定位",
            "case_url": entry,
            "description": (
                f"[待审核][由代码自动生成][review_status:pending]{sha_note} "
                f"strategy={loc.get('strategy')} stability={loc.get('stability')} "
                f"component={n.get('tag')}"
            )[:4000],
            "precondition": "测试环境可访问；如需登录请先完成鉴权",
            "expected_result": f"可稳定定位并操作 {name}",
            "stability_score": loc.get("stability") or "medium",
            "covered_components": [n.get("testid") or name],
            "steps": steps,
        })
        if len(drafts) >= 6:
            break

    # 2) 表单流：同文件内 textbox + button
    textboxes = [n for n in nodes if n.get("semantic_role") == "textbox" and n.get("best_locator")]
    submit = next(
        (n for n in nodes
         if n.get("semantic_role") == "button"
         and n.get("best_locator")
         and any(x in (n.get("accessible_name") or n.get("testid") or "").lower()
                 for x in ("submit", "登录", "保存", "提交", "确认", "login", "save"))),
        None,
    )
    if textboxes and submit and len(drafts) < 8:
        steps = []
        if entry:
            steps.append({
                "action": "navigate",
                "selector_type": "",
                "selector_value": "",
                "input_value": entry,
                "description": "打开表单页",
                "automation_layer": "web",
            })
        for tb in textboxes[:4]:
            loc = tb["best_locator"]
            sample = "test_user" if "user" in (tb.get("name_attr") or tb.get("testid") or "").lower() or "用户" in (tb.get("accessible_name") or "") else "sample"
            if (tb.get("input_type") or "").lower() == "password" or "pass" in (tb.get("testid") or "").lower() or "密" in (tb.get("accessible_name") or "").lower():
                sample = "Test@12345"
            steps.append(step_from_locator(
                action="input",
                locator=loc,
                input_value=sample,
                accessible_name=str(tb.get("accessible_name") or tb.get("testid") or ""),
            ))
        steps.append(step_from_locator(
            action="click",
            locator=submit["best_locator"],
            accessible_name=str(submit.get("accessible_name") or submit.get("testid") or "提交"),
        ))
        drafts.append({
            "case_name": "表单主路径填写并提交",
            "case_role": "login_feature" if any("login" in str(x.get("testid") or "").lower() or "登录" in str(x.get("accessible_name") or "") for x in textboxes + [submit]) else "business",
            "design_method": "前端组件分析+稳定定位",
            "case_url": entry,
            "description": (
                f"[待审核][由代码自动生成][review_status:pending]{sha_note} "
                "基于解析到的输入框与提交按钮生成主路径"
            ),
            "precondition": "测试环境可访问",
            "expected_result": "表单可填写并触发提交",
            "stability_score": "high" if all(
                (n.get("best_locator") or {}).get("stability") == "high"
                for n in textboxes[:2] + [submit]
            ) else "medium",
            "covered_components": [
                n.get("testid") or n.get("accessible_name")
                for n in textboxes[:4] + [submit]
            ],
            "steps": steps,
        })

    if not drafts:
        warns.append("有节点但未映射到可生成的按钮/表单模式")
    return drafts[:8], warns


def _build_llm_prompt(
    *,
    inventory: Dict[str, Any],
    diff: str,
    base_url: str,
    git_sha: str,
    extra_requirements: str,
) -> str:
    block = inventory_to_prompt_block(inventory, max_nodes=35)
    return (
        f"{AUTOMATION_KNOWLEDGE_PROMPT}\n\n"
        f"输出 JSON Schema:\n{UI_AGENT_CASE_SCHEMA}\n\n"
        f"base_url={base_url or '(未提供，不要编造 URL；无入口则从组件操作开始)'}\n"
        f"git_sha={git_sha or '(none)'}\n"
        f"额外需求:\n{(extra_requirements or '无')[:3000]}\n\n"
        f"前端组件清单:\n{block}\n\n"
        f"Diff(截断，仅作上下文):\n{(diff or '')[:12000]}\n\n"
        "请生成 3~8 条高稳定性优先的用例。低稳定性节点除非业务关键否则跳过。"
    )


def generate_reliable_cases_from_frontend(
    *,
    file_snippets: Optional[Dict[str, Any]] = None,
    diff: str = "",
    base_url: str = "",
    git_sha: str = "",
    extra_requirements: str = "",
    profile: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
    existing_case_blobs: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """
    主入口：返回 (drafts, warnings, analysis_meta)。
    drafts 默认 pending，优先高稳定位。
    """
    warns: List[str] = []
    inventory = parse_frontend_files(file_snippets or {}, diff=diff or "")
    meta = {
        "inventory": inventory,
        "inventory_summary": inventory.get("summary"),
        "stability_buckets": inventory.get("stability_buckets"),
        "files_parsed": inventory.get("files_parsed"),
        "analysis_source": "heuristic",
    }

    if inventory.get("files_parsed", 0) == 0 and not (diff or "").strip():
        warns.append("未提供可解析的前端文件内容（file_snippets）或 diff")
        return [], warns, meta

    drafts: List[Dict[str, Any]] = []

    if use_llm and (inventory.get("interactive_nodes") or diff):
        try:
            from ai_selector_recovery import _extract_json_obj
            from ai_local_inference import local_ai_service
            from ai_multi_provider import dispatch_chat
            from ai_modules.generate.design_from_requirements import _normalize_draft
            from ai_modules.code_intel.policy import llm_timeout_s
            import concurrent.futures

            prompt = _build_llm_prompt(
                inventory=inventory,
                diff=diff or "",
                base_url=base_url,
                git_sha=git_sha,
                extra_requirements=extra_requirements,
            )
            timeout = llm_timeout_s()

            def _call():
                return dispatch_chat(prompt, profile, local_ai_service)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_call)
                try:
                    raw = fut.result(timeout=timeout)
                except concurrent.futures.TimeoutError:
                    warns.append(f"UI Agent LLM 超时({timeout}s)，回退启发式")
                    raw = None

            if raw:
                data = _extract_json_obj(raw)
                if isinstance(data, dict):
                    if isinstance(data.get("warnings"), list):
                        warns.extend(str(w) for w in data["warnings"][:10])
                    for item in (data.get("cases") or [])[:10]:
                        if not isinstance(item, dict):
                            continue
                        d = _normalize_draft(item, "web", base_url or "")
                        desc = str(d.get("description") or "")
                        if "[待审核]" not in desc:
                            d["description"] = (
                                "[待审核][由代码自动生成][review_status:pending] " + desc
                            )[:4000]
                        if git_sha and f"source_commit={git_sha}" not in d["description"]:
                            d["description"] = (d["description"] + f" source_commit={git_sha}")[:4000]
                        d["stability_score"] = item.get("stability_score") or "medium"
                        d["covered_components"] = item.get("covered_components") or []
                        d["review_status"] = "pending"
                        d["source_commit"] = (git_sha or "")[:64]
                        # 确保步骤带 stability 字段
                        for st in d.get("steps") or []:
                            if isinstance(st, dict) and "locator_stability" not in st:
                                sv = str(st.get("selector_value") or "")
                                st["locator_stability"] = (
                                    "high" if "data-testid" in sv else "medium"
                                )
                        drafts.append(d)
                    if drafts:
                        meta["analysis_source"] = "llm_ui_agent"
        except Exception as e:
            uat_logger.warning("[UI_AGENT] LLM failed: %s", e)
            warns.append(f"UI Agent LLM 失败，回退启发式: {str(e)[:160]}")

    if not drafts:
        drafts, hw = _heuristic_cases_from_inventory(
            inventory, base_url=base_url, git_sha=git_sha
        )
        warns.extend(hw)
        meta["analysis_source"] = "heuristic"

    # 去重
    blobs = [b.lower() for b in (existing_case_blobs or []) if b]
    if blobs and drafts:
        kept = []
        for d in drafts:
            name = str(d.get("case_name") or "").lower()
            if name and any(name in b for b in blobs):
                warns.append(f"跳过疑似重复: {d.get('case_name')}")
                continue
            kept.append(d)
        drafts = kept

    # 质量过滤：无任何步骤选择器且非纯 navigate 的丢掉
    quality: List[Dict[str, Any]] = []
    for d in drafts:
        steps = [s for s in (d.get("steps") or []) if isinstance(s, dict)]
        has_loc = any(
            (s.get("selector_value") or s.get("action") == "navigate")
            for s in steps
        )
        if not steps or not has_loc:
            warns.append(f"丢弃无定位步骤的草案: {d.get('case_name')}")
            continue
        quality.append(d)

    meta["case_count"] = len(quality)
    return quality[:8], warns, meta


def generate_and_optionally_save(
    db: Any,
    *,
    project_id: int,
    file_snippets: Optional[Dict[str, Any]] = None,
    diff: str = "",
    base_url: str = "",
    git_sha: str = "",
    extra_requirements: str = "",
    use_llm: bool = True,
    save: bool = False,
    user_id: int = 0,
) -> Dict[str, Any]:
    """分析+生成；save=True 时落库 pending。"""
    from ai_modules.code_intel.generate_from_code import save_code_drafts_pending
    from ai_modules.code_intel.match_cases import load_project_cases_for_match

    blobs: List[str] = []
    try:
        for c in load_project_cases_for_match(db, int(project_id)):
            blobs.append(f"{c.get('name','')} {c.get('description','')}")
    except Exception:
        pass

    drafts, warns, meta = generate_reliable_cases_from_frontend(
        file_snippets=file_snippets,
        diff=diff,
        base_url=base_url,
        git_sha=git_sha,
        extra_requirements=extra_requirements,
        use_llm=use_llm,
        existing_case_blobs=blobs,
    )
    created: List[int] = []
    if save and drafts:
        saved = save_code_drafts_pending(
            db,
            project_id=int(project_id),
            drafts=drafts,
            user_id=user_id,
            git_sha=git_sha,
        )
        created = list(saved.get("created_case_ids") or [])
        warns.extend(saved.get("warnings") or [])

    return {
        "ok": True,
        "success": True,
        "drafts": drafts,
        "inventory": meta.get("inventory") if isinstance(meta.get("inventory"), dict) else None,
        "draft_preview": [
            {
                "case_name": d.get("case_name"),
                "stability_score": d.get("stability_score"),
                "step_count": len(d.get("steps") or []),
                "covered_components": d.get("covered_components"),
                "description": (d.get("description") or "")[:240],
            }
            for d in drafts
        ],
        "created_case_ids": created,
        "count": len(created) if save else len(drafts),
        "warnings": warns,
        "meta": meta,
        "message": (
            "已写入待审核用例，激活后才可进 CI"
            if save and created
            else "已生成草案（未落库）" if drafts else "未生成可用草案"
        ),
    }
