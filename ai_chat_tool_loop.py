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


def _result_is_auth_fatal(result_text: str) -> bool:
    """鉴权类失败：再调 hermes_execute 只会重复同一 401，应立即停止重试。"""
    t = (result_text or "").lower()
    if "missing authentication header" in t:
        return True
    if "401" in t and any(
        k in t
        for k in (
            "auth",
            "unauthorized",
            "authentication",
            "鉴权",
            "认证",
            "api key",
            "api_key",
            "token",
            "secret",
            "桌面",
            "gateway",
        )
    ):
        return True
    if "unauthorized" in t and ("desktop" in t or "gateway" in t or "桌面" in (result_text or "")):
        return True
    if "桌面" in (result_text or "") and ("401" in t or "鉴权" in (result_text or "") or "认证" in (result_text or "")):
        return True
    return False


def _auth_fatal_user_message(result_text: str) -> str:
    t = (result_text or "").lower()
    if "missing authentication header" in t:
        return (
            "智能体上游模型鉴权失败（Missing Authentication header）。"
            "请检查 Hermes 使用的 LLM API Key/请求头配置后，停止并重新启动智能体；不要重复提交同一任务。"
        )
    return (
        "桌面自动化网关鉴权失败（401）。平台已尝试补齐密钥并拉起网关；"
        "若仍失败请确认 DESKTOP_AGENT_GATEWAY_SECRET 与网关进程一致，然后「停止」再「启动」智能体。"
        "请勿对同一鉴权错误反复重试。"
    )


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
    Web：优先要求 CDP；若未附着仍允许（按需 ensure_browser），由执行层处理失败。
    Desktop / Auto：Hermes Gateway 已配置即可。
    Android：设备已连接且 Hermes 已配置时允许。
    """
    plat = (platform_type or "web").strip().lower()
    if plat in ("desktop", "auto", "api", "cross"):
        from agent_gateway_client import agent_gateway_configured
        return agent_gateway_configured()
    if plat in ("android", "mobile"):
        from agent_gateway_client import agent_gateway_configured
        if not agent_gateway_configured():
            return False
        try:
            from mobile_device_manager import get_connected_udid
            return bool(get_connected_udid())
        except Exception:
            return False
    # Web：已附着最优；未附着也允许（执行前 ensure_browser）
    from agent_gateway_client import agent_gateway_configured
    return agent_gateway_configured()


def openclaw_execute_allowed(*, embedded_session_id: str = "", platform_type: str = "web") -> bool:
    """Deprecated alias for hermes_execute_allowed."""
    return hermes_execute_allowed(embedded_session_id=embedded_session_id, platform_type=platform_type)


def _agent_execute_tool_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "hermes_execute",
            "description": (
                "通过 Hermes 跨层执行代理完成自动化（Web CDP / 桌面 gateway / 移动 bridge / 接口 HTTP）。"
                "适用于探索流程、操作系统弹窗、多端联动；复杂任务可多次调用并用 continuation_from / session_id 衔接。"
                "执行后根据返回整理 navigate/click/input/launch_app/api_request 等步骤，必要时再 refine_test_plan。"
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
                        "description": "可选，Agent 侧会话标识（与任务上下文总线对齐）",
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
    if plat == "auto":
        parts_agent = [
            "【重要】你是全栈测试编排助手：运行时由 Hermes 充当手和眼睛（单脑执行）。",
            "- 闲聊、问身份/能力、要建议 → 直接自然语言回答，禁止 hermes_execute。",
            "- 任何真实环境操作（网页/桌面/移动/接口/混用）→ 调用 hermes_execute；不要自己用关键词分流。",
            "- Hermes 可在同会话切换 Web CDP、桌面 gateway、接口 HTTP；OS 弹窗用桌面工具。",
            "- 需要改用例 steps → refine_test_plan。",
            "- 收到 NEED_USER_ACTION 时向用户说明并等待，不要假装已完成。",
            "",
            "禁止在未确认用户要操作真实环境时调用 hermes_execute。",
        ]
    elif plat == "desktop":
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
    if plat == "auto":
        parts.extend([
            "",
            "## 输出用例质量",
            "当用户明确要求生成测试用例时，最终输出一个 JSON 对象（不要用 markdown 代码块），字段 case_name, case_url, description, precondition, expected_result, steps（与平台 runner 一致）。",
            "automation_layer 字段根据实际执行方式填写：Web 操作填 'web'，桌面操作填 'desktop'，API 操作填 'api'。",
            "日常对话、询问建议、闲聊时不需要输出 JSON，直接自然语言回答即可。",
        ])
    elif plat != "desktop":
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
    # 仅在真正调用 hermes_execute 前按需拉起本机浏览器；返回 (ok, error_message)
    ensure_browser_before_agent: Any = None
    # None=按 hermes_execute_allowed 自动判断；False=强制禁用自动化工具（纯对话）
    allow_hermes_execute: Optional[bool] = None
    # 跨端任务上下文 session_id（agent_task_context）
    task_session_id: Optional[str] = None
    # 预检得到的能力摘要（注入 Hermes）
    capabilities_summary: Optional[str] = None


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


def _inject_execution_env_verify(
    result_text: str,
    before: Dict[str, str],
    after: Dict[str, str],
    *,
    platform_type: str = "auto",
) -> str:
    """在 Hermes 返回结果中注入执行环境验证；仅 Web 前景会话时用 URL 未变判失败。"""
    try:
        data = json.loads(result_text)
        if not isinstance(data, dict):
            data = {"ok": True, "result": result_text}
    except Exception:
        data = {"ok": True, "result": result_text}

    data["_env_verify"] = {
        "before_url": before.get("url", ""),
        "before_title": before.get("title", ""),
        "after_url": after.get("url", ""),
        "after_title": after.get("title", ""),
        "page_changed": (before.get("url") != after.get("url")) or (before.get("title") != after.get("title")),
        "platform_type": platform_type,
    }

    plat = (platform_type or "auto").strip().lower()
    # 桌面/移动/接口/auto 混用：禁止用「浏览器 URL 未变」一刀切判失败
    if plat in ("desktop", "android", "mobile", "api", "auto"):
        return json.dumps(data, ensure_ascii=False)

    if not data["_env_verify"]["page_changed"] and data.get("ok") is not False:
        output = str(data.get("result") or data.get("output") or data.get("error") or result_text or "").lower()
        action_keywords = (
            "输入", "点击", "填写", "提交", "登录", "导航", "打开", "访问",
            "navigate", "click", "input", "type", "goto", "press",
        )
        if any(k in output for k in action_keywords):
            if (before.get("url") or after.get("url") or "").startswith("http"):
                msg = (
                    "前台本机浏览器页面未变化，但 Hermes 回报已操作。"
                    "请确认已启动本机浏览器并完成 CDP 附着，避免在独立后台浏览器中执行。"
                )
                data["_env_verify"]["warning"] = msg
                data["_env_verify"]["fatal"] = True
                data["ok"] = False
                data["error"] = msg

    return json.dumps(data, ensure_ascii=False)


def _handle_agent_execute(
    *,
    name: str,
    args: Dict[str, Any],
    allow_agent: bool,
    agent_client: Any,
    meta: Dict[str, Any],
    abort_event: Optional[threading.Event] = None,
    params: Optional[ChatToolLoopParams] = None,
) -> str:
    tool_key = "hermes_execute" if name == "hermes_execute" else "openclaw_execute"
    if not allow_agent:
        meta["tools_used"].append(f"{tool_key}_blocked")
        err_msg = (
            f"{tool_key} 已禁用：智能体未就绪或当前模式不允许自动化。"
            "请先启动智能体后再试。"
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
    if params and getattr(params, "task_session_id", None):
        sid = sid or str(params.task_session_id).strip()
    if not instr.strip():
        meta["tools_used"].append(tool_key)
        return json.dumps(
            {"ok": False, "error": "instruction 经拼装后仍为空；请填写主任务或 environment_notes/scope"},
            ensure_ascii=False,
        )

    plat = (getattr(params, "platform_type", None) or "auto") if params else "auto"
    vision_summary = ""
    if params and getattr(params, "screen_observer", None):
        try:
            obs = params.screen_observer
            # 桌面任务：强制截桌面/前台窗，并在首次 Hermes 调用前同步分析（避免共享屏幕“开了却空”）
            try:
                from agent_desktop_fastpath import is_desktop_nl_task

                user_msg = (getattr(params, "message", None) or "").strip()
                if plat in ("desktop",) or (user_msg and is_desktop_nl_task(user_msg)):
                    if hasattr(obs, "set_prefer_surface"):
                        obs.set_prefer_surface("desktop")
                    if getattr(obs, "platform_type", "") in ("auto", "web", ""):
                        obs.platform_type = "desktop"
            except Exception:
                pass
            vision_summary = obs.get_last_analysis() or ""
            if not vision_summary:
                vision_summary = obs.capture_and_analyze_sync(
                    instruction_hint=(
                        f"Desktop/UI automation context. User task: "
                        f"{(getattr(params, 'message', '') or '')[:200]}"
                    ),
                    force=True,
                ) or ""
        except Exception:
            vision_summary = ""

    # 桌面任务：保证 gateway 进程与密钥就绪，避免 Hermes curl 401 空转
    if plat in ("auto", "desktop", "all", "cross"):
        try:
            from desktop_service_bootstrap import ensure_desktop_gateway_for_agent

            ensure_desktop_gateway_for_agent()
        except Exception:
            pass

    ctx_prefix = ""
    try:
        from agent_task_context import get_task_context

        ctx = get_task_context(sid) if sid else None
        if ctx:
            ctx_prefix = ctx.instruction_prefix()
            # 绑定桌面 session
            try:
                import os as _os
                _os.environ["DESKTOP_AGENT_SESSION_ID"] = ctx.desktop_session_id
            except Exception:
                pass
    except Exception:
        pass

    try:
        from hermes_skill_hints import build_explore_instruction

        instr = build_explore_instruction(
            instr,
            {
                "platform": plat,
                "context_prefix": ctx_prefix,
                "vision_summary": vision_summary,
                "capabilities_summary": getattr(params, "capabilities_summary", None) or "",
            },
        )
    except Exception:
        if ctx_prefix:
            instr = ctx_prefix + instr

    before_state = _get_bridge_page_state()
    # 默认不把平台 task session 传给 Hermes（避免 [session_id=] 触发内部会话损坏）。
    # 仅当上下文显式带有 hermes_session_id 且开启 HERMES_PASS_SESSION_ID 时才会注入。
    hermes_sid = ""
    try:
        from agent_task_context import get_task_context

        ctx_h = get_task_context(sid) if sid else None
        if ctx_h and (ctx_h.hermes_session_id or "").strip():
            hermes_sid = (ctx_h.hermes_session_id or "").strip()
    except Exception:
        hermes_sid = ""
    try:
        result_text = agent_client.execute_user_instruction(
            instr, hermes_sid, abort_event=abort_event
        )
    except Exception as ex:
        from hermes_gateway_client import _friendly_corrupt_msg, _is_corrupt_session_error

        err_s = str(ex)
        result_text = json.dumps(
            {
                "ok": False,
                "error": _friendly_corrupt_msg(err_s) if _is_corrupt_session_error(err_s) else err_s[:400],
                "corrupt_session": _is_corrupt_session_error(err_s),
            },
            ensure_ascii=False,
        )

    # 鉴权失败：平台侧桌面兜底一次，并标记禁止再调 Hermes（避免重复 401）
    if _result_is_auth_fatal(result_text):
        meta["hermes_auth_blocked"] = True
        meta["hermes_auth_error"] = _auth_fatal_user_message(result_text)
        fallback_note = ""
        user_msg = ""
        if params is not None:
            user_msg = (getattr(params, "message", None) or "").strip()
        try:
            from agent_desktop_fastpath import is_desktop_nl_task, execute_desktop_nl

            if user_msg and is_desktop_nl_task(user_msg):
                desk = execute_desktop_nl(user_msg)
                # 即使 partial，只要有可读 reply/steps 也作为兜底结果（避免再回 app_query 死胡同）
                if desk.get("ok") or desk.get("steps") or desk.get("reply"):
                    result_text = json.dumps(
                        {
                            "ok": bool(desk.get("ok")) and not desk.get("partial"),
                            "partial": bool(desk.get("partial") or not desk.get("ok")),
                            "via": desk.get("via") or "platform_desktop_fallback",
                            "reply": desk.get("reply")
                            or desk.get("error")
                            or meta["hermes_auth_error"],
                            "steps": desk.get("steps") or [],
                            "hermes_auth_error": meta["hermes_auth_error"],
                            "hint": (
                                "平台已完成本机桌面兜底。请把 reply 原样告知用户；"
                                "不要编造「输入为空字符串」；不要再调用 hermes_execute。"
                            ),
                            "_desktop_fallback_done": True,
                        },
                        ensure_ascii=False,
                    )
                    fallback_note = "platform_desktop_fallback"
                    meta["desktop_fallback_reply"] = desk.get("reply") or ""
                    meta["desktop_fallback_steps"] = desk.get("steps") or []
                    meta["desktop_fallback_partial"] = bool(
                        desk.get("partial") or not desk.get("ok")
                    )
                else:
                    fallback_note = desk.get("error") or "desktop_fallback_failed"
        except Exception as ex:
            fallback_note = str(ex)[:120]
        if fallback_note != "platform_desktop_fallback":
            try:
                parsed = json.loads(result_text)
            except Exception:
                parsed = {"raw": result_text[:800]}
            if not isinstance(parsed, dict):
                parsed = {"raw": str(parsed)[:800]}
            parsed["ok"] = False
            parsed["auth_fatal"] = True
            parsed["error"] = meta["hermes_auth_error"]
            if fallback_note:
                parsed["desktop_fallback"] = fallback_note
            parsed["hint"] = "鉴权失败已确认：禁止再次调用 hermes_execute；请直接向用户说明并输出可保存的用例 JSON。"
            result_text = json.dumps(parsed, ensure_ascii=False)

    after_state = _get_bridge_page_state()
    result_text = _inject_execution_env_verify(
        result_text, before_state, after_state, platform_type=plat
    )

    # 动作后同步视觉（写入 observer + 上下文），供下一轮 Hermes / 平台使用
    if params and getattr(params, "screen_observer", None):
        try:
            import time as _time
            _time.sleep(0.45)
            sync_result = params.screen_observer.capture_and_analyze_sync(
                instruction_hint=f"After hermes_execute. Task: {getattr(params, 'message', '')}"
            )
            if sync_result:
                try:
                    data = json.loads(result_text)
                    if isinstance(data, dict):
                        data["_vision_after"] = sync_result[:500]
                        result_text = json.dumps(data, ensure_ascii=False)
                except Exception:
                    pass
                try:
                    from agent_task_context import get_task_context
                    ctx2 = get_task_context(sid) if sid else None
                    if ctx2:
                        ctx2.add_artifact("vision", sync_result[:800])
                except Exception:
                    pass
        except Exception:
            pass

    try:
        from agent_task_context import get_task_context
        ctx3 = get_task_context(sid) if sid else None
        if ctx3:
            ok_flag = True
            try:
                parsed = json.loads(result_text)
                if isinstance(parsed, dict) and parsed.get("ok") is False:
                    ok_flag = False
                if isinstance(parsed, dict) and parsed.get("corrupt_session"):
                    from agent_task_context import reset_task_context
                    reset_task_context(sid)
            except Exception:
                pass
            ctx3.append_trace("hermes_execute", result_text[:300], ok=ok_flag)
    except Exception:
        pass

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
    if params.allow_hermes_execute is not None:
        allow_agent = bool(params.allow_hermes_execute)
    else:
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
                if meta.get("hermes_auth_blocked"):
                    result_text = json.dumps(
                        {
                            "ok": False,
                            "auth_fatal": True,
                            "error": meta.get("hermes_auth_error")
                            or "鉴权失败已确认，禁止重复调用 hermes_execute",
                            "hint": "请直接向用户说明原因并输出可保存的用例 JSON，不要再调用自动化工具。",
                        },
                        ensure_ascii=False,
                    )
                    meta["tools_used"].append(f"{name}_auth_blocked")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": result_text,
                        }
                    )
                    continue
                if callable(getattr(params, "ensure_browser_before_agent", None)):
                    try:
                        ok_br, err_br = params.ensure_browser_before_agent()
                    except Exception as ex:
                        ok_br, err_br = False, str(ex)[:200]
                    if not ok_br:
                        result_text = json.dumps(
                            {"ok": False, "error": err_br or "本机浏览器未就绪，无法执行自动化"},
                            ensure_ascii=False,
                        )
                        meta["tools_used"].append(f"{name}_browser_blocked")
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tid,
                                "content": result_text,
                            }
                        )
                        continue
                result_text = _handle_agent_execute(
                    name=name,
                    args=args,
                    allow_agent=allow_agent,
                    agent_client=agent_client,
                    meta=meta,
                    abort_event=_abort,
                    params=params,
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
            if name in ("hermes_execute", "openclaw_execute") and meta.get("hermes_auth_blocked"):
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[System] 鉴权失败已确认（401）。禁止再次调用 hermes_execute。"
                            "请用中文向用户说明原因；若有部分步骤可写入用例 JSON；"
                            "不要重复描述同一鉴权错误多次。"
                        ),
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
    if params.allow_hermes_execute is not None:
        allow_agent = bool(params.allow_hermes_execute)
    else:
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
            # 无 tool call：优先当作自然语言回复（闲聊/说明），不要强行走用例 refine
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
                yield ("done", {"total_rounds": round_idx + 1, "plan": normalized, "meta": meta, "reply": ""})
                return
            except ValueError:
                meta["final_round"] = round_idx
                meta["chat_reply"] = True
                yield ("reply", {"text": text})
                yield ("done", {"total_rounds": round_idx + 1, "plan": last_plan, "meta": meta, "reply": text})
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
                if meta.get("hermes_auth_blocked"):
                    result_text = json.dumps(
                        {
                            "ok": False,
                            "auth_fatal": True,
                            "error": meta.get("hermes_auth_error")
                            or "鉴权失败已确认，禁止重复调用 hermes_execute",
                            "hint": "请直接向用户说明原因并输出可保存的用例 JSON，不要再调用自动化工具。",
                        },
                        ensure_ascii=False,
                    )
                    meta["tools_used"].append(f"{name}_auth_blocked")
                    yield ("tool_call_result", {
                        "round": round_idx, "tool": name,
                        "result_preview": result_text[:500],
                    })
                    messages.append({"role": "tool", "tool_call_id": tid, "content": result_text})
                    continue
                if callable(getattr(params, "ensure_browser_before_agent", None)):
                    try:
                        ok_br, err_br = params.ensure_browser_before_agent()
                    except Exception as ex:
                        ok_br, err_br = False, str(ex)[:200]
                    if not ok_br:
                        result_text = json.dumps(
                            {"ok": False, "error": err_br or "本机浏览器未就绪，无法执行自动化"},
                            ensure_ascii=False,
                        )
                        meta["tools_used"].append(f"{name}_browser_blocked")
                        yield ("tool_call_result", {
                            "round": round_idx, "tool": name,
                            "result_preview": result_text[:500],
                        })
                        messages.append({"role": "tool", "tool_call_id": tid, "content": result_text})
                        continue
                result_text = _handle_agent_execute(
                    name=name, args=args, allow_agent=allow_agent,
                    agent_client=agent_client, meta=meta,
                    abort_event=_abort,
                    params=params,
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

            # 桌面兜底已执行：把真实 steps 推到前端，并用平台 reply 直接结束（避免模型编造空参数）
            if (
                name in ("hermes_execute", "openclaw_execute")
                and meta.get("desktop_fallback_steps") is not None
            ):
                fb_steps = meta.get("desktop_fallback_steps") or []
                fb_recs = []
                for st in fb_steps:
                    if not isinstance(st, dict):
                        continue
                    act = (st.get("action") or "desktop").strip()
                    if act in ("wait",):
                        continue
                    tgt = (
                        st.get("description")
                        or st.get("target")
                        or st.get("input_value")
                        or act
                    )
                    fb_recs.append(
                        {
                            "action_type": act,
                            "target": str(tgt)[:120],
                            "status": "success",
                            "result": "",
                            "has_vision": False,
                            "env_verify": env_verify,
                        }
                    )
                if fb_recs:
                    yield ("action_records", fb_recs)
                fb_reply = (meta.get("desktop_fallback_reply") or "").strip()
                if not fb_reply:
                    try:
                        _p = json.loads(result_text)
                        if isinstance(_p, dict):
                            fb_reply = (_p.get("reply") or _p.get("error") or "").strip()
                    except Exception:
                        fb_reply = ""
                if not fb_reply:
                    fb_reply = meta.get("hermes_auth_error") or "桌面任务已由平台本机兜底执行。"
                last_plan = dict(last_plan) if isinstance(last_plan, dict) else {}
                last_plan["platform"] = "desktop"
                last_plan["steps"] = fb_steps
                last_plan.setdefault("case_name", (params.message or "")[:40] or "桌面操作")
                last_plan.setdefault("description", params.message or "")
                meta["partial"] = bool(meta.get("desktop_fallback_partial"))
                meta["chat_reply"] = True
                meta["via"] = "platform_desktop_fallback"
                yield ("reply", {"text": fb_reply})
                yield (
                    "done",
                    {
                        "total_rounds": round_idx + 1,
                        "plan": last_plan,
                        "meta": meta,
                        "reply": fb_reply,
                        "partial": bool(meta.get("desktop_fallback_partial")),
                    },
                )
                return

            # --- 屏幕观察：按需触发截图（仅在结果异常或首次执行时） ---
            if params.screen_observer and name in ("hermes_execute", "openclaw_execute"):
                if _result_looks_unhealthy(result_text):
                    import time as _time
                    _time.sleep(0.5)
                    params.screen_observer.capture_and_analyze_async(
                        instruction_hint=f"Task: {params.message}. Error detected after '{name}'. Analyze what went wrong on screen.",
                    )

            messages.append({"role": "tool", "tool_call_id": tid, "content": result_text})
            if name in ("hermes_execute", "openclaw_execute") and meta.get("hermes_auth_blocked"):
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[System] 鉴权失败已确认（401）。禁止再次调用 hermes_execute。"
                            "请用中文向用户说明原因；若有部分步骤可写入用例 JSON；"
                            "不要重复描述同一鉴权错误多次。"
                        ),
                    }
                )

    yield ("error", f"工具调用轮数超过上限（{_max_tool_rounds()}），请缩短任务")
