"""
Multi-turn AI test chat with OpenAI-style tool calling: hermes_execute + refine_test_plan.

Enable with environment variable AI_CHAT_TOOLS_ENABLE=1.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ai_multi_provider import dispatch_chat_completion_messages, dispatch_chat_stream
from logger import uat_logger
from embedded_browser_client import embedded_gateway_enabled
from agent_gateway_client import agent_tool_result_max_chars, get_agent_gateway_client
from hermes_config import hermes_cdp_attached


def ai_chat_tools_enabled() -> bool:
    return os.environ.get("AI_CHAT_TOOLS_ENABLE", "0").strip().lower() in ("1", "true", "yes", "on")


def _prune_old_screen_observations(messages: List[Dict[str, Any]], max_observations: int = 3) -> None:
    """当 messages 中 [Screen Observation] 消息超过 max_observations 时，移除最旧的。"""
    observation_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].startswith("[Screen Observation]")
    ]
    while len(observation_indices) > max_observations:
        idx = observation_indices.pop(0)
        messages.pop(idx)
        # 索引需要重新计算
        observation_indices = [
            i for i, m in enumerate(messages)
            if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].startswith("[Screen Observation]")
        ]


def _result_looks_unhealthy(result_text: str) -> bool:
    """判断 hermes_execute 结果是否异常，需要触发屏幕观察。"""
    text = (result_text or "").lower()
    # 明确的错误标志
    if '"ok": false' in text or "'ok': false" in text:
        return True
    # 常见异常关键词
    error_keywords = ("error", "exception", "failed", "failure", "timeout", "refused", "unreachable", "crash")
    return any(k in text for k in error_keywords)


def profile_supports_ai_chat_tools(profile: Optional[Dict[str, Any]], legacy_model: str) -> bool:
    """Whether we attempt tool-loop (Ollama, OpenAI-compatible, or Anthropic)."""
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
            return bool(str(profile.get("api_key") or "").strip())
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
    Web：严格限制——只有 CDP attach 到前台浏览器后才允许 hermes_execute。
    Desktop：Hermes 已配置时允许 OS 层探索。
    Android：不支持。
    """
    plat = (platform_type or "web").strip().lower()
    if plat == "desktop":
        from agent_gateway_client import agent_gateway_configured
        return agent_gateway_configured()
    if plat == "android":
        return False
    # Web 平台：严格限制——只有 CDP attach 到前台浏览器后才允许 hermes_execute
    # 禁止 Hermes 在独立后台浏览器中执行
    return hermes_cdp_attached()


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
        "你是 Testory 平台的 AI 测试助手，可以帮助用户进行自动化测试任务，也可以进行日常对话。",
        "",
        "## 意图判断（重要）",
        "请先判断用户输入的意图：",
        "- 如果用户在闲聊、询问你的身份/能力、表达感谢或抱怨 → 直接自然语言回答，不要调用任何工具。",
        "- 如果用户要求执行具体的浏览器/桌面测试操作（如打开网站、点击按钮、输入内容、验证结果、探索页面结构） → 才调用 hermes_execute。",
        "- 如果用户要求修改用例步骤、调整选择器、增加断言 → 调用 refine_test_plan。",
        "- 如果用户只是询问测试建议、用例设计思路 → 直接回答，不要调用工具。",
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
            "当用户明确要求生成测试用例时，最终输出一个 JSON 对象（不要用 markdown 代码块），字段 case_name, case_url, description, precondition, expected_result, steps（与平台 runner 一致）。",
            "日常对话、询问建议、闲聊时不需要输出 JSON，直接自然语言回答即可。",
            "整系统/模块任务时：steps 应覆盖完整流程（含 navigate、必要 wait、click/input、关键 assert），步数可较多；避免只给 3～5 步骨架。",
            "若 LIVE 快照存在：步骤应优先 probe_index 或快照中的 recommended 选择器，勿臆造 class。",
        ])
    else:
        parts.extend([
            "",
            "## 输出用例质量（Windows 桌面）",
            "当用户明确要求生成测试用例时，输出一个 JSON 对象。case_url 留空。每步 automation_layer=desktop。",
            "日常对话不需要输出 JSON。",
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
    abort_event: Optional[threading.Event] = None
    recorder: Any = None  # ActionRecorder 实例，用于观测 hermes_execute 结果
    screen_observer: Any = None  # ScreenObserver 实例，用于屏幕视觉观察


def _get_bridge_page_state() -> Dict[str, str]:
    """获取前台浏览器当前状态（URL、标题），用于验证 Hermes 是否在此浏览器中执行。"""
    try:
        from ai_external_browser_bridge import get_page
        page = get_page()
        if page and not page.is_closed():
            return {"url": page.url, "title": page.title()}
    except Exception:
        pass
    return {"url": "", "title": ""}


def _inject_execution_env_verify(result_text: str, before: Dict[str, str], after: Dict[str, str]) -> str:
    """在 Hermes 返回结果中注入执行环境验证信息。"""
    try:
        data = json.loads(result_text)
    except Exception:
        return result_text
    if not isinstance(data, dict):
        return result_text

    # 记录执行前后浏览器状态
    data["_env_verify"] = {
        "before_url": before.get("url", ""),
        "before_title": before.get("title", ""),
        "after_url": after.get("url", ""),
        "after_title": after.get("title", ""),
        "page_changed": (before.get("url") != after.get("url")) or (before.get("title") != after.get("title")),
    }

    # 如果页面未变化，但结果声称成功，追加警告提示
    if not data["_env_verify"]["page_changed"] and data.get("ok") is True:
        # 检查 result 或 output 中是否包含操作描述
        output = str(data.get("result") or data.get("output") or "").lower()
        action_keywords = ("输入", "点击", "填写", "提交", "登录", "navigate", "click", "input", "type")
        if any(k in output for k in action_keywords):
            data["_env_verify"]["warning"] = (
                "Hermes 返回操作成功，但前台浏览器页面未发生变化（URL/标题均未变）。"
                "可能原因：1) Hermes 未 attach 到前台浏览器，在独立后台浏览器中执行；"
                "2) 操作被浏览器的安全策略阻止；3) 操作在 iframe 或 shadow DOM 中完成，未反映在顶层页面。"
            )

    return json.dumps(data, ensure_ascii=False)


def _handle_agent_execute(
    *,
    name: str,
    args: Dict[str, Any],
    allow_agent: bool,
    agent_client: Any,
    meta: Dict[str, Any],
    abort_event: Optional[threading.Event] = None,
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
    if abort_event is not None and abort_event.is_set():
        meta["tools_used"].append(f"{tool_key}_aborted")
        return json.dumps({"ok": False, "error": "操作已被用户取消"}, ensure_ascii=False)
    instr = _compose_agent_instruction(args)
    sid = (args.get("session_id") or "").strip()
    if not instr.strip():
        meta["tools_used"].append(tool_key)
        return json.dumps(
            {"ok": False, "error": "instruction 经拼装后仍为空；请填写主任务或 environment_notes/scope"},
            ensure_ascii=False,
        )

    # 执行环境验证：记录执行前的前台浏览器状态
    before_state = _get_bridge_page_state()
    result_text = agent_client.execute_user_instruction(instr, sid, abort_event=abort_event)
    after_state = _get_bridge_page_state()
    result_text = _inject_execution_env_verify(result_text, before_state, after_state)

    meta["tools_used"].append(tool_key)
    return result_text


def run_ai_chat_with_tools(
    *,
    local_ai_service: Any,
    params: ChatToolLoopParams,
    abort_event: Optional[threading.Event] = None,
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
    _abort = abort_event or params.abort_event

    for round_idx in range(_max_tool_rounds()):
        if _abort is not None and _abort.is_set():
            raise InterruptedError("操作已被用户取消")
        if prof:
            assistant_msg = dispatch_chat_completion_messages(
                messages,
                tools,
                prof,
                local_ai_service,
                temperature=0.2,
                abort_event=_abort,
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
                abort_event=_abort,
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
                    abort_event=_abort,
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


def run_ai_chat_with_tools_stream(
    *,
    local_ai_service: Any,
    params: ChatToolLoopParams,
    abort_event: Optional[threading.Event] = None,
):
    """流式版 tool calling 循环。yield (event_type, data) 元组。

    event_type:
      - "thinking": {"round": N, "content": "..."}  LLM 正在思考
      - "tool_call_start": {"round": N, "tool": "...", "args_summary": "..."}
      - "tool_call_result": {"round": N, "tool": "...", "result_preview": "..."}
      - "plan_update": {"plan": {...}, "step_count": N}
      - "done": {"total_rounds": N, "plan": {...}, "meta": {...}}
      - "error": "错误信息"
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
    _abort = abort_event or params.abort_event

    for round_idx in range(_max_tool_rounds()):
        if _abort is not None and _abort.is_set():
            yield ("error", "操作已被用户取消")
            return

        yield ("thinking", {"round": round_idx, "content": "AI 正在推理..."})

        # --- 屏幕观察：检查上一轮异步分析的结果，注入到 messages 中 ---
        if params.screen_observer:
            pending = params.screen_observer.pop_pending_result()
            if pending:
                yield ("vision_result", {"text": pending[:300]})
                messages.append({
                    "role": "user",
                    "content": f"[Screen Observation] {pending[:300]}",
                })
                _prune_old_screen_observations(messages, max_observations=3)

        # --- 屏幕观察：触发新一轮异步截图分析 ---
        if params.screen_observer and params.screen_observer.should_capture():
            yield ("vision_start", {"message": "AI 正在观察当前屏幕..."})
            params.screen_observer.capture_and_analyze_async(
                instruction_hint=f"Task: {params.message}. Current screen before reasoning round {round_idx}.",
            )

        # 流式调用 LLM
        content_buf = ""
        assistant_msg: Optional[Dict[str, Any]] = None
        try:
            for evt_type, evt_data in dispatch_chat_stream(
                messages, tools, prof, local_ai_service,
                temperature=0.2, abort_event=_abort,
            ):
                if evt_type == "content_delta":
                    content_buf += evt_data
                elif evt_type == "done":
                    assistant_msg = evt_data
                elif evt_type == "error":
                    yield ("error", evt_data)
                    return
        except Exception as e:
            yield ("error", f"LLM 调用失败: {e}")
            return

        if assistant_msg is None:
            yield ("error", "LLM 返回为空")
            return

        tool_calls = assistant_msg.get("tool_calls")
        content = assistant_msg.get("content") or content_buf

        if not tool_calls:
            # 无 tool call，尝试解析为最终 plan
            text = (content or "").strip()
            if not text:
                yield ("error", "模型返回空内容")
                return
            try:
                parsed = local_ai_service._parse_json_response(text)
                using_model = (params.legacy_model or local_ai_service.model_mid).strip()
                if prof:
                    using_model = ((prof.get("label") or prof.get("model_id") or using_model) or using_model).strip()
                normalized = local_ai_service._normalize_output(
                    parsed, params.message, params.project_name, using_model,
                    probe_registry=params.probe_registry,
                )
                meta["final_round"] = round_idx
                n = len(normalized.get("steps") or [])
                yield ("plan_update", {"plan": normalized, "step_count": n})
                yield ("done", {"total_rounds": round_idx + 1, "plan": normalized, "meta": meta})
                return
            except ValueError:
                # 非 JSON，回退到 refine
                try:
                    refined = local_ai_service.refine_case_and_steps(
                        user_message=params.message, project_name=params.project_name,
                        current_plan=last_plan,
                        history=params.history if isinstance(params.history, list) else [],
                        model=params.legacy_model, profile=prof,
                        page_snapshot=params.page_snapshot, probe_registry=params.probe_registry,
                        probe_url=params.probe_url, memory_context=params.memory_context,
                        dom_context_pack=params.dom_context_pack,
                        interaction_context=params.interaction_context,
                    )
                    meta["fallback"] = "refine_after_non_json"
                    n = len(refined.get("steps") or [])
                    yield ("plan_update", {"plan": refined, "step_count": n})
                    yield ("done", {"total_rounds": round_idx + 1, "plan": refined, "meta": meta})
                except Exception as e2:
                    yield ("error", f"回退 refine 也失败: {e2}")
                return

        # 有 tool calls
        meta["tool_rounds"] = int(meta["tool_rounds"]) + 1
        messages.append({"role": "assistant", "content": content if content else None, "tool_calls": tool_calls})

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

            # 通知前端 tool call 开始
            args_summary = args.get("instruction") or args.get("adjustment") or str(list(args.keys()))
            yield ("tool_call_start", {"round": round_idx, "tool": name, "args_summary": args_summary[:200]})

            if name in ("hermes_execute", "openclaw_execute"):
                result_text = _handle_agent_execute(
                    name=name, args=args, allow_agent=allow_agent,
                    agent_client=agent_client, meta=meta,
                    abort_event=_abort,
                )
            elif name == "refine_test_plan":
                adj = (args.get("adjustment") or "").strip()
                if not adj:
                    result_text = json.dumps({"ok": False, "error": "adjustment 为空"}, ensure_ascii=False)
                else:
                    try:
                        refined = local_ai_service.refine_case_and_steps(
                            user_message=adj, project_name=params.project_name,
                            current_plan=last_plan,
                            history=params.history if isinstance(params.history, list) else [],
                            model=params.legacy_model, profile=prof,
                            page_snapshot=params.page_snapshot, probe_registry=params.probe_registry,
                            probe_url=params.probe_url, memory_context=params.memory_context,
                            dom_context_pack=params.dom_context_pack,
                            interaction_context=params.interaction_context,
                        )
                        last_plan = refined
                        result_text = json.dumps(
                            {"ok": True, "plan": refined, "hint": "已更新 current_plan"},
                            ensure_ascii=False,
                        )[: min(96000, max_result)]
                        n = len(refined.get("steps") or [])
                        yield ("plan_update", {"plan": refined, "step_count": n})
                    except Exception as e:
                        result_text = json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
                meta["tools_used"].append("refine_test_plan")
            else:
                result_text = json.dumps({"ok": False, "error": f"未知工具 {name}"}, ensure_ascii=False)

            # 通知前端 tool call 结果
            yield ("tool_call_result", {
                "round": round_idx, "tool": name,
                "result_preview": result_text[:500],
            })

            # 动作记录：从 hermes_execute 结果中提取结构化动作
            env_verify = None
            if name in ("hermes_execute", "openclaw_execute"):
                try:
                    parsed = json.loads(result_text)
                    if isinstance(parsed, dict):
                        env_verify = parsed.get("_env_verify")
                except Exception:
                    pass

            if params.recorder and name in ("hermes_execute", "openclaw_execute"):
                try:
                    new_recs = params.recorder.capture_from_hermes_result(result_text)
                    if new_recs:
                        yield ("action_records", [
                            {
                                "action_type": r.action_type,
                                "target": r.target,
                                "status": r.status,
                                "result": (r.result or "")[:100],
                                "has_vision": bool(r.vision_info),
                                "env_verify": env_verify,
                            }
                            for r in new_recs
                        ])
                except Exception:
                    pass

            # --- 屏幕观察：按需触发截图（仅在结果异常或首次执行时） ---
            if params.screen_observer and name in ("hermes_execute", "openclaw_execute"):
                if _result_looks_unhealthy(result_text):
                    import time as _time
                    _time.sleep(0.5)
                    params.screen_observer.capture_and_analyze_async(
                        instruction_hint=f"Task: {params.message}. Error detected after '{name}'. Analyze what went wrong on screen.",
                    )

            messages.append({"role": "tool", "tool_call_id": tid, "content": result_text})

    yield ("error", f"工具调用轮数超过上限（{_max_tool_rounds()}），请缩短任务")
