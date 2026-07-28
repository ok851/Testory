# -*- coding: utf-8 -*-
"""从代码信号 / diff 生成待审核用例草稿（testid 优先，不直接进门禁）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from logger import uat_logger

CODE_GEN_SCHEMA = """{
  "cases": [
    {
      "case_name": "string",
      "case_role": "business",
      "design_method": "代码变更覆盖",
      "case_url": "string",
      "description": "string",
      "precondition": "string",
      "expected_result": "string",
      "steps": [
        {
          "action": "navigate|click|input|wait|assert|api_request",
          "selector_type": "css|text|testid",
          "selector_value": "string",
          "input_value": "string",
          "description": "string",
          "automation_layer": "web"
        }
      ]
    }
  ]
}"""


def _heuristic_drafts_from_signals(
    signals: Dict[str, Any],
    impact: Dict[str, Any],
    *,
    base_url: str = "",
    git_sha: str = "",
) -> List[Dict[str, Any]]:
    """无 LLM 时的最小草稿：每个 testid / 路由一条冒烟。"""
    drafts: List[Dict[str, Any]] = []
    testids = list(signals.get("testids") or [])[:8]
    routes = list(signals.get("routes") or [])[:3]
    sha_note = f" source_commit={git_sha}" if git_sha else ""

    if not testids and not routes and impact.get("is_new_feature"):
        drafts.append({
            "case_name": "代码变更-新功能冒烟（待补充选择器）",
            "case_role": "business",
            "design_method": "代码变更覆盖",
            "case_url": base_url or "",
            "description": (
                "[待审核][由代码自动生成][review_status:pending]"
                f"{sha_note} 基于变更信号的占位冒烟，需人工补全步骤。"
            ),
            "precondition": "测试环境可访问",
            "expected_result": "关键页面可打开且无报错",
            "steps": [
                {
                    "action": "navigate",
                    "selector_type": "",
                    "selector_value": "",
                    "input_value": base_url or "/",
                    "description": "打开应用入口",
                    "automation_layer": "web",
                },
                {
                    "action": "assert",
                    "selector_type": "text",
                    "selector_value": "",
                    "input_value": "",
                    "description": "browser_get_screen_text 确认页面已加载（待人工细化）",
                    "automation_layer": "web",
                    "compare_type": "contains",
                },
            ],
        })
        return drafts

    for tid in testids:
        drafts.append({
            "case_name": f"代码覆盖: {tid}"[:200],
            "case_role": "business",
            "design_method": "代码变更覆盖",
            "case_url": base_url or (routes[0] if routes else ""),
            "description": (
                "[待审核][由代码自动生成][review_status:pending]"
                f"{sha_note} 优先使用 data-testid={tid}"
            ),
            "precondition": "测试环境可访问；相关权限已就绪",
            "expected_result": f"可定位并操作 testid={tid}",
            "steps": _steps_for_testid(tid, base_url, routes),
        })

    if not testids:
        for route in routes[:2]:
            drafts.append({
                "case_name": f"路由冒烟: {route}"[:200],
                "case_role": "business",
                "design_method": "代码变更覆盖",
                "case_url": route,
                "description": (
                    "[待审核][由代码自动生成][review_status:pending]"
                    f"{sha_note} 基于路由 {route}"
                ),
                "precondition": "已登录（如需要）",
                "expected_result": "页面可打开",
                "steps": [
                    {
                        "action": "navigate",
                        "selector_type": "",
                        "selector_value": "",
                        "input_value": (base_url.rstrip("/") + route) if base_url else route,
                        "description": f"browser 导航到 {route}",
                        "automation_layer": "web",
                    }
                ],
            })
    return drafts[:10]


def _steps_for_testid(testid: str, base_url: str, routes: List[str]) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    target = base_url or (routes[0] if routes else "")
    if target:
        steps.append({
            "action": "navigate",
            "selector_type": "",
            "selector_value": "",
            "input_value": target,
            "description": "打开相关页面",
            "automation_layer": "web",
        })
    steps.append({
        "action": "click",
        "selector_type": "css",
        "selector_value": f'[data-testid="{testid}"]',
        "input_value": "",
        "description": f'browser_click_element(description="testid:{testid}")',
        "automation_layer": "web",
    })
    steps.append({
        "action": "assert",
        "selector_type": "css",
        "selector_value": f'[data-testid="{testid}"]',
        "input_value": "",
        "description": f"断言元素 testid={testid} 可见",
        "automation_layer": "web",
        "compare_type": "visible",
    })
    return steps


def _build_code_gen_prompt(
    *,
    signals: Dict[str, Any],
    impact: Dict[str, Any],
    diff: str,
    base_url: str,
    git_sha: str,
) -> str:
    sig = json.dumps(
        {
            "testids": signals.get("testids"),
            "aria_labels": signals.get("aria_labels"),
            "routes": signals.get("routes"),
            "api_hints": signals.get("api_hints"),
        },
        ensure_ascii=False,
    )
    impact_summary = json.dumps(
        {
            "change_types": impact.get("change_types"),
            "affected_modules": impact.get("affected_modules"),
            "suggested_new_coverage": impact.get("suggested_new_coverage"),
            "is_new_feature": impact.get("is_new_feature"),
            "summary": impact.get("summary"),
        },
        ensure_ascii=False,
    )
    return (
        "你是测试用例设计助手。根据前端/后端变更信号生成**待审核**自动化用例草案。\n"
        f"输出 JSON Schema:\n{CODE_GEN_SCHEMA}\n\n"
        "硬性规则：\n"
        "1. 有 data-testid 时，selector_type=css，selector_value=[data-testid=\"...\"]\n"
        "2. 无 testid 时用可见文字/aria-label，description 标明「视觉可定位」\n"
        "3. description 中可用 browser_click_element(description=...) 语义描述，勿写死坐标\n"
        "4. 每条用例 description 必须以 [待审核][由代码自动生成][review_status:pending] 开头\n"
        "5. 最多 5 条；覆盖主路径与 1 条异常/分支即可\n"
        "6. 不要编造未出现的 API 或页面\n"
        f"7. 若有 commit，在 description 中注明 source_commit={git_sha or 'unknown'}\n"
        f"base_url={base_url or '(none)'}\n\n"
        f"影响摘要:\n{impact_summary}\n\n"
        f"信号:\n{sig}\n\n"
        f"Diff(截断):\n{(diff or '')[:16000]}\n"
    )


def generate_cases_from_code(
    *,
    signals: Dict[str, Any],
    impact: Dict[str, Any],
    diff: str = "",
    base_url: str = "",
    git_sha: str = "",
    profile: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
    existing_case_blobs: Optional[List[str]] = None,
    file_snippets: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    返回 (drafts, warnings)。
    若提供 file_snippets，优先走 UI Agent（组件精准识别 + 测试知识）。
    """
    warns: List[str] = []
    if impact.get("is_rollback"):
        warns.append("回滚变更：跳过新用例生成，建议恢复历史用例版本")
        return [], warns

    types = [str(t) for t in (impact.get("change_types") or [])]
    if types and all(t in ("style_only", "test_only") for t in types):
        warns.append("变更类型为样式/测试文件：跳过用例生成")
        return [], warns

    # 主路径：有前端源码片段时用 UI Agent
    snippets = file_snippets if isinstance(file_snippets, dict) else {}
    if snippets or (diff and ("data-testid" in diff or "<button" in diff or "<template" in diff)):
        try:
            from ai_modules.code_intel.ui_agent import generate_reliable_cases_from_frontend

            drafts, gw, meta = generate_reliable_cases_from_frontend(
                file_snippets=snippets,
                diff=diff,
                base_url=base_url,
                git_sha=git_sha,
                use_llm=use_llm,
                existing_case_blobs=existing_case_blobs,
                profile=profile,
            )
            warns.extend(gw)
            if meta.get("inventory_summary"):
                warns.append(f"UI分析: {meta['inventory_summary']}")
            if drafts:
                return drafts, warns
        except Exception as e:
            uat_logger.warning("[CODE_INTEL] ui_agent path failed: %s", e)
            warns.append(f"UI Agent 路径失败，回退信号生成: {str(e)[:120]}")

    drafts: List[Dict[str, Any]] = []
    if use_llm:
        try:
            from ai_selector_recovery import _extract_json_obj
            from ai_local_inference import local_ai_service
            from ai_multi_provider import dispatch_chat
            from ai_modules.generate.design_from_requirements import _normalize_draft

            prompt = _build_code_gen_prompt(
                signals=signals,
                impact=impact,
                diff=diff,
                base_url=base_url,
                git_sha=git_sha,
            )
            raw = dispatch_chat(prompt, profile, local_ai_service)
            data = _extract_json_obj(raw)
            if isinstance(data, dict) and isinstance(data.get("cases"), list):
                for item in data["cases"][:8]:
                    if not isinstance(item, dict):
                        continue
                    d = _normalize_draft(item, "web", base_url or "")
                    desc = str(d.get("description") or "")
                    if "[待审核]" not in desc:
                        d["description"] = (
                            "[待审核][由代码自动生成][review_status:pending] " + desc
                        )[:4000]
                    if git_sha and f"source_commit={git_sha}" not in d["description"]:
                        d["description"] = (
                            d["description"] + f" source_commit={git_sha}"
                        )[:4000]
                    drafts.append(d)
        except Exception as e:
            uat_logger.warning("[CODE_INTEL] generate_from_code LLM failed: %s", e)
            warns.append(f"LLM 生成失败，使用启发式: {str(e)[:160]}")

    if not drafts:
        drafts = _heuristic_drafts_from_signals(
            signals, impact, base_url=base_url, git_sha=git_sha
        )
        if drafts:
            warns.append("使用启发式草稿（testid/路由）")

    blobs = [b.lower() for b in (existing_case_blobs or []) if b]
    if blobs and drafts:
        kept: List[Dict[str, Any]] = []
        for d in drafts:
            name = str(d.get("case_name") or "").lower()
            desc = str(d.get("description") or "").lower()
            dup = False
            for b in blobs:
                if name and name in b:
                    dup = True
                    break
                for tid in signals.get("testids") or []:
                    if tid and tid.lower() in b and tid.lower() in desc:
                        dup = True
                        break
                if dup:
                    break
            if dup:
                warns.append(f"跳过疑似重复草案: {d.get('case_name')}")
            else:
                kept.append(d)
        drafts = kept

    return drafts[:8], warns


def save_code_drafts_pending(
    db: Any,
    *,
    project_id: int,
    drafts: List[Dict[str, Any]],
    user_id: int = 0,
    git_sha: str = "",
) -> Dict[str, Any]:
    """写入项目，标记 AI 生成 + review_status=pending；不自动进 CI。"""
    from ai_modules.generate.design_from_requirements import save_design_drafts_to_project
    from ai_modules.code_intel.review import ensure_pending_description, REVIEW_PENDING

    batch = f"CODE-{ (git_sha or 'adhoc')[:12] }"
    for d in drafts:
        if not isinstance(d, dict):
            continue
        d["description"] = ensure_pending_description(
            str(d.get("description") or ""), git_sha=git_sha
        )
        d["review_status"] = REVIEW_PENDING

    result = save_design_drafts_to_project(
        db,
        project_id=int(project_id),
        platform_type="web",
        drafts=drafts,
        user_id=user_id,
        batch_id=batch,
    )
    # 将新建用例正式标为 pending（若 DB 支持 review_status）
    for cid in result.get("created_case_ids") or []:
        try:
            if hasattr(db, "set_case_review_status"):
                db.set_case_review_status(int(cid), REVIEW_PENDING, git_sha=git_sha)
        except Exception:
            pass
    result["review_status"] = REVIEW_PENDING
    return result
