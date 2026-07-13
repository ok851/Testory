"""
Multi-turn AI test chat with OpenAI-style tool calling: hermes_execute + refine_test_plan.

Enable with environment variable AI_CHAT_TOOLS_ENABLE=1.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ai_multi_provider import dispatch_chat_completion_messages
from logger import uat_logger
from embedded_browser_client import embedded_gateway_enabled
from agent_gateway_client import agent_tool_result_max_chars, get_agent_gateway_client
from hermes_config import hermes_cdp_attached


def ai_chat_tools_enabled() -> bool:
    return os.environ.get("AI_CHAT_TOOLS_ENABLE", "0").strip().lower() in ("1", "true", "yes", "on")


def profile_supports_ai_chat_tools(profile: Optional[Dict[str, Any]], legacy_model: str) -> bool:
    """Whether we attempt tool-loop (Ollama or OpenAI-compatible)."""
    if profile and isinstance(profile, dict):
        style = (profile.get("api_style") or "").strip()
        prov = (profile.get("provider") or "").strip()
        if style == "ollama" or prov == "ollama":
            return os.environ.get("AI_CHAT_TOOLS_OLLAMA_ENABLE", "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        if style == "anthropic_messages" or prov == "anthropic":
            return False
        if style == "google_gemini" or prov == "google_gemini":
            return False
        return bool(str(profile.get("api_key") or "").strip())
    return os.environ.get("AI_CHAT_TOOLS_OLLAMA_ENABLE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    ) and bool((legacy_model or "").strip())


def _max_tool_rounds() -> int:
    try:
        return max(1, min(32, int(os.environ.get("AI_CHAT_TOOLS_MAX_ROUNDS", "18"))))
    except ValueError:
        return 18


def _ai_allow_main_playwright_fallback() -> bool:
    return (os.environ.get("AI_ALLOW_MAIN_PLAYWRIGHT_FALLBACK") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def hermes_execute_allowed(*, embedded_session_id: str = "", platform_type: str = "web") -> bool:
    """
    Web：画布 CDP attach 后允许 hermes_execute（同一 Chromium）。
    Desktop：Hermes 已配置时允许 OS 层探索。
    """
    plat = (platform_type or "web").strip().lower()
    if plat == "desktop":
        from agent_gateway_client import agent_gateway_configured

        return agent_gateway_configured()
    if plat == "android":
        return False
    if not embedded_gateway_enabled():
        return True
    if _ai_allow_main_playwright_fallback():
        return True
    if hermes_cdp_attached():
        return True
    if (embedded_session_id or "").strip() and hermes_cdp_attached():
        return True
    return False


def openclaw_execute_allowed(*, embedded_session_id: str = "", platform_type: str = "web") -> bool:
    """Deprecated alias for hermes_execute_allowed."""
    return hermes_execute_allowed(embedded_session_id=embedded_session_id, platform_type=platform_type)


def _agent_execute_tool_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "hermes_execute",
            "description": (
                "通过内嵌 Hermes Agent 在真实浏览器中执行自然语言测试任务（可长链路、多页面、整模块）。"
                "适用于：探索系统、走通业务流程、收集页面 URL/标题/控件线索，供后续写入用例步骤或 Skill。"
                "复杂任务可多次调用：先登录与主干路径，再按模块分次探索；用 continuation_from 衔接上下文。"
                "调用后根据返回文本整理出具体 navigate/click/input/wait/assert 步骤，必要时再调用 refine_test_plan。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "主任务说明：目标系统/模块、要走的流程、注意事项（尽量完整）",
                    },
                    "scope": {
                        "type": "string",
                        "description": "可选：smoke | module | e2e | explore | regression | integration",
                    },
                    "environment_notes": {
                        "type": "string",
                        "description": "可选：基础 URL、账号、环境、测试数据前提、禁用项等",
                    },
                    "acceptance_criteria": {
                        "type": "string",
                        "description": "可选：验收/检查点，分号或换行分隔",
                    },
                    "continuation_from": {
                        "type": "string",
                        "description": "可选：上次执行摘要或待继续的子任务",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "可选，Agent 侧会话标识",
                    },
                },
                "required": ["instruction"],
            },
        },
    }


def chat_tool_schemas(*, allow_openclaw: bool = True, allow_hermes: Optional[bool] = None) -> List[Dict[str, Any]]:
    allow = allow_hermes if allow_hermes is not None else allow_openclaw
    schemas: List[Dict[str, Any]] = []
    if allow:
        schemas.append(_agent_execute_tool_schema())
    schemas.append(
        {
            "type": "function",
            "function": {
                "name": "refine_test_plan",
                "description": (
                    "根据自然语言调整当前 AI 自动化测试用例计划（JSON steps）。"
                    "在需要增删改步骤、修正选择器或断言时调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "adjustment": {
                            "type": "string",
                            "description": "要如何修改用例的说明（中文或英文均可）",
                        }
                    },
                    "required": ["adjustment"],
                },
            },
        }
    )
    return schemas


def _build_system_prompt(
    *,
    project_name: str,
    current_plan: Dict[str, Any],
    page_snapshot: str,
    dom_pack: str,
    memory_context: str,
    interaction_note: str,
    test_scope: str,
    embedded_session_id: str = "",
    platform_type: str = "web",
) -> str:
    plan_preview = json.dumps(current_plan or {}, ensure_ascii=False)
    if len(plan_preview) > 12000:
        plan_preview = plan_preview[:11999] + "…"
    snap = (page_snapshot or "").strip()
    if len(snap) > 6000:
        snap = snap[:5999] + "…"
    dom = (dom_pack or "").strip()
    if len(dom) > 6000:
        dom = dom[:5999] + "…"
    mem = (memory_context or "").strip()
    if len(mem) > 4000:
        mem = mem[:3999] + "…"
    parts = [
        "你是资深 QA / 自动化架构师，负责对话式维护并扩展 AI 自动化测试用例计划。",
        "",
        "## Hermes Agent 与多轮工具",
    ]
    plat = (platform_type or "web").strip().lower()
    if plat == "desktop":
        parts_agent = [
            "【重要】当前为 **Windows 桌面** 测试场景，无需 URL。",
            "可调用 hermes_execute 让 Hermes 在本机操作系统层探索（启动应用、点按窗口、输入文字等）；",
            "完成后用 refine_test_plan 写入 automation_layer=desktop 的可复现步骤。",
            "步骤应使用 launch_app / attach_window / click / input 等桌面动作，不要使用 navigate。",
        ]
    elif embedded_gateway_enabled() and not _ai_allow_main_playwright_fallback():
        if hermes_cdp_attached():
            parts_agent = [
                "【重要】平台已连接内置画布 Chromium（CDP attach）。浏览器操作应优先调用 hermes_execute，",
                "Hermes 将在与中栏实时画面**同一浏览器**中自主 navigate/click/input/snapshot。",
                "执行完成后根据返回摘要调用 refine_test_plan 写入可复现 steps；仅改 JSON/选择器时可只调用 refine_test_plan。",
            ]
        else:
            parts_agent = [
                "【重要】平台已启用内置浏览器运行时，但 Hermes 尚未 attach 到画布 CDP。",
                "请先确保 AI 测试页已连接实时画面，再调用 hermes_execute；当前请通过 refine_test_plan 写入 steps 由平台执行。",
                "当仅改 JSON、选择器或断言时，只调用 refine_test_plan。",
            ]
    elif (embedded_session_id or "").strip() and embedded_gateway_enabled():
        if hermes_cdp_attached():
            parts_agent = [
                "【重要】用户已连接内置 AI 画布且 Hermes CDP 已 attach。可调用 hermes_execute 在同一 Chromium 中探索；",
                "完成后用 refine_test_plan 固化步骤。仅改 JSON 时可只调用 refine_test_plan。",
            ]
        else:
            parts_agent = [
                "【重要】用户已连接内置 AI 画布（browser runtime 会话）。浏览器操作必须只在该画布 Chromium 中通过 steps 体现；",
                "由平台在画布执行。禁止调用 hermes_execute（CDP 未同步，会另开独立浏览器窗口）。",
                "请根据用户指令与 LIVE 快照调用 refine_test_plan 增删改 steps。",
            ]
    else:
        parts_agent = [
            "当用户要「在真实浏览器里跑」「探索系统/模块」「走通流程」「验证一整条业务」时，可调用 hermes_execute。",
            "hermes_execute 可把 scope / environment_notes / acceptance_criteria / continuation_from 与 instruction 组合成长指令；"
            "对大系统请分多轮调用。拿到 Agent 文本结果后提炼选择器、URL、断言文案；必要时调用 refine_test_plan 合并。",
            "当仅改 JSON 步骤、选择器或断言、且无需浏览器时，可只调用 refine_test_plan。",
        ]
    parts.extend(parts_agent)
    if plat != "desktop":
        parts.extend([
            "",
            "## 输出用例质量",
            "最终必须输出且仅输出一个 JSON 对象（不要用 markdown 代码块），字段 case_name, case_url, description, precondition, expected_result, steps（与平台 runner 一致）。",
            "整系统/模块任务时：steps 应覆盖完整流程（含 navigate、必要 wait、click/input、关键 assert），步数可较多；避免只给 3～5 步骨架。",
            "若 LIVE 快照存在：步骤应优先 probe_index 或快照中的 recommended 选择器，勿臆造 class。",
        ])
    else:
        parts.extend([
            "",
            "## 输出用例质量（Windows 桌面）",
            "最终必须输出且仅输出一个 JSON 对象。case_url 留空。每步 automation_layer=desktop。",
            "禁止 navigate/css/xpath。首步 launch_app 或 attach_window；窗口校验用 selector_type=window。",
        ])
    parts.extend([
        "",
        f"项目名: {project_name or 'unknown'}",
        f"当前计划 JSON:\n{plan_preview}",
    ])
    ts = (test_scope or "").strip()
    if ts:
        parts.append(f"【用户指定的测试范围/模块】（须在步骤与描述中落实）: {ts}")
    if interaction_note:
        parts.append(f"交互上下文: {interaction_note}")
    if mem:
        parts.append(f"检索记忆:\n{mem}")
    if snap:
        snap_label = "桌面窗口快照（优先引用 title/hwnd）" if plat == "desktop" else "LIVE 页面快照（优先使用其中定位）"
        parts.append(f"{snap_label}:\n{snap}")
    if dom:
        parts.append(f"DOM 摘要:\n{dom}")
    return "\n\n".join(parts)


def _history_to_messages(history: Any, sanitizer: Callable[[Any], List[Dict[str, str]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(history, list):
        return out
    clean = sanitizer(history)
    for item in clean:
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or "user").strip()
        if role not in ("user", "assistant"):
            role = "user"
        content = (item.get("content") or "").strip()
        if not content:
            continue
        out.append({"role": role, "content": content})
    return out


def _parse_tool_arguments(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {}


def _compose_agent_instruction(args: Dict[str, Any]) -> str:
    base = (args.get("instruction") or "").strip()
    blocks: List[str] = []
    scope = (args.get("scope") or "").strip().lower()
    if scope in ("smoke", "module", "e2e", "explore", "regression", "integration"):
        scope_cn = {
            "smoke": "冒烟（关键路径快速验证）",
            "module": "单模块/功能域深度验证",
            "e2e": "端到端跨多页业务流程",
            "explore": "探索式测试（发现异常与边界）",
            "regression": "回归（对照既有行为）",
            "integration": "集成（多子系统衔接）",
        }.get(scope, scope)
        blocks.append(
            f"【测试范围】{scope_cn}（{scope}）。请按该范围在浏览器中自主完成导航、操作与验证；"
            "输出须包含：访问过的 URL、关键页面标题、执行过的主要操作、发现的 DOM/文案线索、失败或风险点。"
        )
    env_notes = (args.get("environment_notes") or "").strip()
    if env_notes:
        blocks.append("【环境与数据前提】\n" + env_notes)
    ac = (args.get("acceptance_criteria") or "").strip()
    if ac:
        blocks.append("【必须验证的检查点】（逐条尝试并在输出中写明每条通过/失败/跳过原因）\n" + ac)
    cont = (args.get("continuation_from") or "").strip()
    if cont:
        blocks.append("【承接上次执行】（在同一浏览器会话逻辑下继续，不要重复已确认无问题的步骤）\n" + cont)
    if not base and blocks:
        return "\n\n".join(blocks)
    if blocks:
        return "\n\n".join(blocks) + "\n\n【主任务说明】\n" + base
    return base


_compose_openclaw_instruction = _compose_agent_instruction


@dataclass
class ChatToolLoopParams:
    message: str
    project_name: str
    current_plan: Dict[str, Any]
    history: Any
    profile: Optional[Dict[str, Any]]
    legacy_model: str
    page_snapshot: Optional[str]
    probe_registry: Any
    probe_url: Optional[str]
    memory_context: Optional[str]
    dom_context_pack: Optional[str]
    interaction_context: Optional[Dict[str, Any]]
    test_scope: Optional[str] = None
    embedded_session_id: Optional[str] = None
    platform_type: str = "web"


def _handle_agent_execute(
    *,
    name: str,
    args: Dict[str, Any],
    allow_agent: bool,
    agent_client: Any,
    meta: Dict[str, Any],
) -> str:
    tool_key = "hermes_execute" if name == "hermes_execute" else "openclaw_execute"
    if not allow_agent:
        meta["tools_used"].append(f"{tool_key}_blocked")
        err_msg = (
            f"{tool_key} 已禁用：画布 CDP 未 attach 到 Hermes。"
            "请先在 AI 测试页连接实时画面，或改用 refine_test_plan。"
        )
        return json.dumps(
            {
                "ok": False,
                "error": err_msg,
            },
            ensure_ascii=False,
        )
    instr = _compose_agent_instruction(args)
    sid = (args.get("session_id") or "").strip()
    if not instr.strip():
        meta["tools_used"].append(tool_key)
        return json.dumps(
            {"ok": False, "error": "instruction 经拼装后仍为空；请填写主任务或 environment_notes/scope"},
            ensure_ascii=False,
        )
    result_text = agent_client.execute_user_instruction(instr, sid)
    meta["tools_used"].append(tool_key)
    return result_text


def run_ai_chat_with_tools(
    *,
    local_ai_service: Any,
    params: ChatToolLoopParams,
) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    """
    Returns (generated_plan_dict, norm_warnings from caller side still empty here, meta).

    Caller should run apply_step_normalization_to_plan on generated_plan_dict.
    """
    embed_sid = (params.embedded_session_id or "").strip()
    plat = (params.platform_type or "web").strip().lower()
    allow_agent = hermes_execute_allowed(embedded_session_id=embed_sid, platform_type=plat)
    tools = chat_tool_schemas(allow_hermes=allow_agent)
    agent_client = get_agent_gateway_client()

    ic_note = ""
    if params.interaction_context:
        try:
            ic_note = json.dumps(params.interaction_context, ensure_ascii=False)[:2000]
        except Exception:
            ic_note = str(params.interaction_context)[:2000]

    system_prompt = _build_system_prompt(
        project_name=params.project_name,
        current_plan=params.current_plan if isinstance(params.current_plan, dict) else {},
        page_snapshot=params.page_snapshot or "",
        dom_pack=params.dom_context_pack or "",
        memory_context=params.memory_context or "",
        interaction_note=ic_note,
        test_scope=(params.test_scope or "").strip() if params.test_scope else "",
        embedded_session_id=embed_sid,
        platform_type=plat,
    )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(
        _history_to_messages(params.history, local_ai_service._sanitize_chat_history_for_prompt)
    )
    messages.append({"role": "user", "content": params.message})

    last_plan: Dict[str, Any] = dict(params.current_plan) if isinstance(params.current_plan, dict) else {}
    meta: Dict[str, Any] = {"tool_rounds": 0, "tools_used": []}
    prof: Optional[Dict[str, Any]] = params.profile if isinstance(params.profile, dict) else None
    max_result = agent_tool_result_max_chars()

    for round_idx in range(_max_tool_rounds()):
        if prof:
            assistant_msg = dispatch_chat_completion_messages(
                messages,
                tools,
                prof,
                local_ai_service,
                temperature=0.2,
            )
        else:
            default_model = os.environ.get("LOCAL_LLM_MODEL_MID", "llama3:8b-instruct")
            default_profile = {
                "provider": "ollama",
                "api_style": "ollama",
                "model_id": params.legacy_model or default_model,
                "api_key": "",
                "base_url": "",
            }
            assistant_msg = dispatch_chat_completion_messages(
                messages,
                tools,
                default_profile,
                local_ai_service,
                temperature=0.2,
            )

        tool_calls = assistant_msg.get("tool_calls")
        content = assistant_msg.get("content")

        if not tool_calls:
            text = (content or "").strip()
            if not text:
                raise ValueError("模型返回空内容")
            try:
                parsed = local_ai_service._parse_json_response(text)
                using_model = (params.legacy_model or local_ai_service.model_mid).strip()
                if prof:
                    using_model = (
                        (prof.get("label") or prof.get("model_id") or using_model) or using_model
                    ).strip()
                normalized = local_ai_service._normalize_output(
                    parsed,
                    params.message,
                    params.project_name,
                    using_model,
                    probe_registry=params.probe_registry,
                )
                meta["final_round"] = round_idx
                return normalized, [], meta
            except ValueError:
                uat_logger.info("AI chat tools: final message not JSON, falling back to refine once")
                refined = local_ai_service.refine_case_and_steps(
                    user_message=params.message,
                    project_name=params.project_name,
                    current_plan=last_plan,
                    history=params.history if isinstance(params.history, list) else [],
                    model=params.legacy_model,
                    profile=prof,
                    page_snapshot=params.page_snapshot,
                    probe_registry=params.probe_registry,
                    probe_url=params.probe_url,
                    memory_context=params.memory_context,
                    dom_context_pack=params.dom_context_pack,
                    interaction_context=params.interaction_context,
                )
                meta["fallback"] = "refine_after_non_json"
                return refined, [], meta

        meta["tool_rounds"] = int(meta["tool_rounds"]) + 1

        assistant_record: Dict[str, Any] = {
            "role": "assistant",
            "content": content if content else None,
            "tool_calls": tool_calls,
        }
        messages.append(assistant_record)

        if not isinstance(tool_calls, list):
            tool_calls = []

        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = (fn.get("name") or "").strip()
            tid = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            raw_args = fn.get("arguments") if isinstance(fn, dict) else ""
            if not isinstance(raw_args, str):
                raw_args = json.dumps(raw_args, ensure_ascii=False) if raw_args is not None else ""
            args = _parse_tool_arguments(raw_args)
            result_text = ""

            if name in ("hermes_execute", "openclaw_execute"):
                result_text = _handle_agent_execute(
                    name=name,
                    args=args,
                    allow_agent=allow_agent,
                    agent_client=agent_client,
                    meta=meta,
                )
            elif name == "refine_test_plan":
                adj = (args.get("adjustment") or "").strip()
                if not adj:
                    result_text = json.dumps({"ok": False, "error": "adjustment 为空"}, ensure_ascii=False)
                else:
                    refined = local_ai_service.refine_case_and_steps(
                        user_message=adj,
                        project_name=params.project_name,
                        current_plan=last_plan,
                        history=params.history if isinstance(params.history, list) else [],
                        model=params.legacy_model,
                        profile=prof,
                        page_snapshot=params.page_snapshot,
                        probe_registry=params.probe_registry,
                        probe_url=params.probe_url,
                        memory_context=params.memory_context,
                        dom_context_pack=params.dom_context_pack,
                        interaction_context=params.interaction_context,
                    )
                    last_plan = refined
                    result_text = json.dumps(
                        {"ok": True, "plan": refined, "hint": "已更新 current_plan，请在最终回复输出完整 JSON 用例"},
                        ensure_ascii=False,
                    )[: min(96000, max_result)]
                meta["tools_used"].append("refine_test_plan")
            else:
                result_text = json.dumps({"ok": False, "error": f"未知工具 {name}"}, ensure_ascii=False)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": result_text,
                }
            )

    raise ValueError(f"工具调用轮数超过上限（{_max_tool_rounds()}），请缩短任务或提高 AI_CHAT_TOOLS_MAX_ROUNDS")
