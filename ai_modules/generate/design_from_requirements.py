# -*- coding: utf-8 -*-
"""AI 用例设计：从需求一次生成多条测试草案（预览，不落库）。"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from modules.core.logger import uat_logger

DESIGN_SCHEMA_HINT = """{
  "cases": [
    {
      "case_name": "string",
      "case_role": "string (根据需求自行归纳，如 business / smoke / regression / edge_case / security 等；仅当需求明确提到登录/认证时才使用 login_feature)",
      "design_method": "string (e.g. 等价类划分, 边界值, 场景法, 错误推测)",
      "case_url": "string (Web 可选；Desktop/Android/OS 可留空)",
      "description": "string",
      "precondition": "string",
      "expected_result": "string",
      "steps": [
        {
          "action": "navigate|click|input|wait|verify|assert|extract_text|api_request|open_app|tap|input_text|launch_app|type_keys",
          "selector_type": "css|xpath|text|accessibility_id|id|name|automation_id|...",
          "selector_value": "string (必须使用页面控件清单中的真实值，禁止编造)",
          "input_value": "string (assert时为预期值，navigate时为URL，input时为输入内容)",
          "description": "string",
          "compare_type": "string (assert 必填：text_contains|text_equals|url_contains|url_equals|element_visible|element_exists)",
          "probe_index": "integer (页面控件清单中 [n] 的 n 值；无对应控件时留空)",
          "automation_layer": "web|android|desktop (optional)",
          "desktop_spec": { "path": "", "alias": "", "args": [] },
          "mobile_spec": { "package": "", "activity": "" },
          "api_spec": { "method": "GET", "url": "", "headers": {}, "body": null, "assertions": [] }
        }
      ]
    }
  ]
}"""


def design_max_cases() -> int:
    try:
        n = int(os.environ.get("AI_DESIGN_MAX_CASES", "120") or "120")
    except ValueError:
        n = 10
    return max(5, min(n, 120))


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
    entry_target: str = "",
    page_snapshot: str = "",
) -> str:
    from ai_modules.generate.input_classify import normalize_platform

    platform = normalize_platform(platform_type)
    max_cases = design_max_cases()
    # entry_target 优先；兼容旧字段 base_url
    entry = (entry_target or base_url or "").strip()
    body = (requirements_text or "").strip()
    cap = design_requirements_max_chars()
    if len(body) > cap:
        body = body[: cap - 80] + "\n…(truncated)…"

    platform_rules = {
        "web": (
            "平台：Web UI 自动化。步骤 automation_layer=web；action 使用 navigate/click/input/wait/assert。\n"
            "入口：仅当用户提供了目标 URL / 路由时才写 navigate；**不要编造 URL**；无 URL 时从业务操作步骤开始，"
            "在 precondition 写明「已打开目标页」。\n"
            "定位优先 data-testid / role+name / aria-label，禁止脆弱 XPath 与随机 class。\n"
            "case_role 根据需求自行归纳；仅当需求明确提到登录/认证时才生成 login_feature 类用例，否则一律从 business 角度设计。\n"
        ),
        "api": (
            "平台：接口测试。每条用例 steps 仅含 action=api_request，api_spec 含 method/url/headers/body/assertions。\n"
            "若用户给了 API Base，相对路径可拼接；**禁止编造未出现的域名**。\n"
            "case_role 根据需求自行归纳；仅当需求明确提到登录接口时才生成 login_feature 类用例。\n"
        ),
        "android": (
            "平台：Android / 移动端。步骤 automation_layer=android；action 使用 open_app/tap/input_text/wait/assert。\n"
            "**不要使用 Web 的 navigate/URL**。入口用 open_app + mobile_spec.package（若用户提供了包名）。\n"
            "定位优先 accessibility_id / resource-id / content-desc / 可见文案；禁止臆造控件 id。\n"
            "case_role 根据需求自行归纳；仅当需求明确提到登录/认证时才生成 login_feature 类用例，否则从 business 角度设计。\n"
        ),
        "desktop": (
            "平台：Windows 桌面应用（UIA）。步骤 automation_layer=desktop；\n"
            "action 优先：launch_app（可带 desktop_spec.path 或 alias）、click、input/type_keys、wait、assert。\n"
            "**禁止 Web navigate / 浏览器 URL**。入口用用户提供的应用别名/路径/窗口标题；未提供则在 precondition 写「应用已启动」。\n"
            "定位优先 Name / AutomationId / ControlType；禁止屏幕坐标作为主路径。\n"
        ),
        "os": (
            "平台：操作系统级场景（Windows）。步骤仍走 automation_layer=desktop（系统窗口/设置/进程相关 UI），\n"
            "或描述可验证的系统状态断言（服务、进程、文件路径出现在 description/expected_result）。\n"
            "**禁止编造 URL**。若涉及桌面设置/控制面板窗口，用 UIA 定位；不要生成纯 Web 步骤。\n"
            "每条用例职责单一：如「启动服务并验证窗口」「打开系统设置某页并断言」。\n"
        ),
    }
    rules = platform_rules.get(platform, platform_rules["web"])

    entry_block = ""
    if entry:
        if platform == "web":
            entry_block = (
                f"\n用户指定的 Web 入口 URL：{entry}\n"
                "所有 navigate 必须使用该 URL 或其同主机路径；case_url 同步。禁止改用其他域名。\n"
            )
        elif platform == "api":
            entry_block = (
                f"\n用户指定的 API Base：{entry}\n"
                "api_spec.url 若为相对路径则拼接该 Base；禁止换成未给出的主机。\n"
            )
        elif platform == "android":
            entry_block = (
                f"\n用户指定的 App 入口：{entry}\n"
                "open_app / mobile_spec 使用该包名或 Activity；不要编造其他包名。\n"
            )
        elif platform == "desktop":
            entry_block = (
                f"\n用户指定的桌面应用入口：{entry}\n"
                "launch_app 的 desktop_spec.alias 或 path / 窗口标题使用该值；不要编造路径。\n"
            )
        elif platform == "os":
            entry_block = (
                f"\n用户指定的系统场景入口：{entry}\n"
                "步骤与断言围绕该入口（服务/进程/路径/窗口）；不要引入无关系统区域。\n"
            )
    else:
        entry_block = (
            "\n用户未提供入口（URL/包名/应用路径均可空）。"
            "不要编造入口；在 precondition 说明前置环境即可。\n"
        )

    return (
        "你是资深测试设计师。根据需求从多种测试设计方法生成可执行的测试用例草案。\n"
        "方法包括但不限于：等价类划分、边界值分析、判断表法、场景法、错误推测法。\n"
        f"充分覆盖需求中的功能点、边界条件与异常场景，产出尽量详尽的 cases（目标不少于 {max_cases} 条，至少 5 条），每条标注 design_method 与 case_role。\n"
        f"{rules}"
        f"{entry_block}"
        + (f"\n\n【页面实际控件清单】\n以下是目标页面的真实可交互控件，每行 [n] 为 n_index：\n{page_snapshot.strip()[:12000]}\n\nsteps 中的 selector_type/selector_value 必须使用上述清单中的真实值，禁止自行编造。若清单中无法找到对应控件，在 precondition 中说明。\n\n" if (page_snapshot or "").strip() else "")
        + "Steps 字段规则：\n"
        + "- navigate：input_value 必须填写完整 URL；selector_type 和 selector_value 必须为空字符串。\n"
        + "- click：selector_value 必须是控件清单中的真实值；input_value 通常为空。\n"
        + "- input：input_value 必须填写要输入的内容（留空时也必须填 \"\"）。\n"
        + "- assert：compare_type 必须填写（text_contains / text_equals / url_contains / element_visible 等）；input_value 必须填写预期值（如预期包含的文本），禁止为空。\n"
        + "- 每条 Web 用例应以 navigate 开头（如有 URL），确保执行时在正确页面。\n"
        + "\n"
        + "严格约束：\n"
        "- 仅生成需求正文中明确提到或可合理推导的功能场景，禁止捏造需求未提及的功能（如需求无登录则不得生成登录用例）。\n"
        "- case_role 根据实际场景归纳，不得默认套用 login_feature；仅当需求明确提到登录/认证时才使用。\n"
        "- 每条用例的 steps 必须可执行、可验证；禁止空洞描述或与需求无关的步骤。\n"
        "- 数据规则：账号、密码、入口标识仅使用需求正文或用户指定入口中明确给出的值，禁止编造。\n"
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
    if r in ("business", "smoke", "regression", "edge_case", "security",
             "boundary", "negative", "exploratory", "integration", "e2e"):
        return r
    return "business"


def _normalize_draft(
    raw: Dict[str, Any],
    platform_type: str,
    base_url: str,
    entry_target: str = "",
) -> Dict[str, Any]:
    from ai_modules.generate.input_classify import normalize_platform

    platform = normalize_platform(platform_type)
    entry = (entry_target or base_url or "").strip()
    steps_in = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    steps: List[Dict[str, Any]] = []
    for st in steps_in:
        if not isinstance(st, dict):
            continue
        action = (st.get("action") or "").strip().lower()
        if platform == "api" and action != "api_request":
            continue
        # 非 Web：丢掉误生成的 navigate 到 http URL
        if platform in ("desktop", "os", "android") and action == "navigate":
            iv = str(st.get("input_value") or "")
            if iv.startswith("http://") or iv.startswith("https://"):
                continue
        row = {
            "action": action or ("launch_app" if platform in ("desktop", "os") else "navigate"),
            "selector_type": str(st.get("selector_type") or ""),
            "selector_value": str(st.get("selector_value") or ""),
            "input_value": str(st.get("input_value") or ""),
            "description": str(st.get("description") or ""),
        }
        pi_raw = st.get("probe_index")
        if pi_raw not in (None, "", 0, "0"):
            try:
                row["probe_index"] = int(pi_raw)
            except (ValueError, TypeError):
                pass
        if st.get("compare_type"):
            row["compare_type"] = str(st.get("compare_type"))
        layer = str(st.get("automation_layer") or "").strip().lower()
        if platform == "android":
            layer = "android"
        elif platform in ("desktop", "os"):
            layer = "desktop"
        elif platform == "web" and not layer:
            layer = "web"
        if layer:
            row["automation_layer"] = layer
        if st.get("api_spec") is not None:
            row["api_spec"] = st.get("api_spec")
        if st.get("desktop_spec") is not None:
            row["desktop_spec"] = st.get("desktop_spec")
        if st.get("mobile_spec") is not None:
            row["mobile_spec"] = st.get("mobile_spec")
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
    if platform == "web" and entry:
        enforce_base_url_on_draft(draft, entry)
    elif platform != "web":
        # 非 Web 不强制 case_url
        if (draft.get("case_url") or "").startswith(("http://", "https://")) and platform != "api":
            draft["case_url"] = ""
        if platform == "desktop" and entry and steps:
            _ensure_desktop_launch(draft, entry)
        if platform == "android" and entry and steps:
            _ensure_android_open(draft, entry)
    return draft


def _ensure_desktop_launch(draft: Dict[str, Any], entry: str) -> None:
    steps = draft.get("steps") if isinstance(draft.get("steps"), list) else []
    if not steps:
        return
    first = steps[0] if isinstance(steps[0], dict) else {}
    if (first.get("action") or "").lower() == "launch_app":
        return
    spec: Dict[str, Any] = {}
    e = (entry or "").strip()
    if e.startswith("@"):
        spec["alias"] = e
    elif e.lower().endswith((".exe", ".bat", ".cmd")) or "\\" in e or "/" in e:
        spec["path"] = e
    else:
        spec["alias"] = e if e.startswith("@") else e
    steps.insert(
        0,
        {
            "action": "launch_app",
            "selector_type": "",
            "selector_value": "",
            "input_value": e,
            "description": f"启动应用 {e}",
            "automation_layer": "desktop",
            "desktop_spec": spec,
        },
    )
    draft["steps"] = steps


def _ensure_android_open(draft: Dict[str, Any], entry: str) -> None:
    steps = draft.get("steps") if isinstance(draft.get("steps"), list) else []
    if not steps:
        return
    first = steps[0] if isinstance(steps[0], dict) else {}
    if (first.get("action") or "").lower() in ("open_app", "launch_app"):
        return
    steps.insert(
        0,
        {
            "action": "open_app",
            "selector_type": "",
            "selector_value": "",
            "input_value": entry,
            "description": f"打开应用 {entry}",
            "automation_layer": "android",
            "mobile_spec": {"package": entry},
        },
    )
    draft["steps"] = steps


def generate_design_drafts(
    requirements_text: str,
    profile: Optional[Dict[str, Any]],
    *,
    platform_type: str = "web",
    base_url: str = "",
    project_name: str = "",
    extra_context: str = "",
    entry_target: str = "",
) -> Tuple[List[Dict[str, Any]], List[str], Optional[str]]:
    """
    返回 (drafts, warnings, error_message)。
    error_message 非空表示失败。
    """
    from ai_modules.generate.input_classify import normalize_platform

    warns: List[str] = []
    text = (requirements_text or "").strip()
    if not text:
        return [], warns, "requirements_text 为空"

    entry = (entry_target or base_url or "").strip()
    platform = normalize_platform(platform_type)

    # 探测目标页面，获取真实 DOM 数据（仅 web 平台有 URL 时）
    page_snapshot = ""
    probe_registry: list = []
    if entry and platform == "web":
        try:
            from modules.ai.ai_page_probe import collect_page_controls
            uat_logger.info("[AI_DESIGN] Probing page: %s", entry)
            page_snapshot, probe_err, probe_registry = collect_page_controls(entry)
            if probe_err:
                warns.append(f"页面探测警告：{probe_err}")
            elif page_snapshot:
                uat_logger.info("[AI_DESIGN] Page probed OK, %d controls found", len(probe_registry))
        except Exception as e:
            warns.append(f"页面探测失败：{e}")

    prompt = build_design_preview_prompt(
        text,
        platform_type,
        base_url=entry,
        project_name=project_name,
        extra_context=extra_context,
        entry_target=entry,
        page_snapshot=page_snapshot,
    )
    from modules.ai.ai_selector_recovery import _extract_json_obj
    from modules.ai.ai_local_inference import local_ai_service
    from modules.ai.ai_multi_provider import dispatch_chat

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

    platform = normalize_platform(platform_type)

    drafts: List[Dict[str, Any]] = []
    for item in cases_raw[: design_max_cases() + 5]:
        if not isinstance(item, dict):
            continue
        drafts.append(_normalize_draft(item, platform, entry, entry_target=entry))

    if not drafts:
        return [], warns, "未能解析出有效用例草案"

    # 解析 probe_index → 真实 selector（仅 web 有探测数据时）
    if probe_registry and platform == "web":
        try:
            from modules.ai.ai_local_inference import clamp_plan_steps_to_probe_registry
            _tech_warns = []
            for d in drafts:
                steps = d.get("steps") or []
                if steps:
                    clamp_warnings = clamp_plan_steps_to_probe_registry(steps, probe_registry)
                    _tech_warns.extend(clamp_warnings)
            if _tech_warns:
                uat_logger.info("[AI_DESIGN] probe_index clamp: %d warnings suppressed", len(_tech_warns))
        except Exception as e:
            uat_logger.warning("[AI_DESIGN] probe_index resolution failed: %s", e)

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
    from ai_modules.generate.input_classify import normalize_platform
    from modules.auth.license_manager import license_manager, LicenseType

    platform = normalize_platform(platform_type)
    case_type = "api" if platform == "api" else "ui"

    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    created_ids: List[int] = []
    warnings: List[str] = []
    prefix = f"[AI-DESIGN:{batch_id}] " if batch_id else "[AI-DESIGN] "

    # 对 web 平台草案做断言回放修正（填充交互后才出现的文本）
    if platform == "web":
        try:
            from modules.ai.ai_page_probe import ground_plan_assertions_with_replay
            _has_assert = any(
                isinstance(d, dict) and any(
                    isinstance(s, dict) and str(s.get("action") or "").strip().lower() == "assert"
                    for s in (d.get("steps") or [])
                )
                for d in drafts
            )
            if _has_assert:
                _url = ""
                for d in drafts:
                    if isinstance(d, dict) and d.get("case_url"):
                        _url = str(d["case_url"]).strip()
                        break
                if _url:
                    uat_logger.info("[AI_DESIGN] Grounding assertions for %d drafts via replay", len(drafts))
                    for d in drafts:
                        if not isinstance(d, dict):
                            continue
                        steps = d.get("steps") or []
                        if steps:
                            grounded_steps, gw, gerr = ground_plan_assertions_with_replay(_url, steps)
                            if grounded_steps:
                                d["steps"] = grounded_steps
                            if gw:
                                warnings.extend(gw)
                            if gerr:
                                uat_logger.warning("[AI_DESIGN] Assert grounding error: %s", gerr)
        except Exception as e:
            uat_logger.warning("[AI_DESIGN] Assert grounding skipped: %s", e)

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
            generated_by_ai=True,
            review_status=str(draft.get("review_status") or "active"),
            source_commit=str(draft.get("source_commit") or "")[:64],
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
    elif platform in ("desktop", "os"):
        layer = "desktop"
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
    if step.get("desktop_spec") is not None:
        import json as _json

        ds = step.get("desktop_spec")
        kwargs["desktop_spec"] = (
            _json.dumps(ds, ensure_ascii=False) if isinstance(ds, dict) else str(ds or "")
        )
    if step.get("mobile_spec") is not None:
        import json as _json

        ms = step.get("mobile_spec")
        kwargs["mobile_spec"] = (
            _json.dumps(ms, ensure_ascii=False) if isinstance(ms, dict) else str(ms or "")
        )
    return kwargs
