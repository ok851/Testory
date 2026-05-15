"""
Multi-turn AI test chat with OpenAI-style tool calling: openclaw_execute + refine_test_plan.

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
from openclaw_gateway_client import OpenClawGatewayClient, _tool_result_max_chars


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


def chat_tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "openclaw_execute",
                "description": (
                    "通过 OpenClaw Gateway 在真实浏览器中执行自然语言测试任务（可长链路、多页面、整模块）。"
                    "适用于：探索系统、走通业务流程、收集页面 URL/标题/控件线索，供后续写入用例步骤。"
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
                            "description": "可选：smoke | module | e2e | explore | regression | integration，影响探索深度与输出结构",
                        },
                        "environment_notes": {
                            "type": "string",
                            "description": "可选：基础 URL、账号、环境、测试数据前提、禁用项等",
                        },
                        "acceptance_criteria": {
                            "type": "string",
                            "description": "可选：验收/检查点，分号或换行分隔；要求 OpenClaw 输出中逐条回应",
                        },
                        "continuation_from": {
                            "type": "string",
                            "description": "可选：上次执行摘要或待继续的子任务，避免重复劳动",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "可选，OpenClaw 侧会话标识",
                        },
                    },
                    "required": ["instruction"],
                },
            },
        },
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
        },
    ]


def _build_system_prompt(
    *,
    project_name: str,
    current_plan: Dict[str, Any],
    page_snapshot: str,
    dom_pack: str,
    memory_context: str,
    interaction_note: str,
    test_scope: str,
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
        "## OpenClaw 与多轮工具",
        "当用户要「在真实浏览器里跑」「探索系统/模块」「走通流程」「验证一整条业务」时，必须调用 openclaw_execute。",
        "openclaw_execute 可把 scope / environment_notes / acceptance_criteria / continuation_from 与 instruction 组合成一条长指令；"
        "对大系统请分多轮调用：例如 (1) 登录与首页 (2) 核心业务模块A (3) 模块B 或报表/导出等，每轮用 continuation_from 摘要上一轮。",
        "拿到 OpenClaw 文本结果后：提炼可落地的选择器、URL、断言文案；若当前计划与探索结果不一致，调用 refine_test_plan 合并。",
        "当仅改 JSON 步骤、选择器或断言、且无需浏览器时，可只调用 refine_test_plan。",
        "",
        "## 输出用例质量",
        "最终必须输出且仅输出一个 JSON 对象（不要用 markdown 代码块），字段 case_name, case_url, description, precondition, expected_result, steps（与平台 runner 一致）。",
        "整系统/模块任务时：steps 应覆盖完整流程（含 navigate、必要 wait、click/input、关键 assert），步数可较多；避免只给 3～5 步骨架。",
        "若 LIVE 快照存在：步骤应优先 probe_index 或快照中的 recommended 选择器，勿臆造 class。",
        "",
        f"项目名: {project_name or 'unknown'}",
        f"当前计划 JSON:\n{plan_preview}",
    ]
    ts = (test_scope or "").strip()
    if ts:
        parts.append(f"【用户指定的测试范围/模块】（须在步骤与描述中落实）: {ts}")
    if interaction_note:
        parts.append(f"交互上下文: {interaction_note}")
    if mem:
        parts.append(f"检索记忆:\n{mem}")
    if snap:
        parts.append(f"LIVE 页面快照（优先使用其中定位）:\n{snap}")
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


def _compose_openclaw_instruction(args: Dict[str, Any]) -> str:
    """
    将结构化字段拼成一条交给 OpenClaw 的长指令，便于整系统/模块化探索与回归。
    """
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
        blocks.append(
            "【必须验证的检查点】（逐条尝试并在输出中写明每条通过/失败/跳过原因）\n" + ac
        )
    cont = (args.get("continuation_from") or "").strip()
    if cont:
        blocks.append("【承接上次执行】（在同一浏览器会话逻辑下继续，不要重复已确认无问题的步骤）\n" + cont)
    if not base and blocks:
        return "\n\n".join(blocks)
    if blocks:
        return "\n\n".join(blocks) + "\n\n【主任务说明】\n" + base
    return base


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


def run_ai_chat_with_tools(
    *,
    local_ai_service: Any,
    params: ChatToolLoopParams,
) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    """
    Returns (generated_plan_dict, norm_warnings from caller side still empty here, meta).

    Caller should run apply_step_normalization_to_plan on generated_plan_dict.
    """
    tools = chat_tool_schemas()
    oc_client = OpenClawGatewayClient()

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
    )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(
        _history_to_messages(params.history, local_ai_service._sanitize_chat_history_for_prompt)
    )
    messages.append({"role": "user", "content": params.message})

    last_plan: Dict[str, Any] = dict(params.current_plan) if isinstance(params.current_plan, dict) else {}
    meta: Dict[str, Any] = {"tool_rounds": 0, "tools_used": []}
    prof: Optional[Dict[str, Any]] = params.profile if isinstance(params.profile, dict) else None

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
            assistant_msg = local_ai_service.chat_ollama_messages(
                messages,
                params.legacy_model or local_ai_service.model_mid,
                tools,
                None,
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

            if name == "openclaw_execute":
                instr = _compose_openclaw_instruction(args)
                sid = (args.get("session_id") or "").strip()
                if not instr.strip():
                    result_text = json.dumps(
                        {"ok": False, "error": "instruction 经拼装后仍为空；请填写主任务或 environment_notes/scope"},
                        ensure_ascii=False,
                    )
                else:
                    result_text = oc_client.execute_user_instruction(instr, sid)
                meta["tools_used"].append("openclaw_execute")
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
                    )[: min(96000, _tool_result_max_chars())]
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
