# -*- coding: utf-8 -*-
"""AI 用例设计：从需求一次生成多条测试草案（预览，不落库）。"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from logger import uat_logger

DESIGN_SCHEMA_HINT = """{
  "cases": [
    {
      "case_name": "string",
      "case_role": "login_feature|business|auth_fixture",
      "design_method": "string (e.g. 等价类划分, 边界值, 场景法, 错误推测)",
      "case_url": "string",
      "description": "string",
      "precondition": "string",
      "expected_result": "string",
      "steps": [
        {
          "action": "navigate|click|input|wait|verify|assert|extract_text|api_request|open_app|tap|input_text",
          "selector_type": "css|xpath|text|accessibility_id|id|...",
          "selector_value": "string",
          "input_value": "string",
          "description": "string",
          "compare_type": "string (assert only)",
          "automation_layer": "web|android (optional)",
          "api_spec": { "method": "GET", "url": "", "headers": {}, "body": null, "assertions": [] }
        }
      ]
    }
  ]
}"""


def design_max_cases() -> int:
    try:
        n = int(os.environ.get("AI_DESIGN_MAX_CASES", "10") or "10")
    except ValueError:
        n = 10
    return max(3, min(n, 15))


def design_requirements_max_chars() -> int:
    try:
        n = int(os.environ.get("AI_DESIGN_REQUIREMENTS_MAX_CHARS", "24000") or "24000")
    except ValueError:
        n = 24000
    return max(2000, min(n, 120000))


def build_design_preview_prompt(
    requirements_text: str,
    platform_type: str,
    base_url: str = "",
    project_name: str = "",
    extra_context: str = "",
) -> str:
    platform = (platform_type or "web").strip().lower()
    if platform in ("mobile",):
        platform = "android"
    max_cases = design_max_cases()
    bu = (base_url or "").strip()
    body = (requirements_text or "").strip()
    cap = design_requirements_max_chars()
    if len(body) > cap:
        body = body[: cap - 80] + "\n…(truncated)…"

    platform_rules = {
        "web": (
            "平台：Web UI 自动化。步骤 action 使用 navigate/click/input/wait/assert 等。\n"
            "login_feature：完整登录流程与断言。\n"
            "business：前置说明已登录；步骤从登录后业务操作开始，不要重复 navigate 到登录页再填账号密码；"
            "接口相关可在 description 注明使用 {{auth_token}}。\n"
            "auth_fixture（可选 0-1 条）：仅登录成功并进入系统，用于批量前置，步骤尽量短。\n"
        ),
        "api": (
            "平台：接口测试。每条用例 steps 仅含 action=api_request，api_spec 含 method/url/headers/body/assertions。\n"
            "login_feature：登录接口及错误凭据场景。\n"
            "business：业务接口，headers 使用 Authorization: Bearer {{auth_token}} 等占位符，不要写死 token。\n"
        ),
        "android": (
            "平台：Android。步骤含 automation_layer=android，action 使用 open_app/tap/input_text 等。\n"
            "login_feature 保留完整登录；business 从已登录状态开始。\n"
        ),
    }
    rules = platform_rules.get(platform, platform_rules["web"])

    url_block = ""
    if bu and platform == "web":
        url_block = (
            f"\n强制基础 URL（用户指定，禁止改用其他域名）：{bu}\n"
            "所有 navigate 的 input_value 必须使用该 URL 或其同主机路径；case_url 同步。\n"
            "禁止 example.com、admin.sanatorium.com 等未在需求或基础 URL 中出现的域名。\n"
        )

    return (
        "你是资深测试设计师。根据需求从多种测试设计方法生成可执行的测试用例草案。\n"
        "方法包括但不限于：等价类划分、边界值分析、判断表法、场景法、错误推测法。\n"
        f"产出 {max_cases} 条左右 cases（可略少，但至少 3 条），每条标注 design_method 与 case_role。\n"
        f"{rules}"
        f"{url_block}"
        "数据规则：账号、密码、URL 仅使用需求正文中明确给出的值，禁止编造。\n"
        "输出：ONLY 一个 JSON 对象，不要 markdown，结构：\n"
        + DESIGN_SCHEMA_HINT
        + f"\n\n项目：{project_name or 'unknown'}\n\n需求正文：\n"
        + body
        + (
            ("\n\n补充上下文：\n" + (extra_context or "").strip()[:4000])
            if (extra_context or "").strip()
            else ""
        )
    )


def _same_host(url_a: str, url_b: str) -> bool:
    try:
        ha = urlparse(url_a or "").netloc.lower()
        hb = urlparse(url_b or "").netloc.lower()
        return bool(ha and hb and ha == hb)
    except Exception:
        return False


def enforce_base_url_on_draft(draft: Dict[str, Any], base_url: str) -> None:
    """Web 草案：强制 case_url 与 navigate 使用用户 base_url。"""
    bu = (base_url or "").strip()
    if not bu or not isinstance(draft, dict):
        return
    if not (draft.get("case_url") or "").strip() or not _same_host(
        str(draft.get("case_url") or ""), bu
    ):
        draft["case_url"] = bu
    steps = draft.get("steps") if isinstance(draft.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if (step.get("action") or "").strip().lower() == "navigate":
            iv = (step.get("input_value") or "").strip()
            if not iv or not _same_host(iv, bu):
                step["input_value"] = bu
                step["selector_type"] = ""
                step["selector_value"] = ""


def _normalize_case_role(raw: Any) -> str:
    r = (str(raw or "").strip().lower() if raw is not None else "") or "business"
    if r in ("login_feature", "login", "login-test"):
        return "login_feature"
    if r in ("auth_fixture", "fixture", "auth_setup", "setup"):
        return "auth_fixture"
    return "business"


def _normalize_draft(raw: Dict[str, Any], platform_type: str, base_url: str) -> Dict[str, Any]:
    platform = (platform_type or "web").strip().lower()
    if platform == "mobile":
        platform = "android"
    steps_in = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    steps: List[Dict[str, Any]] = []
    for st in steps_in:
        if not isinstance(st, dict):
            continue
        action = (st.get("action") or "").strip().lower()
        if platform == "api" and action != "api_request":
            continue
        row = {
            "action": action or "navigate",
            "selector_type": str(st.get("selector_type") or ""),
            "selector_value": str(st.get("selector_value") or ""),
            "input_value": str(st.get("input_value") or ""),
            "description": str(st.get("description") or ""),
        }
        if st.get("compare_type"):
            row["compare_type"] = str(st.get("compare_type"))
        if st.get("automation_layer"):
            row["automation_layer"] = str(st.get("automation_layer"))
        if st.get("api_spec") is not None:
            row["api_spec"] = st.get("api_spec")
        steps.append(row)

    draft = {
        "case_name": str(raw.get("case_name") or raw.get("name") or "AI 用例")[:200],
        "case_role": _normalize_case_role(raw.get("case_role")),
        "design_method": str(raw.get("design_method") or "")[:120],
        "case_url": str(raw.get("case_url") or "")[:2000],
        "description": str(raw.get("description") or "")[:3900],
        "precondition": str(raw.get("precondition") or "")[:2000],
        "expected_result": str(raw.get("expected_result") or "")[:2000],
        "steps": steps,
    }
    if platform == "web" and base_url:
        enforce_base_url_on_draft(draft, base_url)
    return draft


def generate_design_drafts(
    requirements_text: str,
    profile: Optional[Dict[str, Any]],
    *,
    platform_type: str = "web",
    base_url: str = "",
    project_name: str = "",
    extra_context: str = "",
) -> Tuple[List[Dict[str, Any]], List[str], Optional[str]]:
    """
    返回 (drafts, warnings, error_message)。
    error_message 非空表示失败。
    """
    warns: List[str] = []
    text = (requirements_text or "").strip()
    if not text:
        return [], warns, "requirements_text 为空"

    prompt = build_design_preview_prompt(
        text, platform_type, base_url, project_name, extra_context
    )
    from ai_selector_recovery import _extract_json_obj
    from ai_local_inference import local_ai_service
    from ai_multi_provider import dispatch_chat

    try:
        raw = dispatch_chat(prompt, profile, local_ai_service)
    except Exception as e:
        uat_logger.warning("[AI_DESIGN] LLM failed: %s", e)
        return [], warns, str(e)

    data = _extract_json_obj(raw)
    if not isinstance(data, dict):
        return [], warns, "模型输出无法解析为 JSON，请重试或缩短需求"

    cases_raw = data.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        return [], warns, "模型未返回 cases 数组或为空，请补充更具体的需求"

    platform = (platform_type or "web").strip().lower()
    if platform == "mobile":
        platform = "android"
    bu = (base_url or "").strip()

    drafts: List[Dict[str, Any]] = []
    for item in cases_raw[: design_max_cases() + 5]:
        if not isinstance(item, dict):
            continue
        drafts.append(_normalize_draft(item, platform, bu))

    if not drafts:
        return [], warns, "未能解析出有效用例草案"

    if len(drafts) > design_max_cases():
        warns.append(f"草案超过上限，已截断至 {design_max_cases()} 条")
        drafts = drafts[: design_max_cases()]

    return drafts, warns, None


def save_design_drafts_to_project(
    db: Any,
    *,
    project_id: int,
    platform_type: str,
    drafts: List[Dict[str, Any]],
    user_id: int = 0,
    batch_id: str = "",
) -> Dict[str, Any]:
    """将选中的草案写入项目，返回 count / created_case_ids / warnings。"""
    from license_manager import license_manager, LicenseType

    platform = (platform_type or "web").strip().lower()
    if platform == "mobile":
        platform = "android"
    case_type = "api" if platform == "api" else "ui"

    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    created_ids: List[int] = []
    warnings: List[str] = []
    prefix = f"[AI-DESIGN:{batch_id}] " if batch_id else "[AI-DESIGN] "

    for draft in drafts:
        if not isinstance(draft, dict):
            continue
        name = (draft.get("case_name") or "AI 用例")[:200]
        role = _normalize_case_role(draft.get("case_role"))
        desc = prefix + f"[role:{role}] " + (draft.get("description") or "")[:3700]

        if limits.get("max_cases_per_project", -1) != -1:
            if db.get_project_case_count(project_id) >= limits["max_cases_per_project"]:
                warnings.append(f"已达项目用例上限，停止在 {len(created_ids)} 条")
                break

        if license_info.license_type == LicenseType.FREE.value:
            db.increment_created_cases(user_id)

        case_id = db.create_test_case_v2(
            project_id,
            name,
            draft.get("case_url", "") or "",
            desc,
            draft.get("precondition", "") or "",
            draft.get("expected_result", "") or "",
            case_type=case_type,
            case_role=role,
        )
        steps = draft.get("steps") or []
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            kwargs = _draft_step_to_db_kwargs(step, case_id, idx, platform)
            if kwargs:
                db.create_test_step(**kwargs)
        created_ids.append(case_id)

    return {
        "success": True,
        "created_case_ids": created_ids,
        "count": len(created_ids),
        "warnings": warnings,
    }


def _draft_step_to_db_kwargs(
    step: Dict[str, Any], case_id: int, step_order: int, platform: str
) -> Optional[Dict[str, Any]]:
    """将草案步骤转为 create_test_step 参数（避免 app 循环依赖时可内联）。"""
    action = (step.get("action") or "").strip().lower()
    if platform == "api":
        if action != "api_request":
            return None
        spec = step.get("api_spec")
        if isinstance(spec, dict):
            api_json = json.dumps(spec, ensure_ascii=False)
        else:
            api_json = str(spec or "").strip()
            if not api_json:
                api_json = json.dumps(
                    {
                        "method": "GET",
                        "url": "",
                        "headers": {},
                        "assertions": [{"type": "status_code", "expected": 200}],
                    },
                    ensure_ascii=False,
                )
        return {
            "case_id": case_id,
            "action": "api_request",
            "selector_type": "",
            "selector_value": "",
            "input_value": "",
            "description": (step.get("description") or "")[:2000],
            "step_order": step_order,
            "api_spec": api_json,
        }

    st = str(step.get("selector_type") or "")
    sv = str(step.get("selector_value") or "")
    iv = str(step.get("input_value") or "")
    url_col = ""
    if action == "navigate":
        url_col = iv or sv
        st, sv = "", ""
        iv = url_col
    layer = str(step.get("automation_layer") or "").lower()
    if platform == "android":
        layer = "android"
    elif not layer:
        layer = "web"
    kwargs: Dict[str, Any] = {
        "case_id": case_id,
        "action": action or "navigate",
        "selector_type": st,
        "selector_value": sv,
        "input_value": iv,
        "description": (step.get("description") or "")[:2000],
        "step_order": step_order,
        "url": url_col,
        "automation_layer": layer,
    }
    if action == "assert" and step.get("compare_type"):
        kwargs["compare_type"] = str(step.get("compare_type"))
    return kwargs
