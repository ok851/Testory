"""
Multi-turn AI test chat with OpenAI-style tool calling: hermes_execute + refine_test_plan.

Enable with environment variable AI_CHAT_TOOLS_ENABLE=1.
"""
from __future__ import annotations

import json
import os
import re
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


def _result_is_auth_fatal(result_text: str) -> bool:
    """鉴权/上游不可恢复失败：再调 hermes_execute 只会重复，应立即停止重试。"""
    t = (result_text or "").lower()
    if "missing authentication header" in t:
        return True
    if "insufficient balance" in t or "余额不足" in (result_text or ""):
        return True
    if "402" in t and ("balance" in t or "insufficient" in t or "payment" in t):
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
    try:
        data = json.loads(result_text)
        if isinstance(data, dict) and (
            data.get("auth_fatal") or data.get("upstream_balance") or data.get("upstream_auth")
        ):
            return True
    except Exception:
        pass
    return False


def _result_is_stream_empty(result_text: str) -> bool:
    """空流结果：外层再调 hermes_execute 只会空转刷「正在跨层执行」。"""
    try:
        data = json.loads(result_text or "")
        if isinstance(data, dict) and data.get("stream_empty_text"):
            return True
    except Exception:
        pass
    return "stream_empty_text" in (result_text or "")


def _strip_invented_case_json(text: str) -> str:
    """失败回复里去掉模型夹带的「供参考」用例 JSON，避免误导用户去保存。"""
    import re

    t = (text or "").strip()
    if not t:
        return t
    t = re.sub(r"```(?:json)?\s*\{[\s\S]*?\}\s*```", "", t, flags=re.IGNORECASE)
    # 去掉独立大段 case_name/steps JSON（保留前后说明）
    t = re.sub(
        r"\{[^{}]*\"case_name\"[^{}]*\"steps\"\s*:\s*\[[\s\S]*?\]\s*\}",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t or text.strip()


def _hermes_retry_blocked(meta: Dict[str, Any]) -> bool:
    return bool(
        meta.get("hermes_auth_blocked")
        or meta.get("hermes_stream_blocked")
        or meta.get("hermes_tool_loop_blocked")
    )


def _hermes_retry_blocked_payload(meta: Dict[str, Any]) -> str:
    if meta.get("hermes_auth_blocked"):
        return json.dumps(
            {
                "ok": False,
                "auth_fatal": True,
                "error": meta.get("hermes_auth_error")
                or "鉴权失败已确认，禁止重复调用 hermes_execute",
                "hint": (
                    "请用中文向用户说明失败原因；禁止再次 hermes_execute；"
                    "禁止编造可执行 steps 或「供参考」用例 JSON；"
                    "不要提及任何环境变量名称。"
                ),
            },
            ensure_ascii=False,
        )
    if meta.get("hermes_tool_loop_blocked"):
        return json.dumps(
            {
                "ok": False,
                "tool_loop": True,
                "error": meta.get("hermes_tool_loop_error")
                or "上次 Hermes 已因工具死循环中止，禁止再次 hermes_execute",
                "hint": (
                    "请用中文向用户说明：智能体卡在 skill_view/navigate 等重复工具上已停止；"
                    "禁止再次 hermes_execute；禁止谎称用户取消。"
                ),
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "ok": False,
            "stream_empty_text": True,
            "error": meta.get("hermes_stream_error")
            or "上次 hermes_execute 已空流结束，禁止再次调用（避免空转至超时）",
            "hint": (
                "请用中文向用户说明智能体无可用执行轨迹；"
                "建议先「停止」再「启动」智能体后由用户重发。"
                "禁止再次 hermes_execute；禁止编造未实际执行的用例 steps JSON；"
                "不要提及环境变量。"
            ),
        },
        ensure_ascii=False,
    )


def _abort_user_message(abort_event: Optional[threading.Event], params: Optional["ChatToolLoopParams"] = None) -> str:
    """区分超时 / 工具死循环 / 用户真取消，禁止一律报「用户取消」。"""
    if abort_event is not None and getattr(abort_event, "_timed_out", False):
        return "任务已超过设定的超时时间，已自动停止"
    if params is not None and _deadline_exceeded(params):
        return "任务已超过设定的超时时间，已自动停止"
    reason = ""
    if abort_event is not None:
        reason = str(getattr(abort_event, "_abort_reason", "") or "").strip()
    if reason == "tool_loop":
        return (
            "智能体在重复调用同一工具（如 skill_view / browser_navigate）无进展，已自动中止。"
            "这不是您取消的；请重试或改述任务。"
        )
    if reason == "timeout":
        return "任务已超过设定的超时时间，已自动停止"
    return "操作已被用户取消"


def _web_hermes_system_prompt() -> str:
    return (
        "【最高优先级 — 覆盖 Hermes 默认提示与工具描述】"
        "你是 Testory 网页自动化执行器（CDP attach 到用户本机已打开的浏览器）。"
        "硬性禁止：skill_view、skill_list、skill_manage、terminal、bash、curl、windows_*、computer_use、新开标签页、截图/视觉当主路径。"
        "浏览器任务以 DOM 为准：指令中若已有「页面 DOM/可交互控件」清单，直接据此 click/type/fill；"
        "browser_snapshot 是无障碍树/DOM ref（不是视觉截图），仅在 DOM 清单缺失或难定位时再调用一次，禁止连续 snapshot。"
        "视觉截图/vision 仅作最终兜底。"
        "硬性禁止：平台已打开目标站后仍调用 browser_navigate（会重复造轮子并可能新开空白标签）。"
        "忽略工具说明里「Requires browser_navigate to be called first」。"
        "正确顺序：读 DOM → browser_click/type/fill → 必要时再 snapshot 核验。"
        "同一工具连续两次无进展则输出 NEED_USER_ACTION 并停止。"
        "需要人工验证码时输出 NEED_USER_ACTION:<原因>。"
    )


def _auth_fatal_user_message(result_text: str) -> str:
    """面向最终用户的说明：不暴露环境变量，只给可操作步骤。"""
    t = (result_text or "").lower()
    raw = result_text or ""
    if "insufficient balance" in t or "余额不足" in raw or ("402" in t and "balance" in t):
        return (
            "当前选用的大模型账户余额不足，智能体无法调用上游模型。"
            "请到「模型配置」更换可用引擎或充值后，点击「停止智能体」再「启动智能体」，然后重试。"
        )
    if "missing authentication header" in t or "invalid api key" in t or "incorrect api key" in t:
        return (
            "智能体连接上游大模型失败（API Key 无效或缺失）。"
            "请到「模型配置」检查并保存密钥后，点击「停止智能体」再「启动智能体」。"
        )
    if "桌面" in raw or "desktop" in t or "8766" in t:
        return (
            "本机桌面自动化服务鉴权未对齐，暂时无法操控桌面。"
            "请点击「停止智能体」再「启动智能体」，平台会自动同步本机服务；无需手动配置。"
        )
    return (
        "智能体执行鉴权失败。"
        "请确认「模型配置」中的引擎可用，然后「停止」并重新「启动」智能体后再试；"
        "无需关心环境变量。"
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


def _desktop_windows_tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "windows_focus_app",
                "description": (
                    "将指定应用窗口激活到前台并设为当前桌面目标。"
                    "若应用未运行，会自动尝试启动（等同 launch）；也可显式用 windows_launch_app。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app_name": {"type": "string", "description": "窗口标题或部分应用名，如「记事本」"},
                    },
                    "required": ["app_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "windows_launch_app",
                "description": (
                    "启动本机应用（未运行也可）。对应用例层 launch_app。"
                    "如 notepad/记事本、calc/计算器；启动后自动聚焦并绑定目标。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app_name": {
                            "type": "string",
                            "description": "应用名或别名，如「记事本」「notepad」「计算器」",
                        },
                    },
                    "required": ["app_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "windows_click_element",
                "description": (
                    "按短控件名点击（UIA→OCR）。description 只写「确定」「保存」「文件」等标签；"
                    "禁止把「编辑内容为…」整句当点击目标——那是 windows_type_text。"
                    "勿默认点菜单「编辑」；仅当任务明确需要应用内搜索时才用「搜索」。"
                    "失败则 get_screen_text 观察后重试；勿先单独按 Ctrl。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "短控件名，如「确定」；不要写用户整句",
                        },
                    },
                    "required": ["description"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "windows_type_text",
                "description": (
                    "向当前桌面目标窗口输入文本（优先 UIA/目标窗粘贴）。"
                    "记事本等编辑器启动后可直接输入，无需先点「编辑」菜单。"
                    "用户说「编辑内容为X / 输入X / 写入X」时，把 X 作为 text。"
                    "返回 capture_after；未核验时勿声称已输入。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要输入的字符串"},
                        "clear": {"type": "boolean", "description": "输入前是否 Ctrl+A 并删除"},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "windows_press_key",
                "description": (
                    "按键或组合键。必须一次传入完整键，如 Enter、Esc、Ctrl+N、Ctrl+S；"
                    "禁止只传 ctrl。新建文件/新页用 Ctrl+N；勿默认用 Ctrl+F 或点搜索。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "完整按键名，如 Enter / Ctrl+N（不要只写 ctrl）",
                        },
                    },
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "windows_wait",
                "description": (
                    "短暂等待。duration_ms 毫秒；或 condition=stable / desktop_change / window:标题关键词。"
                    "动作用后可用 desktop_change 核验窗口变化。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "duration_ms": {"type": "integer", "description": "等待毫秒数"},
                        "condition": {
                            "type": "string",
                            "description": "stable | desktop_change | window:标题",
                        },
                    },
                },
            },
        },
    ]


def _screen_observation_tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_screen_text",
                "description": (
                    "获取当前屏幕可见文字及位置（轻量 OCR，优先于视觉描述）。"
                    "操作前不确定元素位置、或点击失败后应调用此工具。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "region": {
                            "type": "string",
                            "description": "可选：关注区域提示（如窗口标题）",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_screen_description",
                "description": (
                    "获取屏幕视觉结构化描述（≤300字）。仅在文字信息不足以理解界面时调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hint": {
                            "type": "string",
                            "description": "关注点，如「当前聚焦窗口和按钮」",
                        },
                    },
                },
            },
        },
    ]


def _should_enable_desktop_windows_tools(platform_type: str, message: str = "") -> bool:
    """是否注册外层 windows_*。以 resolve_task_route 为准，避免 web/desktop 互串。

    设 PLATFORM_OUTER_DESKTOP_TOOLS=0 可关闭。
    """
    import os

    raw = (os.environ.get("PLATFORM_OUTER_DESKTOP_TOOLS") or "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    plat = (platform_type or "").strip().lower()
    if plat == "android":
        return False
    # UI 已显式选桌面：即使本轮 message 为空也挂工具（schema 预览/默认会话）
    if plat == "desktop" and not (message or "").strip():
        return True
    try:
        from agent_intent import resolve_task_route

        route = resolve_task_route(message or "", ui_platform=plat or "auto")
        if route.needs_desktop_tools:
            return True
        if plat == "desktop" and route.mode == "automation" and not route.needs_browser:
            return True
        # UI=desktop 且非明确网页任务：仍挂桌面工具
        if plat == "desktop" and route.platform != "web":
            return True
    except Exception:
        if plat == "desktop":
            return True
        try:
            from agent_desktop_fastpath import is_desktop_nl_task

            if message and is_desktop_nl_task(message):
                return True
        except Exception:
            pass
    return False


def _desktop_tool_failed(result_text: str) -> bool:
    try:
        data = json.loads(result_text or "")
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("success") is False or data.get("ok") is False:
        return True
    if data.get("flow_halt") is True:
        return True
    if data.get("verified") is False and data.get("success") is not True:
        return True
    return False


# 可重复的观察/等待类工具（不做「已成功则永久跳过」）
_DESKTOP_REPEATABLE_TOOLS = frozenset(
    {
        "get_screen_text",
        "get_screen_description",
        "windows_wait",
    }
)
_DESKTOP_OBS_CAP = 3  # 单次任务最多观察次数，防止空转刷屏


def _norm_tool_arg_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _is_search_ui_click_desc(description: str) -> bool:
    d = _norm_tool_arg_text(description)
    if not d:
        return False
    return any(
        k in d
        for k in (
            "搜索",
            "search",
            "查找",
            "find",
            "清空",
            "clear",
            "搜一下",
        )
    )


def _text_is_replay_of_prior(text: str, prior: str) -> bool:
    """同一关键词、或关键词被拼接重复（如 abcabc）。"""
    t = _norm_tool_arg_text(text)
    p = _norm_tool_arg_text(prior)
    if not t or not p:
        return False
    if t == p:
        return True
    if t == p + p:
        return True
    if len(p) >= 2 and t.startswith(p) and t[len(p) :] == p:
        return True
    return False


def _desktop_action_fingerprint(
    name: str,
    args: Optional[Dict[str, Any]],
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """稳定指纹：同工具+同关键参数（输入忽略 clear，避免 clear 不同导致重复灌字）。"""
    a = args or {}
    phase = str((meta or {}).get("desktop_phase") or "").strip() or "start"
    n = (name or "").strip()
    if n == "windows_focus_app":
        return f"{n}|app={_norm_tool_arg_text(a.get('app_name') or a.get('name'))}"
    if n == "windows_launch_app":
        return f"{n}|app={_norm_tool_arg_text(a.get('app_name') or a.get('name') or a.get('path'))}"
    if n == "windows_click_element":
        desc = _norm_tool_arg_text(a.get("description") or a.get("locate") or a.get("text"))
        if _is_search_ui_click_desc(desc):
            return f"{n}|family=search_ui"
        return f"{n}|desc={desc}"
    if n == "windows_type_text":
        # 故意不含 clear：否则 clear=false/true 会各记一条，失败重试时叠字
        return f"{n}|text={_norm_tool_arg_text(a.get('text'))}"
    if n == "windows_press_key":
        return f"{n}|key={_norm_tool_arg_text(a.get('key'))}|phase={phase}"
    if n in ("get_screen_text", "get_screen_description"):
        return f"{n}|obs"
    if n == "windows_wait":
        return f"{n}|ms={a.get('duration_ms') or ''}|c={_norm_tool_arg_text(a.get('condition'))}"
    try:
        payload = json.dumps(a, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        payload = str(a)
    return f"{n}|{payload[:180]}"


def _desktop_tool_succeeded(result_text: str) -> bool:
    try:
        data = json.loads(result_text or "")
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("skipped"):
        return False
    if data.get("success") is True or data.get("ok") is True:
        return True
    return False


def _desktop_type_delivery_ok(result_text: str) -> bool:
    """投递成功但 OCR 失败时，仍视为「已灌过字」，禁止再 type 同一串。"""
    try:
        data = json.loads(result_text or "")
    except Exception:
        return False
    if not isinstance(data, dict) or data.get("skipped"):
        return False
    if data.get("success") is True:
        return True
    delivery = data.get("delivery")
    if isinstance(delivery, dict) and delivery.get("ok") is True:
        return True
    attempts = data.get("attempts")
    if isinstance(attempts, list):
        for a in attempts:
            if not isinstance(a, dict):
                continue
            d = a.get("delivery")
            if isinstance(d, dict) and d.get("ok") is True:
                return True
    return False


def _remember_typed_text(meta: Dict[str, Any], text: str) -> None:
    t = str(text or "").strip()
    if not t:
        return
    arr = meta.setdefault("typed_texts", [])
    if not isinstance(arr, list):
        arr = []
        meta["typed_texts"] = arr
    nt = _norm_tool_arg_text(t)
    if nt and nt not in arr:
        arr.append(nt)


def _skip_payload(reason: str, hint: str, **extra: Any) -> str:
    body = {
        "success": True,
        "skipped": True,
        "reason": reason,
        "hint": hint,
        **extra,
    }
    return json.dumps(body, ensure_ascii=False)


def _record_succeeded_desktop_action(
    meta: Dict[str, Any],
    name: str,
    args: Optional[Dict[str, Any]],
    result_text: str,
) -> None:
    if name not in (
        "windows_focus_app",
        "windows_launch_app",
        "windows_click_element",
        "windows_type_text",
        "windows_press_key",
        "windows_wait",
        "get_screen_text",
        "get_screen_description",
    ):
        return
    fps = meta.setdefault("succeeded_action_fps", [])
    if not isinstance(fps, list):
        fps = []
        meta["succeeded_action_fps"] = fps
    attempted = meta.setdefault("attempted_action_fps", [])
    if not isinstance(attempted, list):
        attempted = []
        meta["attempted_action_fps"] = attempted
    if name in ("get_screen_text", "get_screen_description"):
        meta["obs_count"] = int(meta.get("obs_count") or 0) + 1

    try:
        data = json.loads(result_text or "")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    # 输入：投递成功则锁文本防叠字，但不推进 phase / 不记「已验证成功」除非 verified
    if name == "windows_type_text" and _desktop_type_delivery_ok(result_text):
        text = str((args or {}).get("text") or "").strip()
        _remember_typed_text(meta, text)
        fp = _desktop_action_fingerprint(name, args, meta)
        if fp not in attempted:
            attempted.append(fp)
        verified_ok = bool(data.get("verified") is True or data.get("success") is True)
        if verified_ok and fp not in fps:
            fps.append(fp)
        if not meta.get("last_search_query") and text:
            phase = str(meta.get("desktop_phase") or "start")
            if phase in ("start", "app_focused", "search_ready", "query_typed"):
                meta["last_search_query"] = text
        if verified_ok:
            _advance_desktop_phase(meta, name, args or {}, result_text)
        return

    if not _desktop_tool_succeeded(result_text):
        return

    # 点击：须 verified 才记成功指纹并推进阶段（避免假成功导致重播/乱序）
    if name == "windows_click_element":
        fp = _desktop_action_fingerprint(name, args, meta)
        if fp not in attempted:
            attempted.append(fp)
        if data.get("verified") is False:
            return
        if fp not in fps:
            fps.append(fp)
        desc = str((args or {}).get("description") or "")
        if _is_search_ui_click_desc(desc) or data.get("search_armed"):
            meta["search_ui_done"] = True
        _advance_desktop_phase(meta, name, args or {}, result_text)
        return

    fp = _desktop_action_fingerprint(name, args, meta)
    if fp not in fps:
        fps.append(fp)
    if name == "windows_focus_app":
        apps = meta.setdefault("focused_apps", [])
        if isinstance(apps, list):
            app = _norm_tool_arg_text((args or {}).get("app_name") or (args or {}).get("name"))
            if app and app not in apps:
                apps.append(app)
    if name == "windows_launch_app":
        apps = meta.setdefault("focused_apps", [])
        if isinstance(apps, list):
            app = _norm_tool_arg_text(
                (args or {}).get("app_name") or (args or {}).get("name") or (args or {}).get("path")
            )
            if app and app not in apps:
                apps.append(app)
    _advance_desktop_phase(meta, name, args or {}, result_text)


def _advance_desktop_phase(
    meta: Dict[str, Any],
    name: str,
    args: Dict[str, Any],
    result_text: str,
) -> None:
    profile = str(meta.get("flow_profile") or "generic")
    # 通用 profile：不进入 search_ready 流水线，避免非搜索任务被锁死
    if profile != "im_search":
        order_g = ("start", "app_focused", "acted", "done")
        cur = str(meta.get("desktop_phase") or "start")
        if cur not in order_g:
            cur = "start"

        def _bump_g(to: str) -> None:
            nonlocal cur
            if order_g.index(to) >= order_g.index(cur):
                meta["desktop_phase"] = to
                cur = to

        try:
            data = json.loads(result_text or "")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if name == "windows_focus_app":
            _bump_g("app_focused")
            return
        if name == "windows_launch_app":
            _bump_g("app_focused")
            return
        if name in (
            "windows_click_element",
            "windows_type_text",
            "windows_press_key",
        ):
            _bump_g("acted")
            if data.get("search_armed") or _is_search_ui_click_desc(
                str((args or {}).get("description") or "")
            ):
                # 记录但不切换到 IM 专属 phase
                meta["search_ui_touched"] = True
            return
        return

    order = (
        "start",
        "app_focused",
        "search_ready",
        "query_typed",
        "item_selected",
        "compose",
        "body_typed",
        "submitted",
    )
    cur = str(meta.get("desktop_phase") or "start")
    if cur not in order:
        cur = "start"

    def _bump(to: str) -> None:
        nonlocal cur
        if order.index(to) >= order.index(cur):
            meta["desktop_phase"] = to
            cur = to

    try:
        data = json.loads(result_text or "")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    if name == "windows_focus_app":
        _bump("app_focused")
        return
    if name == "windows_launch_app":
        _bump("app_focused")
        return
    if name == "windows_click_element":
        desc = _norm_tool_arg_text(args.get("description") or "")
        if data.get("search_armed") or _is_search_ui_click_desc(desc):
            _bump("search_ready")
            return
        if cur in ("query_typed", "search_ready", "app_focused"):
            _bump("item_selected")
            _bump("compose")
        elif cur in ("item_selected", "compose", "body_typed"):
            _bump("compose")
        return
    if name == "windows_type_text":
        text = str(args.get("text") or "").strip()
        if cur in ("start", "app_focused", "search_ready") or data.get("search_armed"):
            meta["last_search_query"] = text
            _bump("query_typed")
        else:
            meta["last_body_text"] = text
            _bump("body_typed")
        phase = str(data.get("input_phase") or "").lower()
        if phase == "compose":
            _bump("compose")
        return
    if name == "windows_press_key":
        key = _norm_tool_arg_text(args.get("key"))
        if key in ("enter", "return"):
            if cur in ("query_typed", "search_ready"):
                _bump("item_selected")
                _bump("compose")
            elif cur in ("body_typed", "compose", "item_selected"):
                _bump("submitted")
            phase = str(data.get("input_phase") or "").lower()
            if phase == "compose":
                _bump("compose")
        return


def _forbidden_replay_summary(meta: Dict[str, Any]) -> str:
    bits = []
    apps = meta.get("focused_apps") or []
    if isinstance(apps, list) and apps:
        bits.append("已聚焦:" + ",".join(str(a) for a in apps[:3]))
    if meta.get("search_ui_done") or str(meta.get("desktop_phase") or "") in (
        "search_ready",
        "query_typed",
        "item_selected",
        "compose",
        "body_typed",
        "submitted",
    ):
        bits.append("搜索UI已完成(禁止再点搜索/清空)")
    q = str(meta.get("last_search_query") or meta.get("auto_typed_contact") or "").strip()
    if q:
        bits.append(f"已输入搜索词「{q}」(禁止再输)")
    typed = meta.get("typed_texts") or []
    if isinstance(typed, list) and typed:
        bits.append("已输入文本:" + ",".join(str(t)[:20] for t in typed[:4]))
    phase = str(meta.get("desktop_phase") or "start")
    bits.append(f"phase={phase}")
    return "；".join(bits) if bits else f"phase={phase}"


def _desktop_progress_reminder(meta: Dict[str, Any]) -> str:
    phase = str((meta or {}).get("desktop_phase") or "").strip()
    if phase not in (
        "search_ready",
        "query_typed",
        "item_selected",
        "compose",
        "body_typed",
    ):
        return ""
    return (
        f"[System] 进度锁定：{_forbidden_replay_summary(meta)}。"
        "失败时只允许「向前」修复，严禁回退重跑已成功/已尝试的 focus、搜索点击、同一段输入。"
        "下一步通常是：Enter 确认结果，或输入尚未出现过的正文并提交。"
    )


def _desktop_flow_should_stop(meta: Optional[Dict[str, Any]]) -> bool:
    """桌面步骤失败后整任务停：禁止进入下一轮 LLM。"""
    m = meta or {}
    return bool(m.get("desktop_flow_halted"))


def _desktop_halt_user_facing(tool_name: str, result_text: str) -> str:
    """给前端/用户看的失败说明（非注入给模型的继续指令）。"""
    err = ""
    sug = ""
    try:
        data = json.loads(result_text or "")
        if isinstance(data, dict):
            err = str(data.get("error") or "")[:300]
            sug = str(data.get("suggestion") or "")[:300]
    except Exception:
        pass
    parts = [f"桌面步骤 `{tool_name}` 失败，任务已停止。"]
    if err:
        parts.append(err)
    if sug:
        parts.append(f"建议：{sug}")
    parts.append("请处理界面后重发指令，或说明下一步。")
    return " ".join(parts)


def _desktop_fail_stop_message(tool_name: str, result_text: str, *, meta: Optional[Dict[str, Any]] = None) -> str:
    err = ""
    sug = ""
    try:
        data = json.loads(result_text or "")
        if isinstance(data, dict):
            err = str(data.get("error") or "")[:200]
            sug = str(data.get("suggestion") or "")[:200]
    except Exception:
        pass
    if meta is None:
        meta = {}
    meta["desktop_flow_halted"] = True
    meta["failed"] = True
    meta["partial"] = True
    meta["repair_forward_only"] = True
    locked = _forbidden_replay_summary(meta)
    return (
        f"[System] 流程闸：上一步 `{tool_name}` 失败"
        + (f"（{err}）" if err else "")
        + "。本轮剩余工具已取消；**整任务已停止，禁止再调用任何 windows_* / 猜测下一步**。"
        f"【进度摘要】{locked}。"
        "请用中文向用户说明失败原因并结束；不要继续 focus、点搜索、输入或按键。"
        + (f" 建议：{sug}" if sug else "")
    )


def _should_skip_replay_desktop_tool(
    name: str,
    args: Dict[str, Any],
    meta: Dict[str, Any],
) -> Optional[str]:
    """通用防回退：语义族 + 指纹 + 已输入文本；失败后强制只前进。"""
    n = (name or "").strip()
    fps = meta.get("succeeded_action_fps") or []
    if not isinstance(fps, list):
        fps = []
    phase = str(meta.get("desktop_phase") or meta.get("wechat_phase") or "").strip()
    forward_only = bool(meta.get("repair_forward_only"))
    typed = meta.get("typed_texts") if isinstance(meta.get("typed_texts"), list) else []
    last_q = str(meta.get("last_search_query") or meta.get("auto_typed_contact") or "").strip()

    # 观察刷屏
    if n in ("get_screen_text", "get_screen_description"):
        obs = int(meta.get("obs_count") or 0)
        if obs >= _DESKTOP_OBS_CAP:
            return _skip_payload(
                "observation_cap",
                "观察次数已达上限，请直接做尚未完成的下一步，禁止继续截屏空转。",
                desktop_phase=phase or "start",
            )
        if forward_only and obs >= 1:
            return _skip_payload(
                "repair_skip_extra_observe",
                "修复模式只允许观察一次，请立刻执行前进动作。",
            )

    if n in _DESKTOP_REPEATABLE_TOOLS:
        return None

    fp = _desktop_action_fingerprint(n, args, meta)
    if fp in fps:
        return _skip_payload(
            "already_succeeded_no_replay",
            "该动作已成功/已尝试过，禁止回退重跑；请推进未完成的下一步。",
            fingerprint=fp,
            desktop_phase=phase or "start",
        )

    if n == "windows_focus_app":
        app = _norm_tool_arg_text(args.get("app_name") or args.get("name"))
        focused = meta.get("focused_apps") if isinstance(meta.get("focused_apps"), list) else []
        if app and app in focused:
            return _skip_payload(
                "focus_already_done",
                "该应用已成功聚焦，禁止重复 focus。",
            )
        if forward_only and focused:
            return _skip_payload(
                "repair_skip_refocus",
                "修复模式禁止回退 focus；请做下一步新动作。",
            )

    if n == "windows_launch_app":
        app = _norm_tool_arg_text(args.get("app_name") or args.get("name") or args.get("path"))
        focused = meta.get("focused_apps") if isinstance(meta.get("focused_apps"), list) else []
        if app and app in focused:
            return _skip_payload(
                "launch_already_done",
                "该应用已启动/聚焦，禁止重复 launch；请继续后续操作。",
            )

    if n == "windows_click_element":
        desc = str(args.get("description") or args.get("text") or "")
        search_click = _is_search_ui_click_desc(desc)
        if search_click and (
            meta.get("search_ui_done")
            or meta.get("auto_typed_search")
            or phase
            in (
                "search_ready",
                "query_typed",
                "item_selected",
                "compose",
                "body_typed",
                "submitted",
                "chat_open",
            )
        ):
            return _skip_payload(
                "search_ui_already_done",
                "搜索相关点击已完成，禁止再点搜索/清空；若结果已出请 Enter 或输入正文。",
            )
        if forward_only and search_click:
            return _skip_payload(
                "repair_skip_search_click",
                "修复模式禁止回退点搜索；请 Enter 确认或输入未出现过的正文。",
            )

    if n == "windows_type_text":
        text = str(args.get("text") or "").strip()
        nt = _norm_tool_arg_text(text)
        if nt and nt in typed:
            return _skip_payload(
                "text_already_typed",
                f"文本「{text[:40]}」已输入过，禁止再次 type（会叠字）；请 Enter 确认或输入新正文。",
            )
        if last_q and _text_is_replay_of_prior(text, last_q):
            return _skip_payload(
                "search_query_already_typed",
                f"搜索词「{last_q[:40]}」已输入，禁止重复/拼接输入。",
            )
        if meta.get("auto_typed_search") and last_q and _text_is_replay_of_prior(text, last_q):
            return _skip_payload(
                "auto_typed_lock",
                "平台已自动输入过该搜索词，禁止模型再 type 同一内容。",
            )
        if forward_only and last_q and _text_is_replay_of_prior(text, last_q):
            return _skip_payload(
                "repair_skip_retype",
                "修复模式禁止重输已输入内容。",
            )

    if n == "windows_press_key":
        key = _norm_tool_arg_text(args.get("key"))
        # 同阶段同键已成功则上面指纹会拦；此处额外：query 已输入后禁止 Ctrl+F 回退
        if key in ("ctrl+f", "^f") and phase in (
            "query_typed",
            "item_selected",
            "compose",
            "body_typed",
            "submitted",
        ):
            return _skip_payload(
                "hotkey_search_replay",
                "搜索阶段已过，禁止再 Ctrl+F。",
            )

    return None


def _pending_search_query_from_user_message(message: str) -> str:
    """从用户原话解析「搜索框应输入的关键词」（IM/文件管理器等通用）。"""
    msg = (message or "").strip()
    if not msg:
        return ""
    try:
        from agent_desktop_fastpath import _parse_wechat_send

        pair = _parse_wechat_send(msg)
        if pair and pair[0]:
            return str(pair[0]).strip()
    except Exception:
        pass
    # 兜底：引号内较短片段
    try:
        quoted = re.findall(r"[「『\"'“]([^」』\"'”]{1,40})[」』\"'”]", msg)
        for q in quoted:
            q = (q or "").strip()
            if q and "消息" not in q and len(q) <= 40:
                return q
    except Exception:
        pass
    return ""


def _pending_contact_from_user_message(message: str) -> str:
    """兼容旧名。"""
    return _pending_search_query_from_user_message(message)


def _message_wants_search_autofill(message: str) -> bool:
    """仅当用户意图是「搜联系人/条目并继续」时，平台才自动 type+Enter。"""
    t = (message or "").strip()
    if not t:
        return False
    sendish = any(
        k in t
        for k in (
            "发消息",
            "发给",
            "发送",
            "发一句",
            "发一条",
            "发条",
            "搜索",
            "搜一下",
            "查找",
            "找一下",
        )
    )
    # 「给X发」类：有可解析关键词即可
    if not sendish and not re.search(r"给.+发", t):
        return False
    return bool(_pending_search_query_from_user_message(t))


def _resolve_desktop_flow_profile(message: str, platform_type: str = "") -> str:
    """im_search：搜→输→Enter 流水线；generic：通用 focus→act。"""
    if _message_wants_search_autofill(message):
        return "im_search"
    return "generic"


def _auto_type_contact_after_search_click(
    *,
    params: Any,
    meta: Dict[str, Any],
    click_result_text: str,
) -> Optional[Tuple[str, str]]:
    """点开搜索后立刻自动输入关键词，避免等下一轮 LLM 时焦点被抢走。

    Returns (query, type_result_json) or None.
    """
    if meta.get("auto_typed_search"):
        return None
    # 非 IM 搜索意图：不自动灌词，交给模型下一步
    if str(meta.get("flow_profile") or "") != "im_search":
        if not _message_wants_search_autofill(getattr(params, "message", "") or ""):
            return None
    try:
        data = json.loads(click_result_text or "")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("success"):
        return None
    if not (data.get("search_armed") or data.get("via") in ("search_ctrl_f", "geometry_wechat_search")):
        desc = str(data.get("description") or "")
        if not any(k in desc for k in ("搜索", "search", "Search", "查找")):
            return None
    contact = _pending_search_query_from_user_message(getattr(params, "message", "") or "")
    if not contact:
        return None
    type_json = _dispatch_desktop_or_screen_tool(
        "windows_type_text", {"text": contact, "clear": True}
    )
    meta["auto_typed_search"] = True
    meta["auto_typed_contact"] = contact
    meta["last_search_query"] = contact
    meta["search_ui_done"] = True
    meta["tools_used"].append("windows_type_text_auto")
    # 无论 OCR 是否通过，只要投递过就锁定，防止模型再 type 叠字
    _record_succeeded_desktop_action(meta, "windows_type_text", {"text": contact}, type_json)
    _remember_typed_text(meta, contact)
    return contact, type_json


def _auto_open_wechat_search_hit_after_type(
    *,
    meta: Dict[str, Any],
    type_result_json: str,
) -> Optional[str]:
    """搜索关键词输入成功后按 Enter 确认首条结果（仅 im_search profile）。"""
    if meta.get("auto_opened_search_hit"):
        return None
    if str(meta.get("flow_profile") or "") != "im_search" and not meta.get("auto_typed_search"):
        return None
    try:
        data = json.loads(type_result_json or "")
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    if not meta.get("auto_typed_search"):
        return None
    press_json = _dispatch_desktop_or_screen_tool(
        "windows_press_key", {"key": "Enter"}
    )
    meta["auto_opened_search_hit"] = True
    meta["tools_used"].append("windows_press_key_auto_enter")
    enter_ok = False
    try:
        enter_ok = bool(json.loads(press_json or "").get("success"))
    except Exception:
        enter_ok = False
    if enter_ok:
        meta["desktop_phase"] = "compose"
        meta["wechat_phase"] = "chat_open"  # 兼容旧字段
        try:
            from windows_desktop_tools import mark_compose_input_phase

            mark_compose_input_phase()
        except Exception:
            pass
    _record_succeeded_desktop_action(
        meta, "windows_press_key", {"key": "Enter"}, press_json
    )
    return press_json


def _maybe_persist_desktop_run_memory(
    meta: Dict[str, Any],
    *,
    message: str = "",
    failed: bool = False,
) -> None:
    if failed or meta.get("desktop_flow_halted") or meta.get("failed"):
        return
    tools = meta.get("tools_used") or []
    if not isinstance(tools, list) or not tools:
        return
    winish = any(str(t).startswith("windows_") for t in tools)
    if not winish:
        return
    try:
        from desktop_run_memory import apps_from_meta, record_successful_run

        app = apps_from_meta(meta) or ""
        if not app:
            # 从用户话里取前几个字作粗标签
            app = (message or "").strip()[:24] or "desktop"
        record_successful_run(
            app_label=app,
            tools_used=[str(t) for t in tools if not str(t).endswith("_skipped_replay")],
            phase=str(meta.get("desktop_phase") or ""),
            user_goal=message or "",
        )
    except Exception:
        pass


def prefer_outer_desktop_tools(*, platform_type: str = "", message: str = "") -> bool:
    """桌面任务是否走外层 windows_*（禁止再包一层 hermes_execute 空转）。"""
    return _should_enable_desktop_windows_tools(platform_type, message)


def chat_tool_schemas(
    *,
    allow_openclaw: bool = True,
    allow_hermes: Optional[bool] = None,
    platform_type: str = "web",
    allow_screen_tools: bool = False,
    allow_desktop_windows_tools: Optional[bool] = None,
    message: str = "",
    allow_refine_test_plan: bool = True,
) -> List[Dict[str, Any]]:
    allow = allow_hermes if allow_hermes is not None else allow_openclaw
    schemas: List[Dict[str, Any]] = []
    enable_win = (
        allow_desktop_windows_tools
        if allow_desktop_windows_tools is not None
        else _should_enable_desktop_windows_tools(platform_type, message)
    )
    plat = (platform_type or "web").strip().lower()
    # 桌面精简 profile：只暴露 windows_* + 轻量观察，避免 hermes/refine 淹没模型
    desktop_slim = enable_win and plat in ("desktop", "auto") and (
        plat == "desktop"
        or _should_enable_desktop_windows_tools("desktop", message)
    )
    if enable_win:
        schemas.extend(_desktop_windows_tool_schemas())
        # 桌面任务默认带观察工具（精简集）
        if desktop_slim or allow_screen_tools:
            schemas.extend(_screen_observation_tool_schemas())
    elif allow_screen_tools:
        schemas.extend(_screen_observation_tool_schemas())
    if allow and not (desktop_slim and plat == "desktop"):
        schemas.append(_agent_execute_tool_schema())
    if allow_refine_test_plan and not (desktop_slim and plat == "desktop"):
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


def _dispatch_desktop_or_screen_tool(name: str, args: Dict[str, Any]) -> str:
    from windows_desktop_tools import (
        SCREEN_TOOL_NAMES,
        WINDOWS_TOOL_NAMES,
        dispatch_windows_or_screen_tool,
    )

    from windows_desktop_tools import WINDOWS_COMPAT_TOOL_NAMES

    if (
        name in WINDOWS_TOOL_NAMES
        or name in SCREEN_TOOL_NAMES
        or name in WINDOWS_COMPAT_TOOL_NAMES
    ):
        result = dispatch_windows_or_screen_tool(name, args or {})
        return json.dumps(result, ensure_ascii=False)
    return json.dumps({"success": False, "error": f"未知工具 {name}"}, ensure_ascii=False)


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
    generate_case_after_run: bool = False,
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
        "- 如果用户要求执行具体的浏览器测试操作 → 可调用 hermes_execute（同一任务只调用一次）。",
        "- 如果是 Windows 桌面 GUI 操作（打开应用、点击、输入等）→ **直接调用 windows_***"
        "（launch/focus → 新建用 Ctrl+N → type_text；「编辑内容为X」勿点菜单编辑），逐步执行；"
        "每步根据工具返回再决定下一步。"
        "禁止只调用 hermes_execute 后空等；禁止臆造「已输入/已发送」。",
        (
            "- 开启「执行后生成用例」时：操作成功后只需简短中文汇报；"
            "平台会从动作轨迹自动规范化生成用例，禁止 refine_test_plan，禁止手写大段用例 JSON。"
            if generate_case_after_run
            else "- 未开启「执行后生成用例」：操作完成后简短中文汇报即可，禁止 refine_test_plan，禁止输出用例 JSON。"
        ),
        "- 如果用户只是询问测试建议、用例设计思路 → 直接回答，不要调用工具。",
        "- 若 hermes_execute 返回 stream_empty / auth_fatal → 禁止再次 hermes_execute。",
        "",
        "## Hermes Agent 与多轮工具",
    ]
    plat = (platform_type or "web").strip().lower()
    if plat == "auto":
        parts_agent = [
            "【重要】你是可执行 Agent：闲聊直接答；真实环境用工具逐步操作。",
            "- 闲聊、问身份/能力、要建议 → 直接自然语言回答，禁止乱调工具。",
            "- Windows 桌面 GUI（任意本机应用）→ **必须用 windows_***："
            "launch/focus →（新建用 Ctrl+N）→ type_text 写正文 / press；"
            "「编辑内容为X」= type_text(X)，禁止点菜单「编辑」；勿默认点「搜索」。"
            "每步看工具返回再继续；禁止只调一次 hermes_execute 然后声称完成。"
            "同轮不要一次提交多个互依赖动作；若上一步失败/flow_halt，禁止继续 type/press/click。"
            "【进度】已成功步骤禁止回退重跑；失败即停止并向用户说明。"
            "严禁编造「无法操作某应用/只能测网页」；用 windows_* 真实执行。",
            "- 网页 / 移动 / 跨层复杂探索 → 可一次 hermes_execute。",
            (
                "- 开启生成用例：成功后简短汇报；用例由平台从动作轨迹自动生成，禁止 refine_test_plan / 手写大段 JSON。"
                if generate_case_after_run
                else "- 未开启生成用例：完成后简短汇报，禁止 refine_test_plan / 用例 JSON。"
            ),
            "- 收到 NEED_USER_ACTION / stream_empty / auth_fatal 时向用户说明；"
            "禁止编造未实际执行的 steps JSON。",
            "",
            "禁止在未确认用户要操作真实环境时调用自动化工具。",
        ]
    elif plat == "desktop":
        parts_agent = [
            "【重要】当前为 **Windows 桌面** 场景。用 windows_* 逐步操控本机任意 GUI 应用。",
            "通用流程：若应用可能未打开，先 windows_launch_app / windows_focus_app → "
            "需要新建文件/新页时用 windows_press_key(Ctrl+N) → "
            "「编辑内容为… / 输入… / 写入…」请直接 windows_type_text(正文)，不要点菜单「编辑」。",
            "记事本等文本编辑器：启动后文档区通常已可输入，优先 type_text；仅当输入失败再 click 正文区域。",
            "windows_click_element 的 description 只写短控件名（如「确定」「保存」），禁止把用户整句（如「编辑内容为xxx」）当作点击目标。",
            "按用户目标点击控件；勿默认点「搜索」。仅当用户要搜索联系人/条目时才点搜索并输入关键词，再 Enter 确认。",
            "禁止单独按 ctrl；热键须完整组合（如 Ctrl+N 新建、Ctrl+S 保存）。",
            "【进度锁】已成功的 focus/点击/同一段输入禁止回退重跑。"
            "少用反复 get_screen_description。",
            "禁止未看工具返回就声称「已完成」；失败时用中文说明真实工具错误。"
            "【流程闸】同轮每步只调一个 windows_*；上一步 success=false / flow_halt 则整任务停止，禁止继续猜测下一步。",
            "【严禁编造能力限制】你具备 windows_*，可操作本机已安装/已打开的桌面应用。"
            "禁止回答「只能测网页」「某某应用无法自动化所以不做」等推脱。",
            (
                "【收尾】开启生成用例：windows_* 成功后一两句中文汇报即可；"
                "平台从动作轨迹自动生成用例，禁止 refine_test_plan / 手写大段 JSON。"
                if generate_case_after_run
                else "【收尾】未开启生成用例：windows_* 目标完成后立刻用一两句中文汇报结果并结束，禁止再调工具、禁止输出用例 JSON。"
            ),
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
            "起始网址在用户消息里（平台无独立 URL 输入框）；instruction 须带上完整任务（含 URL、账号、验收点）。"
            "平台会尝试从消息解析 URL 并预导航；若仍停在 about:blank，Hermes 须先 navigate 到消息中的地址。",
            "hermes_execute 可把 scope / environment_notes / acceptance_criteria / continuation_from 与 instruction 组合成长指令；"
            "对大系统请分多轮调用。拿到 Agent 文本结果后提炼选择器、URL、断言文案；必要时调用 refine_test_plan 合并。",
            "当仅改 JSON 步骤、选择器或断言、且无需浏览器时，可只调用 refine_test_plan。",
        ]
    parts.extend(parts_agent)
    if plat == "auto":
        parts.extend([
            "",
            "## 输出用例质量",
            (
                "开启生成用例时：不要手写用例 JSON / 不要 refine_test_plan；"
                "平台在工具结束后从 ActionRecorder 轨迹自动规范化并给出可保存用例。"
                if generate_case_after_run
                else "未开启「执行后生成用例」：禁止输出用例 JSON，禁止 refine_test_plan；完成后自然语言汇报即可。"
            ),
            (
                ""
                if generate_case_after_run
                else "若用户之后单独点「生成用例」，再走用例生成入口。"
            ),
            "",
            "若 hermes_execute 失败/空流：只用中文说明原因与排查建议，禁止输出「供参考」假 steps。",
            "日常对话、询问建议、闲聊时不需要输出 JSON，直接自然语言回答即可。",
        ])
    elif plat != "desktop":
        parts.extend([
            "",
            "## 输出用例质量",
            (
                "开启生成用例时：禁止手写用例 JSON / refine_test_plan；平台从动作轨迹自动生成。"
                if generate_case_after_run
                else "未开启生成用例：禁止输出用例 JSON；操作完成后简短汇报。"
            ),
            "日常对话、询问建议、闲聊时不需要输出 JSON，直接自然语言回答即可。",
            "若执行失败/空流：禁止编造 steps。",
        ])
    else:
        parts.extend([
            "",
            "## 输出用例质量（Windows 桌面）",
            (
                "开启生成用例时：成功后简短汇报即可；平台从 ActionRecorder 轨迹自动生成 desktop 用例，"
                "禁止 refine_test_plan / 手写大段 JSON。"
                if generate_case_after_run
                else "未开启「执行后生成用例」：禁止输出用例 JSON / refine_test_plan；目标达成后一两句中文汇报并结束。"
            ),
            "日常对话不需要输出 JSON。",
            "若 hermes_execute 空流/失败：只用中文说明，禁止输出假 launch_app/input steps。",
        ])
    # 去掉空段落
    parts = [p for p in parts if p is not None and str(p).strip() != ""]
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
    # 共享屏幕开关：为 True 时向工具列表注册 get_screen_text / get_screen_description
    allow_screen_tools: bool = False
    # None=按 platform/message 自动判断；True/False 强制开关 windows_* 工具
    allow_desktop_windows_tools: Optional[bool] = None
    # 任务截止时间戳（time.time()）；超时后工具循环主动停止
    deadline_ts: Optional[float] = None
    # 仅在真正调用 hermes_execute 前按需拉起本机浏览器；返回 (ok, error_message)
    ensure_browser_before_agent: Any = None
    # None=按 hermes_execute_allowed 自动判断；False=强制禁用自动化工具（纯对话）
    allow_hermes_execute: Optional[bool] = None
    # 跨端任务上下文 session_id（agent_task_context）
    task_session_id: Optional[str] = None
    # 预检得到的能力摘要（注入 Hermes）
    capabilities_summary: Optional[str] = None
    # 执行后是否从 ActionRecorder 轨迹生成用例（不走二次 LLM refine）
    generate_case_after_run: bool = False
    # 是否暴露 refine_test_plan（任务执行默认 False；用例对话可 True）
    allow_refine_test_plan: Optional[bool] = None


def _remaining_deadline_sec(params: "ChatToolLoopParams") -> Optional[float]:
    import time as _time

    dl = getattr(params, "deadline_ts", None)
    if dl is None:
        return None
    try:
        return max(0.0, float(dl) - _time.time())
    except Exception:
        return None


def _deadline_exceeded(params: "ChatToolLoopParams") -> bool:
    rem = _remaining_deadline_sec(params)
    if rem is None:
        return False
    return rem <= 0.0


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


def _resolve_start_url_for_hermes(params: Optional[ChatToolLoopParams], args: Dict[str, Any]) -> str:
    """任务起始 URL：优先用户消息原文（前端无独立 URL 框），再 plan / probe / 工具参数。"""
    candidates: List[str] = []
    if params:
        candidates.append(str(getattr(params, "message", None) or "").strip())
        candidates.append(str(getattr(params, "test_scope", None) or "").strip())
        candidates.append(str(getattr(params, "probe_url", None) or "").strip())
        plan = getattr(params, "current_plan", None) or {}
        if isinstance(plan, dict):
            candidates.append(str(plan.get("case_url") or "").strip())
        ctx = getattr(params, "interaction_context", None) or {}
        if isinstance(ctx, dict):
            candidates.append(str(ctx.get("url") or "").strip())
    candidates.append(str((args or {}).get("start_url") or (args or {}).get("url") or "").strip())
    candidates.append(str((args or {}).get("instruction") or "").strip())
    try:
        from agent_intent import extract_task_url
    except Exception:
        extract_task_url = None  # type: ignore
    for c in candidates:
        if not c:
            continue
        if extract_task_url:
            hit = extract_task_url(c, allow_seed=False)
            if hit:
                return hit
            # 候选本身已是纯 URL
            hit2 = extract_task_url(f"打开 {c}", allow_seed=False)
            if hit2 and c.strip().lower().startswith(("http://", "https://", "www.", "localhost")):
                return hit2
        cl = c.strip()
        if re.match(r"^https?://\S+$", cl, re.I):
            return cl.rstrip(").,;]}\"'")
    # 最后允许百度等种子（仅用户消息）
    if params and extract_task_url:
        msg = str(getattr(params, "message", None) or "").strip()
        if msg:
            seeded = extract_task_url(msg, allow_seed=True)
            if seeded:
                return seeded
    return ""


def _handle_agent_execute(
    *,
    name: str,
    args: Dict[str, Any],
    allow_agent: bool,
    agent_client: Any,
    meta: Dict[str, Any],
    abort_event: Optional[threading.Event] = None,
    params: Optional[ChatToolLoopParams] = None,
    on_trace: Any = None,
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
        return json.dumps(
            {"ok": False, "error": _abort_user_message(abort_event, params), "aborted": True},
            ensure_ascii=False,
        )
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

    start_url = _resolve_start_url_for_hermes(params, args)
    cur_url = ""
    cur_title = ""
    try:
        cur = _get_bridge_page_state()
        cur_url = str(cur.get("url") or "").strip()
        cur_title = str(cur.get("title") or "").strip()
    except Exception:
        pass
    # 桥接页状态为空时，从 CDP /json/list 取最佳 http 页，避免误判「未到达」而反复 navigate
    if not cur_url or cur_url.lower() in ("about:blank", "chrome://newtab/", "edge://newtab/"):
        try:
            from web_capture.cdp_browser import fetch_cdp_pages, _snap as _cdp_snap, _is_blank_page_url

            port = int((_cdp_snap() or {}).get("debug_port") or 0)
            for item in fetch_cdp_pages(port):
                u = str(item.get("url") or "").strip()
                if u and not _is_blank_page_url(u) and u.lower().startswith(("http://", "https://")):
                    cur_url = u
                    cur_title = str(item.get("title") or cur_title or "").strip()
                    break
        except Exception:
            pass

    def _url_looks_on_target(current: str, target: str) -> bool:
        if not current or not target:
            return False
        if current in ("about:blank", "chrome://newtab/", "edge://newtab/"):
            return False
        try:
            from urllib.parse import urlparse

            a, b = urlparse(current), urlparse(target)
            if (a.scheme or "http") and (b.netloc or "").lower() and (a.netloc or "").lower() == (b.netloc or "").lower():
                # 同 host 即视为已到达（路径可能因登录跳转略有不同）
                return True
        except Exception:
            pass
        return target.rstrip("/") in current or current.rstrip("/") in target

    already_on = _url_looks_on_target(cur_url, start_url) if start_url else (
        bool(cur_url) and cur_url not in ("about:blank", "chrome://newtab/", "edge://newtab/")
    )
    # 平台侧再清一次空白标签，避免 Hermes navigate 前已有多余 NTP
    try:
        from web_capture.cdp_browser import close_blank_cdp_targets, _snap as _cdp_snap

        port = int((_cdp_snap() or {}).get("debug_port") or 0)
        close_blank_cdp_targets(port, keep_url_substr=cur_url or start_url or "")
    except Exception:
        pass

    # 注入平台已采集的 DOM（JS 可交互控件），减少对 snapshot/navigate 的依赖
    dom_pack = ""
    try:
        from ai_external_browser_bridge import get_dom_context_pack, get_page_snapshot

        dom_pack = (get_dom_context_pack() or "").strip()
        if not dom_pack:
            dom_pack = (get_page_snapshot() or "").strip()
    except Exception:
        dom_pack = ""

    if already_on and cur_url:
        instr = (
            f"【当前浏览器状态】URL={cur_url}，标题={cur_title}。\n"
            f"**禁止** browser_navigate / skill_view / terminal / 新开标签（平台已导航成功，再 navigate=重复造轮子）。\n"
            f"优先使用下方 DOM 控件清单直接 click/type；"
            f"仅当清单不足以定位时，才允许 **一次** browser_snapshot（DOM/a11y ref，非视觉）。\n\n"
            + (f"【页面 DOM/可交互控件】\n{dom_pack[:6000]}\n\n" if dom_pack else "")
            + instr
        )
    elif start_url:
        instr = (
            f"【起始 URL】{start_url}\n"
            f"平台通常已预导航；若指令含「当前浏览器状态」或 DOM 清单，**禁止** browser_navigate。\n"
            f"仅当确认仍在 about:blank 时，允许 **仅一次** browser_navigate 到该地址；到达后禁止再 navigate。\n"
            f"勿新开空白标签。优先 DOM 清单；snapshot 仅作难定位兜底。\n\n"
            + (f"【页面 DOM/可交互控件】\n{dom_pack[:6000]}\n\n" if dom_pack else "")
            + instr
        )
    elif dom_pack:
        instr = f"【页面 DOM/可交互控件】\n{dom_pack[:6000]}\n\n" + instr

    # 供熔断：已在目标页时，navigate 出现 1 次即中止
    meta["hermes_already_on_page"] = bool(already_on)
    meta["hermes_forbid_navigate"] = bool(already_on)

    plat = (getattr(params, "platform_type", None) or "auto") if params else "auto"
    vision_summary = ""

    # 纯 web / 含 URL 的 auto：不拉桌面 gateway，避免桌面侧车干扰 Hermes
    _need_desktop_gw = plat in ("desktop", "all", "cross") or (
        plat == "auto" and not start_url
    )
    if _need_desktop_gw:
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
            if plat == "web" or (start_url and plat in ("auto", "web")):
                ctx.active_surface = "web"
            elif plat == "desktop":
                ctx.active_surface = "desktop"
            ctx_prefix = ctx.instruction_prefix()
            if plat == "desktop":
                try:
                    import os as _os

                    _os.environ["DESKTOP_AGENT_SESSION_ID"] = ctx.desktop_session_id
                except Exception:
                    pass
    except Exception:
        pass

    # 外层已路由为 web 时，强制 Hermes 网页专用指令（避免 auto 混入桌面）
    explore_plat = "web" if (plat == "web" or (start_url and plat == "auto")) else plat
    if explore_plat == "auto" and start_url:
        explore_plat = "web"

    try:
        from hermes_skill_hints import build_explore_instruction

        instr = build_explore_instruction(
            instr,
            {
                "platform": explore_plat,
                "context_prefix": ctx_prefix,
                "vision_summary": vision_summary,
                "capabilities_summary": getattr(params, "capabilities_summary", None) or "",
                "start_url": start_url,
                "already_on_target_page": already_on,
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

    hermes_system = ""
    if explore_plat == "web":
        hermes_system = _web_hermes_system_prompt()

    try:
        result_text = None
        traces: List[str] = []
        tool_events: List[Dict[str, Any]] = []
        # Hermes 同名工具死循环熔断（skill_view / terminal / browser_navigate / 连续 snapshot）
        _rep_name = ""
        _rep_count = 0
        _forbid_nav = bool(meta.get("hermes_forbid_navigate"))
        _REP_LIMIT = 2
        _REP_WATCH = frozenset(
            {
                "terminal",
                "bash",
                "shell",
                "skill_view",
                "browser_navigate",
                "browser_goto",
                "navigate",
                "browser_snapshot",
            }
        )
        _NAV_NAMES = frozenset({"browser_navigate", "browser_goto", "navigate"})

        def _note_hermes_tool_name(raw: str) -> Optional[str]:
            nonlocal _rep_name, _rep_count
            n = (raw or "").strip().lower()
            if n.startswith("hermes:"):
                n = n.split(":", 1)[-1].strip()
            # "browser_navigate(...)" / "terminal"
            n = re.split(r"[\s(/]", n, maxsplit=1)[0].strip()
            if not n:
                return None
            # 「Hermes 开始执行」等非工具轨迹不计入
            if n in ("hermes", "start", "trace", "hint", "tool", "tool_progress"):
                return None
            if "开始执行" in (raw or ""):
                return None
            watch = n in _REP_WATCH or any(n.startswith(w) for w in _REP_WATCH)
            if not watch:
                _rep_name = ""
                _rep_count = 0
                return None
            if n == _rep_name:
                _rep_count += 1
            else:
                _rep_name = n
                _rep_count = 1
            # 已在目标页：任意一次 navigate 即视为重复造轮子
            limit = 1 if (_forbid_nav and n in _NAV_NAMES) else _REP_LIMIT
            # snapshot 连续 2 次无动作也熔断（应基于 DOM 直接操作）
            if _rep_count >= limit:
                return n
            return None

        def _halt_tool_loop(looped: str) -> str:
            err = (
                f"智能体工具「{looped}」连续调用仍无进展，已中止（非用户取消）。"
                "网页任务应优先用平台注入的 DOM 控件清单 click/type；"
                "browser_snapshot 仅难定位时用一次（DOM ref，非视觉）；"
                "禁止 skill_view / 反复 browser_navigate。"
            )
            meta["hermes_tool_loop_blocked"] = True
            meta["hermes_tool_loop_error"] = err
            meta["hermes_stream_blocked"] = True
            meta["hermes_stream_error"] = err
            meta["hermes_failed"] = True
            meta["savable"] = False
            meta["failed"] = True
            return json.dumps(
                {
                    "ok": False,
                    "error": err,
                    "tool_loop": True,
                    "loop_tool": looped,
                    "hint": "请用中文向用户说明死循环已停止；禁止再次 hermes_execute；禁止说用户取消。",
                },
                ensure_ascii=False,
            )

        if hasattr(agent_client, "execute_user_instruction_stream"):
            for ev_kind, ev_payload in agent_client.execute_user_instruction_stream(
                instr,
                hermes_sid,
                abort_event=abort_event,
                system_prompt=hermes_system,
            ):
                if params is not None and _deadline_exceeded(params):
                    if abort_event is not None:
                        setattr(abort_event, "_timed_out", True)
                        setattr(abort_event, "_abort_reason", "timeout")
                        abort_event.set()
                    result_text = json.dumps(
                        {
                            "ok": False,
                            "error": "任务已超过设定超时，Hermes 跨层执行已中止",
                            "timeout": True,
                        },
                        ensure_ascii=False,
                    )
                    break
                if abort_event is not None and abort_event.is_set():
                    result_text = json.dumps(
                        {
                            "ok": False,
                            "error": _abort_user_message(abort_event, params),
                            "aborted": True,
                        },
                        ensure_ascii=False,
                    )
                    break
                if ev_kind == "trace":
                    msg = str(
                        (ev_payload or {}).get("message")
                        or (ev_payload or {}).get("stage")
                        or ""
                    ).strip()
                    if msg:
                        traces.append(msg[:300])
                        if callable(on_trace):
                            try:
                                on_trace(msg[:300])
                            except Exception:
                                pass
                        looped = _note_hermes_tool_name(msg)
                        if looped:
                            # 切勿 abort_event.set()：会被外层误报成「用户取消」
                            result_text = _halt_tool_loop(looped)
                            break
                elif ev_kind == "tool":
                    if isinstance(ev_payload, dict):
                        tool_events.append(ev_payload)
                        sum_m = str(
                            ev_payload.get("summary")
                            or ev_payload.get("name")
                            or "tool"
                        ).strip()
                        if sum_m:
                            traces.append(sum_m[:300])
                            if callable(on_trace):
                                try:
                                    on_trace(sum_m[:300])
                                except Exception:
                                    pass
                            looped = _note_hermes_tool_name(
                                str(ev_payload.get("name") or sum_m)
                            )
                            if looped:
                                result_text = _halt_tool_loop(looped)
                                break
                elif ev_kind == "tool_events":
                    evs = (ev_payload or {}).get("events") if isinstance(ev_payload, dict) else None
                    if isinstance(evs, list):
                        tool_events.extend([e for e in evs if isinstance(e, dict)])
                elif ev_kind == "error":
                    err_m = str((ev_payload or {}).get("error") or "Hermes 失败")
                    # 网关因 abort 回灌的「用户取消」若实际是超时/死循环，改写文案
                    if "用户取消" in err_m and abort_event is not None:
                        err_m = _abort_user_message(abort_event, params)
                    result_text = json.dumps(
                        {"ok": False, "error": err_m},
                        ensure_ascii=False,
                    )
                    break
                elif ev_kind == "result":
                    result_text = (ev_payload or {}).get("content") or ""
                    more = (ev_payload or {}).get("tool_events")
                    if isinstance(more, list) and more:
                        tool_events = [e for e in more if isinstance(e, dict)] or tool_events
            if traces:
                meta["hermes_traces"] = traces[-40:]
            if tool_events:
                meta["hermes_tool_events"] = tool_events[-80:]
        if result_text is None:
            result_text = agent_client.execute_user_instruction(
                instr, hermes_sid, abort_event=abort_event, system_prompt=hermes_system
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

    # 空流：禁止外层再次 hermes_execute（避免刷「正在跨层执行」到超时）
    if _result_is_stream_empty(result_text):
        meta["hermes_stream_blocked"] = True
        try:
            parsed_se = json.loads(result_text)
        except Exception:
            parsed_se = {}
        if not isinstance(parsed_se, dict):
            parsed_se = {}
        meta["hermes_stream_error"] = (
            parsed_se.get("error")
            or parsed_se.get("reply")
            or "Hermes 空流结束，禁止再次 hermes_execute"
        )
        had_tools = bool(parsed_se.get("had_tool_activity") or meta.get("hermes_tool_events"))
        # 无工具活动或明确 ok=false → 整次失败（不可保存编造用例）
        if (not had_tools) or parsed_se.get("ok") is False:
            meta["hermes_failed"] = True
            meta["savable"] = False
        if parsed_se.get("ok") is not False and not had_tools:
            # 无工具活动的空流视为失败
            parsed_se["ok"] = False
            parsed_se["stream_empty_text"] = True
            parsed_se["hint"] = (
                "空流已确认：禁止再次调用 hermes_execute；"
                "禁止编造可执行 steps/用例 JSON；只用中文说明失败原因与排查建议。"
            )
            result_text = json.dumps(parsed_se, ensure_ascii=False)
        elif "hint" not in parsed_se:
            parsed_se["hint"] = (
                "禁止再次调用 hermes_execute（空流闸）。"
                "禁止编造未实际执行的用例 steps JSON。"
            )
            if "stream_empty_text" not in parsed_se:
                parsed_se["stream_empty_text"] = True
            result_text = json.dumps(parsed_se, ensure_ascii=False)

    # 鉴权失败：若已启用 windows_* 则引导改用细粒度工具，不再自动抢跑微信 fastpath
    if _result_is_auth_fatal(result_text):
        meta["hermes_auth_blocked"] = True
        meta["hermes_failed"] = True
        meta["savable"] = False
        meta["hermes_auth_error"] = _auth_fatal_user_message(result_text)
        fallback_note = ""
        user_msg = ""
        if params is not None:
            user_msg = (getattr(params, "message", None) or "").strip()
        windows_enabled = False
        if params is not None:
            if getattr(params, "allow_desktop_windows_tools", None) is True:
                windows_enabled = True
            elif getattr(params, "allow_desktop_windows_tools", None) is None:
                windows_enabled = _should_enable_desktop_windows_tools(
                    getattr(params, "platform_type", "") or "auto",
                    user_msg,
                )
        if windows_enabled:
            try:
                parsed = json.loads(result_text)
            except Exception:
                parsed = {"raw": result_text[:800]}
            if not isinstance(parsed, dict):
                parsed = {"raw": str(parsed)[:800]}
            parsed["ok"] = False
            parsed["auth_fatal"] = True
            parsed["error"] = meta["hermes_auth_error"]
            parsed["hint"] = (
                "智能体鉴权/上游模型不可用。请用中文向用户说明："
                + meta["hermes_auth_error"]
                + " 禁止再次调用 hermes_execute；不要提及环境变量名。"
            )
            result_text = json.dumps(parsed, ensure_ascii=False)
        else:
            try:
                from agent_desktop_fastpath import is_desktop_nl_task, execute_desktop_nl

                if user_msg and is_desktop_nl_task(user_msg):
                    # 仅当显式开启 DESKTOP_NL_FASTPATH 才走平台旁路；默认不抢跑应用宏
                    import os as _os_fb

                    if _os_fb.environ.get("DESKTOP_NL_FASTPATH", "0").strip().lower() in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    ):
                        desk = execute_desktop_nl(user_msg)
                        if desk.get("ok") or desk.get("steps") or desk.get("reply"):
                            result_text = json.dumps(
                                {
                                    "ok": bool(desk.get("ok")) and not desk.get("partial"),
                                    "partial": bool(desk.get("partial") or not desk.get("ok")),
                                    "via": desk.get("via") or "platform_desktop_fallback",
                                    "reply": desk.get("reply")
                                    or desk.get("error")
                                    or "桌面旁路已执行（请结合逐步结果确认，勿当作已核验成功）。",
                                    "steps": desk.get("steps") or [],
                                    "step_results": desk.get("step_results") or [],
                                    "hermes_auth_blocked": True,
                                    "hermes_auth_error": meta["hermes_auth_error"],
                                    "hint": (
                                        "平台已完成本机桌面兜底。请把 reply 原样告知用户；"
                                        "不要编造「已输入」；不要再调用 hermes_execute。"
                                    ),
                                    "_desktop_fallback_done": True,
                                },
                                ensure_ascii=False,
                            )
                            fallback_note = "platform_desktop_fallback"
                            meta["desktop_fallback_reply"] = desk.get("reply") or ""
                            meta["desktop_fallback_steps"] = desk.get("steps") or []
                            meta["desktop_fallback_step_results"] = desk.get("step_results") or []
                            meta["desktop_fallback_partial"] = bool(
                                desk.get("partial") or not desk.get("ok")
                            )
                        else:
                            fallback_note = desk.get("error") or "desktop_fallback_failed"
                    else:
                        fallback_note = "desktop_fastpath_disabled"
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
                parsed["hint"] = (
                    "鉴权失败已确认：禁止再次调用 hermes_execute；"
                    "请用中文向用户说明原因；禁止编造可执行 steps / 用例 JSON。"
                )
                result_text = json.dumps(parsed, ensure_ascii=False)

    after_state = _get_bridge_page_state()
    result_text = _inject_execution_env_verify(
        result_text, before_state, after_state, platform_type=plat
    )

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
    tools = chat_tool_schemas(
        allow_hermes=allow_agent,
        platform_type=plat,
        allow_screen_tools=bool(getattr(params, "allow_screen_tools", False)),
        allow_desktop_windows_tools=getattr(params, "allow_desktop_windows_tools", None),
        message=params.message or "",
        allow_refine_test_plan=(
            bool(params.allow_refine_test_plan)
            if getattr(params, "allow_refine_test_plan", None) is not None
            else True
        ),
    )
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
        generate_case_after_run=bool(getattr(params, "generate_case_after_run", False)),
    )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(
        _history_to_messages(params.history, local_ai_service._sanitize_chat_history_for_prompt)
    )
    messages.append({"role": "user", "content": params.message})

    last_plan: Dict[str, Any] = dict(params.current_plan) if isinstance(params.current_plan, dict) else {}
    meta: Dict[str, Any] = {
        "tool_rounds": 0,
        "tools_used": [],
        "succeeded_action_fps": [],
        "desktop_phase": "start",
        "flow_profile": _resolve_desktop_flow_profile(
            getattr(params, "message", "") or "", plat
        ),
        "obs_count": 0,
        "typed_texts": [],
        "focused_apps": [],
        "search_ui_done": False,
        "repair_forward_only": False,
    }
    prof: Optional[Dict[str, Any]] = params.profile if isinstance(params.profile, dict) else None
    max_result = agent_tool_result_max_chars()
    _abort = abort_event or params.abort_event

    from windows_desktop_tools import SCREEN_TOOL_NAMES, WINDOWS_TOOL_NAMES

    for round_idx in range(_max_tool_rounds()):
        if _abort is not None and _abort.is_set():
            raise InterruptedError(_abort_user_message(_abort, params))
        if _deadline_exceeded(params):
            raise InterruptedError("任务已超过设定超时时间")
        if _desktop_flow_should_stop(meta):
            meta["final_round"] = round_idx
            meta["savable"] = False
            reply = _desktop_halt_user_facing(
                str(meta.get("desktop_last_failed_tool") or "windows_*"),
                json.dumps(
                    {"error": meta.get("desktop_last_error") or "", "suggestion": ""},
                    ensure_ascii=False,
                ),
            )
            meta["halt_reply"] = reply
            return {}, [], meta
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
            if meta.get("hermes_failed") or meta.get("savable") is False:
                meta["final_round"] = round_idx
                meta["chat_reply"] = True
                meta["failed"] = True
                meta["savable"] = False
                meta["reply_text"] = text
                return {}, [], meta
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

        pending_calls = [tc for tc in tool_calls if isinstance(tc, dict)]
        idx_tc = 0
        while idx_tc < len(pending_calls):
            tc = pending_calls[idx_tc]
            idx_tc += 1
            fn = tc.get("function") or {}
            name = (fn.get("name") or "").strip()
            tid = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            raw_args = fn.get("arguments") if isinstance(fn, dict) else ""
            if not isinstance(raw_args, str):
                raw_args = json.dumps(raw_args, ensure_ascii=False) if raw_args is not None else ""
            args = _parse_tool_arguments(raw_args)
            result_text = ""

            if name in ("hermes_execute", "openclaw_execute"):
                if _hermes_retry_blocked(meta):
                    result_text = _hermes_retry_blocked_payload(meta)
                    meta["tools_used"].append(f"{name}_retry_blocked")
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
                _allow_ref = getattr(params, "allow_refine_test_plan", None)
                if _allow_ref is None:
                    _allow_ref = True
                if not _allow_ref:
                    result_text = json.dumps(
                        {
                            "ok": False,
                            "skipped": True,
                            "error": "当前任务不走二次 LLM 润色用例",
                            "hint": (
                                "用例将由平台从动作轨迹自动生成；请直接用中文汇报执行结果。"
                                if bool(getattr(params, "generate_case_after_run", False))
                                else "请直接用中文汇报执行结果并结束。"
                            ),
                        },
                        ensure_ascii=False,
                    )
                    meta["tools_used"].append("refine_test_plan_skipped")
                else:
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
            elif name in WINDOWS_TOOL_NAMES or name in SCREEN_TOOL_NAMES:
                skip_json = _should_skip_replay_desktop_tool(name, args or {}, meta)
                if skip_json:
                    result_text = skip_json
                    meta["tools_used"].append(f"{name}_skipped_replay")
                else:
                    call_args = dict(args or {})
                    if (
                        name == "windows_type_text"
                        and str(meta.get("desktop_phase") or meta.get("wechat_phase") or "")
                        in ("item_selected", "compose", "body_typed", "chat_open")
                    ):
                        call_args.setdefault("field", "compose")
                    result_text = _dispatch_desktop_or_screen_tool(name, call_args)
                    meta["tools_used"].append(name)
                    _record_succeeded_desktop_action(meta, name, call_args, result_text)
            else:
                result_text = json.dumps({"ok": False, "error": f"未知工具 {name}"}, ensure_ascii=False)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": result_text,
                }
            )
            if name == "windows_click_element":
                auto = _auto_type_contact_after_search_click(
                    params=params, meta=meta, click_result_text=result_text
                )
                if auto:
                    contact, type_json = auto
                    auto_tid = f"call_auto_{uuid.uuid4().hex[:10]}"
                    messages.append(
                        {"role": "tool", "tool_call_id": auto_tid, "content": type_json}
                    )
                    type_ok = False
                    try:
                        type_ok = bool(json.loads(type_json).get("success"))
                    except Exception:
                        pass
                    if type_ok:
                        enter_json = _auto_open_wechat_search_hit_after_type(
                            meta=meta, type_result_json=type_json
                        )
                        enter_ok = False
                        if enter_json:
                            enter_tid = f"call_auto_{uuid.uuid4().hex[:10]}"
                            messages.append(
                                {"role": "tool", "tool_call_id": enter_tid, "content": enter_json}
                            )
                            try:
                                enter_ok = bool(json.loads(enter_json).get("success"))
                            except Exception:
                                enter_ok = False
                        if enter_ok:
                            next_hint = (
                                f"[System] 平台已输入搜索词「{contact}」并 Enter 确认首条结果。"
                                "请继续主界面操作（输入正文/提交）；禁止回退重搜或重复已成功步骤。"
                            )
                        else:
                            next_hint = (
                                f"[System] 平台已在搜索框输入「{contact}」。"
                                "请优先 windows_press_key('Enter') 确认首条结果；"
                                "不要重复输入同一关键词。"
                            )
                        messages.append({"role": "user", "content": next_hint})
                    else:
                        meta["desktop_flow_halted"] = True
                        meta["desktop_last_failed_tool"] = "windows_type_text"
                        meta["failed"] = True
                        meta["partial"] = True
                        meta["savable"] = False
                        messages.append(
                            {
                                "role": "user",
                                "content": _desktop_fail_stop_message(
                                    "windows_type_text", type_json, meta=meta
                                ),
                            }
                        )
                        meta["final_round"] = round_idx
                        meta["halt_reply"] = _desktop_halt_user_facing(
                            "windows_type_text", type_json
                        )
                        return {}, [], meta
            if name in ("hermes_execute", "openclaw_execute") and _hermes_retry_blocked(meta):
                if meta.get("hermes_stream_blocked") and not meta.get("hermes_auth_blocked"):
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[System] Hermes 空流已确认。禁止再次调用 hermes_execute（避免空转至超时）。"
                                "请用中文向用户说明：未见可用工具轨迹或无文本摘要；"
                                "建议检查 computer_use / MCP / Gateway 后由用户重发。"
                                "禁止输出任何用例 JSON / steps（含「供参考」）。"
                            ),
                        }
                    )
                else:
                    win_on = _should_enable_desktop_windows_tools(
                        getattr(params, "platform_type", "") or "auto",
                        getattr(params, "message", "") or "",
                    ) or getattr(params, "allow_desktop_windows_tools", None) is True
                    tip = (
                        "请改用已注册的 windows_* / get_screen_* 完成本机桌面任务，或用中文向用户说明原因；"
                        if win_on
                        else (
                            "请用中文向用户说明："
                            + (meta.get("hermes_auth_error") or "智能体鉴权失败，请停止并重新启动智能体")
                            + "；不要提及环境变量。"
                        )
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[System] 鉴权失败已确认（401）。禁止再次调用 hermes_execute。"
                                + tip
                                + "不要重复描述同一鉴权错误多次。"
                            ),
                        }
                    )

            if name in WINDOWS_TOOL_NAMES and _desktop_tool_failed(result_text):
                meta["desktop_flow_halted"] = True
                meta["desktop_last_failed_tool"] = name
                try:
                    _ed = json.loads(result_text or "{}")
                    meta["desktop_last_error"] = str((_ed or {}).get("error") or "")[:300]
                except Exception:
                    meta["desktop_last_error"] = ""
                meta["failed"] = True
                meta["partial"] = True
                meta["savable"] = False
                while idx_tc < len(pending_calls):
                    skip = pending_calls[idx_tc]
                    idx_tc += 1
                    sfn = skip.get("function") or {}
                    sname = (sfn.get("name") or "").strip() or "tool"
                    sid = skip.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                    blocked = json.dumps(
                        {
                            "success": False,
                            "ok": False,
                            "flow_halt": True,
                            "error": f"已取消：因上一步 `{name}` 失败，不再执行 `{sname}`",
                        },
                        ensure_ascii=False,
                    )
                    messages.append({"role": "tool", "tool_call_id": sid, "content": blocked})
                    meta["tools_used"].append(f"{sname}_flow_halted")
                stop_msg = _desktop_fail_stop_message(name, result_text, meta=meta)
                messages.append({"role": "user", "content": stop_msg})
                meta["final_round"] = round_idx
                meta["halt_reply"] = _desktop_halt_user_facing(name, result_text)
                return {}, [], meta

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
    tools = chat_tool_schemas(
        allow_hermes=allow_agent,
        platform_type=plat,
        allow_screen_tools=bool(getattr(params, "allow_screen_tools", False)),
        allow_desktop_windows_tools=getattr(params, "allow_desktop_windows_tools", None),
        message=params.message or "",
        allow_refine_test_plan=(
            bool(params.allow_refine_test_plan)
            if getattr(params, "allow_refine_test_plan", None) is not None
            else True
        ),
    )
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
        generate_case_after_run=bool(getattr(params, "generate_case_after_run", False)),
    )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(
        _history_to_messages(params.history, local_ai_service._sanitize_chat_history_for_prompt)
    )
    messages.append({"role": "user", "content": params.message})
    # 轻量桌面记忆 hint（若有）
    try:
        from desktop_run_memory import hint_for_app

        mem_hint = hint_for_app((params.message or "")[:80])
        if mem_hint:
            messages.append({"role": "user", "content": f"[System] {mem_hint}"})
    except Exception:
        pass

    last_plan: Dict[str, Any] = dict(params.current_plan) if isinstance(params.current_plan, dict) else {}
    meta: Dict[str, Any] = {
        "tool_rounds": 0,
        "tools_used": [],
        "succeeded_action_fps": [],
        "desktop_phase": "start",
        "flow_profile": _resolve_desktop_flow_profile(
            getattr(params, "message", "") or "", plat
        ),
        "obs_count": 0,
        "typed_texts": [],
        "focused_apps": [],
        "search_ui_done": False,
        "repair_forward_only": False,
    }
    prof: Optional[Dict[str, Any]] = params.profile if isinstance(params.profile, dict) else None
    max_result = agent_tool_result_max_chars()
    _abort = abort_event or params.abort_event

    from windows_desktop_tools import SCREEN_TOOL_NAMES, WINDOWS_TOOL_NAMES

    # 立刻给前端反馈，避免用户空等首轮 LLM 十几秒
    yield (
        "thinking",
        {
            "round": 0,
            "content": "已接到指令，正在调用模型规划步骤…",
        },
    )

    for round_idx in range(_max_tool_rounds()):
        if _abort is not None and _abort.is_set():
            yield ("error", _abort_user_message(_abort, params))
            return
        if _deadline_exceeded(params):
            if _abort is not None:
                setattr(_abort, "_timed_out", True)
                setattr(_abort, "_abort_reason", "timeout")
                _abort.set()
            yield ("error", "任务已超过设定的超时时间，已自动停止")
            return
        if _desktop_flow_should_stop(meta):
            reply = meta.get("halt_reply") or _desktop_halt_user_facing(
                str(meta.get("desktop_last_failed_tool") or "windows_*"),
                json.dumps(
                    {"error": meta.get("desktop_last_error") or ""},
                    ensure_ascii=False,
                ),
            )
            meta["final_round"] = round_idx
            meta["savable"] = False
            yield ("thinking", {"round": round_idx, "content": "步骤失败，任务已停止"})
            yield ("reply", {"text": reply})
            yield (
                "done",
                {
                    "total_rounds": round_idx + 1,
                    "plan": {},
                    "meta": meta,
                    "reply": reply,
                    "failed": True,
                    "savable": False,
                    "partial": True,
                },
            )
            return

        if round_idx == 0:
            yield ("thinking", {"round": round_idx, "content": "模型推理中（首轮通常数秒，请稍候）…"})
        else:
            yield ("thinking", {"round": round_idx, "content": f"第 {round_idx + 1} 轮推理…"})

        phase_note = _desktop_progress_reminder(meta)
        if phase_note and not meta.get("_phase_note_injected"):
            messages.append({"role": "user", "content": phase_note})
            meta["_phase_note_injected"] = True
            yield ("thinking", {"round": round_idx, "content": "进度已锁定，禁止回退重跑已成功步骤"})
        elif phase_note and round_idx > 0:
            if not any(
                isinstance(m, dict)
                and m.get("role") == "user"
                and "进度锁定" in str(m.get("content") or "")
                for m in messages[-3:]
            ):
                messages.append({"role": "user", "content": phase_note})

        # 流式调用 LLM：HTTP 超时不超过任务剩余时间
        content_buf = ""
        assistant_msg: Optional[Dict[str, Any]] = None
        rem = _remaining_deadline_sec(params)
        llm_timeout = int(os.environ.get("LOCAL_LLM_TIMEOUT", "240") or 240)
        if rem is not None:
            llm_timeout = max(5, min(llm_timeout, int(rem)))
        last_think_len = 0
        announced_tools: set = set()
        try:
            for evt_type, evt_data in dispatch_chat_stream(
                messages, tools, prof, local_ai_service,
                temperature=0.2, timeout=llm_timeout, abort_event=_abort,
            ):
                if evt_type == "content_delta":
                    content_buf += evt_data
                    # 每累计约 24 字推一次思考摘要，避免长时间无 UI 更新
                    if len(content_buf) - last_think_len >= 24:
                        last_think_len = len(content_buf)
                        snippet = content_buf.strip().replace("\n", " ")
                        if len(snippet) > 120:
                            snippet = snippet[:117] + "…"
                        if snippet:
                            yield ("thinking", {"round": round_idx, "content": f"思考：{snippet}"})
                elif evt_type == "tool_call_delta":
                    tname = (evt_data or {}).get("name") or ""
                    if tname and tname not in announced_tools:
                        announced_tools.add(tname)
                        yield (
                            "thinking",
                            {
                                "round": round_idx,
                                "content": f"准备执行工具：{tname}…",
                            },
                        )
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
            # 执行失败（空流/鉴权等）：禁止把回复里夹带的「供参考」JSON 解析成可保存用例
            if meta.get("hermes_failed") or meta.get("savable") is False:
                meta["final_round"] = round_idx
                meta["chat_reply"] = True
                meta["failed"] = True
                meta["savable"] = False
                meta["partial"] = False
                clean_text = _strip_invented_case_json(text)
                yield ("reply", {"text": clean_text})
                yield (
                    "done",
                    {
                        "total_rounds": round_idx + 1,
                        "plan": {},
                        "meta": meta,
                        "reply": clean_text,
                        "failed": True,
                        "savable": False,
                        "partial": False,
                    },
                )
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
                _maybe_persist_desktop_run_memory(
                    meta, message=params.message or "", failed=False
                )
                yield ("plan_update", {"plan": normalized, "step_count": n})
                yield (
                    "done",
                    {
                        "total_rounds": round_idx + 1,
                        "plan": normalized,
                        "meta": meta,
                        "reply": "",
                        "savable": True,
                        "failed": False,
                        "partial": bool(meta.get("partial")),
                    },
                )
                return
            except ValueError:
                meta["final_round"] = round_idx
                meta["chat_reply"] = True
                _maybe_persist_desktop_run_memory(
                    meta,
                    message=params.message or "",
                    failed=bool(meta.get("hermes_failed") or meta.get("failed")),
                )
                yield ("reply", {"text": text})
                yield (
                    "done",
                    {
                        "total_rounds": round_idx + 1,
                        "plan": last_plan if meta.get("savable") is not False else {},
                        "meta": meta,
                        "reply": text,
                        "savable": meta.get("savable") is not False and bool((last_plan or {}).get("steps")),
                        "failed": bool(meta.get("hermes_failed")),
                        "partial": bool(meta.get("partial")),
                    },
                )
                return

        # 有 tool calls
        meta["tool_rounds"] = int(meta["tool_rounds"]) + 1
        messages.append({"role": "assistant", "content": content if content else None, "tool_calls": tool_calls})

        if not isinstance(tool_calls, list):
            tool_calls = []

        # 同轮多工具：按序执行；任一关键桌面步骤失败则取消后续（流程闸）
        pending_calls = [tc for tc in tool_calls if isinstance(tc, dict)]
        idx_tc = 0
        while idx_tc < len(pending_calls):
            tc = pending_calls[idx_tc]
            idx_tc += 1
            if _abort is not None and _abort.is_set():
                yield ("error", _abort_user_message(_abort, params))
                return
            if _deadline_exceeded(params):
                if _abort is not None:
                    setattr(_abort, "_timed_out", True)
                    setattr(_abort, "_abort_reason", "timeout")
                    _abort.set()
                yield ("error", "任务已超过设定的超时时间，已自动停止")
                return
            fn = tc.get("function") or {}
            name = (fn.get("name") or "").strip()
            tid = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            raw_args = fn.get("arguments") if isinstance(fn, dict) else ""
            if not isinstance(raw_args, str):
                raw_args = json.dumps(raw_args, ensure_ascii=False) if raw_args is not None else ""
            args = _parse_tool_arguments(raw_args)
            result_text = ""

            # 通知前端 tool call 开始
            args_summary = (
                args.get("instruction")
                or args.get("adjustment")
                or args.get("description")
                or args.get("app_name")
                or (f"{args.get('contact')} ← {args.get('text')}" if args.get("contact") else None)
                or args.get("text")
                or args.get("key")
                or args.get("hint")
                or str(list(args.keys()))
            )
            yield ("tool_call_start", {"round": round_idx, "tool": name, "args_summary": str(args_summary)[:200]})

            if name in ("hermes_execute", "openclaw_execute"):
                if _hermes_retry_blocked(meta):
                    result_text = _hermes_retry_blocked_payload(meta)
                    meta["tools_used"].append(f"{name}_retry_blocked")
                    yield ("tool_call_result", {
                        "round": round_idx, "tool": name,
                        "result_preview": result_text[:500],
                    })
                    messages.append({"role": "tool", "tool_call_id": tid, "content": result_text})
                    halt = (
                        meta.get("hermes_tool_loop_error")
                        or meta.get("hermes_stream_error")
                        or meta.get("hermes_auth_error")
                        or "智能体执行已中止"
                    )
                    yield ("reply", {"text": halt})
                    yield (
                        "done",
                        {
                            "total_rounds": round_idx + 1,
                            "plan": {},
                            "meta": meta,
                            "reply": halt,
                            "failed": True,
                            "savable": False,
                            "partial": False,
                        },
                    )
                    return
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
                result_text = ""
                _trace_q: Any = None
                try:
                    import queue as _queue

                    _trace_q = _queue.Queue()
                except Exception:
                    _trace_q = None

                def _on_hermes_trace(msg: str) -> None:
                    if _trace_q is not None:
                        try:
                            _trace_q.put_nowait(str(msg or "")[:300])
                        except Exception:
                            pass

                _holder: Dict[str, Any] = {"text": "", "err": None}

                def _hermes_worker() -> None:
                    try:
                        _holder["text"] = _handle_agent_execute(
                            name=name,
                            args=args,
                            allow_agent=allow_agent,
                            agent_client=agent_client,
                            meta=meta,
                            abort_event=_abort,
                            params=params,
                            on_trace=_on_hermes_trace if _trace_q is not None else None,
                        )
                    except Exception as _hex:
                        _holder["err"] = _hex
                        _holder["text"] = json.dumps(
                            {"ok": False, "error": str(_hex)[:400]},
                            ensure_ascii=False,
                        )
                    finally:
                        if _trace_q is not None:
                            try:
                                _trace_q.put_nowait(None)
                            except Exception:
                                pass

                _ht = threading.Thread(target=_hermes_worker, daemon=True)
                _ht.start()
                _live_trace_n = 0
                while _ht.is_alive() or (_trace_q is not None and not _trace_q.empty()):
                    if _deadline_exceeded(params):
                        if _abort is not None:
                            setattr(_abort, "_timed_out", True)
                            setattr(_abort, "_abort_reason", "timeout")
                            _abort.set()
                        yield ("error", "任务已超过设定的超时时间，已自动停止")
                        return
                    msg_tr = None
                    if _trace_q is not None:
                        try:
                            msg_tr = _trace_q.get(timeout=0.4)
                        except Exception:
                            msg_tr = "__wait__"
                    else:
                        _ht.join(timeout=0.4)
                        continue
                    if msg_tr is None:
                        break
                    if msg_tr == "__wait__":
                        continue
                    if msg_tr:
                        _live_trace_n += 1
                        yield ("thinking", {"round": round_idx, "content": f"Hermes: {msg_tr}"})
                        yield ("hermes_trace", {"round": round_idx, "message": msg_tr, "tool": name})
                _ht.join(timeout=2.0)
                result_text = str(_holder.get("text") or "")
                if _live_trace_n == 0:
                    for tr in (meta.get("hermes_traces") or [])[-20:]:
                        yield ("thinking", {"round": round_idx, "content": f"Hermes: {tr}"})
                        yield ("hermes_trace", {"round": round_idx, "message": tr, "tool": name})
                meta.pop("hermes_traces", None)
                # 真实工具轨迹 → 动作卡（禁止散文猜测）
                tool_evs = meta.pop("hermes_tool_events", None) or []
                if tool_evs:
                    from ai_action_recorder import ActionRecorder as _AR

                    rec_tmp = params.recorder if params.recorder else _AR()
                    out_recs = []
                    for te in tool_evs[-40:]:
                        if not isinstance(te, dict):
                            continue
                        te_status = str(te.get("status") or "").strip().lower()
                        if te_status in ("running", "in_progress", "started", "progress"):
                            if te.get("result") is None and te.get("sse_event") == "tool_calls_delta":
                                continue
                            if te.get("result") is None and not te.get("args"):
                                continue
                        try:
                            new_recs = rec_tmp.capture_from_tool_event(
                                name=str(te.get("name") or "tool"),
                                args=te.get("args") if isinstance(te.get("args"), dict) else {},
                                result=te.get("result"),
                                status=str(te.get("status") or ""),
                            )
                            for r in new_recs:
                                st = (r.status or "warning").strip().lower()
                                if st in ("running", "in_progress", "started", "progress"):
                                    continue
                                if st in ("fail", "error", "failed"):
                                    st = "failed"
                                elif st in ("ok", "done", "success", "completed", "complete"):
                                    st = "success"
                                elif st not in ("warning", "skipped"):
                                    st = "warning"
                                out_recs.append(
                                    {
                                        "action_type": r.action_type,
                                        "target": r.target,
                                        "status": st,
                                        "result": (r.result or "")[:100],
                                        "has_vision": False,
                                        "env_verify": None,
                                    }
                                )
                        except Exception:
                            continue
                    if out_recs:
                        yield ("action_records", out_recs)
                # Hermes 工具死循环：立即向用户说明并结束，禁止再开一轮被误报成「用户取消」
                if meta.get("hermes_tool_loop_blocked"):
                    halt = meta.get("hermes_tool_loop_error") or "智能体因工具死循环已中止"
                    yield ("tool_call_result", {
                        "round": round_idx, "tool": name,
                        "result_preview": (result_text or "")[:500],
                    })
                    messages.append({"role": "tool", "tool_call_id": tid, "content": result_text})
                    yield ("reply", {"text": halt})
                    yield (
                        "done",
                        {
                            "total_rounds": round_idx + 1,
                            "plan": {},
                            "meta": meta,
                            "reply": halt,
                            "failed": True,
                            "savable": False,
                            "partial": False,
                        },
                    )
                    return
            elif name == "refine_test_plan":
                _allow_ref = getattr(params, "allow_refine_test_plan", None)
                if _allow_ref is None:
                    _allow_ref = True
                if not _allow_ref:
                    result_text = json.dumps(
                        {
                            "ok": False,
                            "skipped": True,
                            "error": "当前任务不走二次 LLM 润色用例",
                            "hint": (
                                "用例将由平台从动作轨迹自动生成；请直接用中文汇报执行结果。"
                                if bool(getattr(params, "generate_case_after_run", False))
                                else "请直接用中文汇报执行结果并结束。"
                            ),
                        },
                        ensure_ascii=False,
                    )
                    meta["tools_used"].append("refine_test_plan_skipped")
                else:
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
            elif name in WINDOWS_TOOL_NAMES or name in SCREEN_TOOL_NAMES:
                skip_json = _should_skip_replay_desktop_tool(name, args or {}, meta)
                if skip_json:
                    result_text = skip_json
                    meta["tools_used"].append(f"{name}_skipped_replay")
                else:
                    call_args = dict(args or {})
                    if (
                        name == "windows_type_text"
                        and str(meta.get("desktop_phase") or meta.get("wechat_phase") or "")
                        in ("item_selected", "compose", "body_typed", "chat_open")
                    ):
                        call_args.setdefault("field", "compose")
                    result_text = _dispatch_desktop_or_screen_tool(name, call_args)
                    meta["tools_used"].append(name)
                    _record_succeeded_desktop_action(meta, name, call_args, result_text)
                if name in SCREEN_TOOL_NAMES:
                    try:
                        parsed_vis = json.loads(result_text)
                        preview = ""
                        if isinstance(parsed_vis, dict):
                            preview = (
                                parsed_vis.get("description")
                                or " ".join((parsed_vis.get("texts") or [])[:12])
                                or parsed_vis.get("error")
                                or ""
                            )
                        if preview:
                            yield ("vision_result", {"text": str(preview)[:300]})
                    except Exception:
                        pass
                if name in WINDOWS_TOOL_NAMES:
                    try:
                        parsed_act = json.loads(result_text)
                        ok = bool(isinstance(parsed_act, dict) and parsed_act.get("success"))
                        verified = True
                        if isinstance(parsed_act, dict):
                            if parsed_act.get("verified") is False:
                                verified = False
                            cap = parsed_act.get("capture_after")
                            if isinstance(cap, dict) and cap.get("unchanged"):
                                verified = False
                        tgt = (
                            (parsed_act or {}).get("matched")
                            or (parsed_act or {}).get("app_name")
                            or (parsed_act or {}).get("description")
                            or (parsed_act or {}).get("key")
                            or args.get("description")
                            or args.get("app_name")
                            or args.get("text")
                            or args.get("key")
                            or name
                        )
                        if ok and verified:
                            st = "success"
                        elif ok and not verified:
                            st = "warning"
                        else:
                            st = "failed"
                        yield (
                            "action_records",
                            [
                                {
                                    "action_type": name.replace("windows_", ""),
                                    "target": str(tgt)[:120],
                                    "status": st,
                                    "result": (result_text or "")[:100],
                                    "has_vision": False,
                                    "env_verify": None,
                                }
                            ],
                        )
                    except Exception:
                        pass
            else:
                result_text = json.dumps({"ok": False, "error": f"未知工具 {name}"}, ensure_ascii=False)

            yield ("tool_call_result", {
                "round": round_idx, "tool": name,
                "result_preview": result_text[:500],
            })

            # 点开搜索后立刻自动输入联系人（不等下一轮 LLM，避免焦点被平台抢走）
            if name == "windows_click_element":
                auto = _auto_type_contact_after_search_click(
                    params=params, meta=meta, click_result_text=result_text
                )
                if auto:
                    contact, type_json = auto
                    auto_tid = f"call_auto_{uuid.uuid4().hex[:10]}"
                    yield (
                        "tool_call_start",
                        {
                            "round": round_idx,
                            "tool": "windows_type_text",
                            "args_summary": f"自动输入搜索词：{contact}"[:200],
                        },
                    )
                    type_ok = False
                    try:
                        parsed_type = json.loads(type_json)
                        type_ok = bool(isinstance(parsed_type, dict) and parsed_type.get("success"))
                    except Exception:
                        parsed_type = {}
                    yield (
                        "action_records",
                        [
                            {
                                "action_type": "type_text",
                                "target": contact[:120],
                                "status": "success" if type_ok else "failed",
                                "result": (type_json or "")[:100],
                                "has_vision": False,
                                "env_verify": None,
                            }
                        ],
                    )
                    yield (
                        "tool_call_result",
                        {
                            "round": round_idx,
                            "tool": "windows_type_text",
                            "result_preview": (type_json or "")[:500],
                        },
                    )
                    messages.append(
                        {"role": "tool", "tool_call_id": auto_tid, "content": type_json}
                    )
                    if type_ok:
                        enter_json = _auto_open_wechat_search_hit_after_type(
                            meta=meta, type_result_json=type_json
                        )
                        enter_ok = False
                        if enter_json:
                            yield (
                                "tool_call_start",
                                {
                                    "round": round_idx,
                                    "tool": "windows_press_key",
                                    "args_summary": "自动 Enter 打开首条搜索结果",
                                },
                            )
                            try:
                                parsed_enter = json.loads(enter_json)
                                enter_ok = bool(
                                    isinstance(parsed_enter, dict) and parsed_enter.get("success")
                                )
                            except Exception:
                                parsed_enter = {}
                            yield (
                                "action_records",
                                [
                                    {
                                        "action_type": "press_key",
                                        "target": "Enter",
                                        "status": "success" if enter_ok else "failed",
                                        "result": (enter_json or "")[:100],
                                        "has_vision": False,
                                        "env_verify": None,
                                    }
                                ],
                            )
                            yield (
                                "tool_call_result",
                                {
                                    "round": round_idx,
                                    "tool": "windows_press_key",
                                    "result_preview": (enter_json or "")[:500],
                                },
                            )
                            enter_tid = f"call_auto_{uuid.uuid4().hex[:10]}"
                            messages.append(
                                {"role": "tool", "tool_call_id": enter_tid, "content": enter_json}
                            )
                        if enter_ok:
                            next_hint = (
                                f"[System] 平台已输入搜索词「{contact}」并 Enter 确认首条结果。"
                                "请继续主界面下一步（输入正文/提交）；禁止回退重搜或重复已成功步骤。"
                            )
                            think_msg = f"已输入「{contact}」并确认结果，继续下一步…"
                        else:
                            next_hint = (
                                f"[System] 平台已在搜索框输入「{contact}」。"
                                "请优先 windows_press_key('Enter') 确认首条结果；"
                                "不要重复输入同一关键词，也不要回退重跑已成功步骤。"
                            )
                            think_msg = f"已自动输入「{contact}」，请 Enter 确认…"
                        messages.append({"role": "user", "content": next_hint})
                        yield (
                            "thinking",
                            {
                                "round": round_idx,
                                "content": think_msg,
                            },
                        )
                        # 跳过同轮里模型又发的重复已成功动作
                        while idx_tc < len(pending_calls):
                            nxt = pending_calls[idx_tc]
                            nfn = nxt.get("function") or {}
                            nname = (nfn.get("name") or "").strip()
                            raw_a = nfn.get("arguments") if isinstance(nfn, dict) else ""
                            if not isinstance(raw_a, str):
                                raw_a = json.dumps(raw_a, ensure_ascii=False) if raw_a is not None else ""
                            nargs = _parse_tool_arguments(raw_a)
                            skip_pending = _should_skip_replay_desktop_tool(nname, nargs, meta)
                            if not skip_pending and nname == "windows_type_text":
                                ntext = str(nargs.get("text") or "").strip()
                                if ntext and (ntext == contact or contact in ntext):
                                    skip_pending = json.dumps(
                                        {
                                            "success": True,
                                            "skipped": True,
                                            "reason": "already_auto_typed_search",
                                            "text": contact,
                                        },
                                        ensure_ascii=False,
                                    )
                            if not skip_pending:
                                break
                            idx_tc += 1
                            sid = nxt.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                            skipped = skip_pending
                            yield (
                                "tool_call_start",
                                {
                                    "round": round_idx,
                                    "tool": nname or "windows_tool",
                                    "args_summary": "已跳过（防回退重跑）",
                                },
                            )
                            yield (
                                "tool_call_result",
                                {
                                    "round": round_idx,
                                    "tool": nname or "windows_tool",
                                    "result_preview": skipped[:500],
                                },
                            )
                            messages.append({"role": "tool", "tool_call_id": sid, "content": skipped})
                            meta["tools_used"].append(f"{nname}_skipped_replay")
                    else:
                        meta["desktop_flow_halted"] = True
                        meta["desktop_last_failed_tool"] = "windows_type_text"
                        meta["failed"] = True
                        meta["partial"] = True
                        meta["savable"] = False
                        meta["halt_reply"] = _desktop_halt_user_facing(
                            "windows_type_text", type_json
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": _desktop_fail_stop_message(
                                    "windows_type_text", type_json, meta=meta
                                ),
                            }
                        )
                        yield (
                            "thinking",
                            {
                                "round": round_idx,
                                "content": "自动输入失败，任务已停止",
                            },
                        )
                        # 取消同轮剩余
                        while idx_tc < len(pending_calls):
                            skip = pending_calls[idx_tc]
                            idx_tc += 1
                            sfn = skip.get("function") or {}
                            sname = (sfn.get("name") or "").strip() or "tool"
                            sid = skip.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                            blocked = json.dumps(
                                {
                                    "success": False,
                                    "flow_halt": True,
                                    "error": f"已取消：因自动输入「{contact}」失败",
                                },
                                ensure_ascii=False,
                            )
                            yield (
                                "tool_call_start",
                                {"round": round_idx, "tool": sname, "args_summary": "已取消（流程闸）"},
                            )
                            yield (
                                "tool_call_result",
                                {"round": round_idx, "tool": sname, "result_preview": blocked[:500]},
                            )
                            messages.append({"role": "tool", "tool_call_id": sid, "content": blocked})
                        break

            env_verify = None
            if name in ("hermes_execute", "openclaw_execute"):
                try:
                    parsed = json.loads(result_text)
                    if isinstance(parsed, dict):
                        env_verify = parsed.get("_env_verify")
                except Exception:
                    pass

            # 桌面兜底已执行：把真实 steps 推到前端后提前结束本轮任务
            if (
                name in ("hermes_execute", "openclaw_execute")
                and meta.get("desktop_fallback_steps") is not None
            ):
                fb_steps = meta.get("desktop_fallback_steps") or []
                fb_results = meta.get("desktop_fallback_step_results") or []
                fb_recs = []
                if fb_results:
                    for item in fb_results:
                        if not isinstance(item, dict):
                            continue
                        st = item.get("step") if isinstance(item.get("step"), dict) else {}
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
                                "status": "success" if item.get("ok") else "failed",
                                "result": (item.get("error") or "")[:100],
                                "has_vision": False,
                                "env_verify": env_verify,
                            }
                        )
                else:
                    overall_ok = not bool(meta.get("desktop_fallback_partial"))
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
                                "status": "success" if overall_ok else "failed",
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
                messages.append({"role": "tool", "tool_call_id": tid, "content": result_text})
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

            messages.append({"role": "tool", "tool_call_id": tid, "content": result_text})
            if name in ("hermes_execute", "openclaw_execute") and _hermes_retry_blocked(meta):
                if meta.get("hermes_stream_blocked") and not meta.get("hermes_auth_blocked"):
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[System] Hermes 空流已确认。禁止再次调用 hermes_execute（避免空转至超时）。"
                                "请用中文向用户说明：未见可用工具轨迹或无文本摘要；"
                                "建议检查 computer_use / MCP / Gateway 后由用户重发。"
                                "禁止输出任何用例 JSON / steps（含「供参考」）。"
                            ),
                        }
                    )
                else:
                    win_on = _should_enable_desktop_windows_tools(
                        getattr(params, "platform_type", "") or "auto",
                        getattr(params, "message", "") or "",
                    ) or getattr(params, "allow_desktop_windows_tools", None) is True
                    tip = (
                        "请改用已注册的 windows_* / get_screen_* 完成本机桌面任务，或用中文向用户说明原因；"
                        if win_on
                        else (
                            "请用中文向用户说明："
                            + (meta.get("hermes_auth_error") or "智能体鉴权失败，请停止并重新启动智能体")
                            + "；不要提及环境变量。"
                        )
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[System] 鉴权失败已确认（401）。禁止再次调用 hermes_execute。"
                                + tip
                                + "不要重复描述同一鉴权错误多次。"
                            ),
                        }
                    )

            # —— 流程闸：桌面步骤失败则取消同轮后续工具，并结束整任务 ——
            if name in WINDOWS_TOOL_NAMES and _desktop_tool_failed(result_text):
                meta["desktop_flow_halted"] = True
                meta["desktop_last_failed_tool"] = name
                try:
                    _ed = json.loads(result_text or "{}")
                    meta["desktop_last_error"] = str((_ed or {}).get("error") or "")[:300]
                except Exception:
                    meta["desktop_last_error"] = ""
                meta["failed"] = True
                meta["partial"] = True
                meta["savable"] = False
                meta["halt_reply"] = _desktop_halt_user_facing(name, result_text)
                yield (
                    "thinking",
                    {
                        "round": round_idx,
                        "content": f"步骤失败，已暂停后续动作（{name}）",
                    },
                )
                while idx_tc < len(pending_calls):
                    skip = pending_calls[idx_tc]
                    idx_tc += 1
                    sfn = (skip.get("function") or {})
                    sname = (sfn.get("name") or "").strip() or "tool"
                    sid = skip.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                    blocked = json.dumps(
                        {
                            "success": False,
                            "ok": False,
                            "flow_halt": True,
                            "error": f"已取消：因上一步 `{name}` 失败，不再执行 `{sname}`",
                            "suggestion": "任务已停止，请处理后重发。",
                        },
                        ensure_ascii=False,
                    )
                    yield (
                        "tool_call_start",
                        {"round": round_idx, "tool": sname, "args_summary": "已取消（流程闸）"},
                    )
                    yield (
                        "tool_call_result",
                        {"round": round_idx, "tool": sname, "result_preview": blocked[:500]},
                    )
                    yield (
                        "action_records",
                        [
                            {
                                "action_type": sname.replace("windows_", ""),
                                "target": "已取消",
                                "status": "failed",
                                "result": "flow_halt",
                                "has_vision": False,
                                "env_verify": None,
                            }
                        ],
                    )
                    messages.append({"role": "tool", "tool_call_id": sid, "content": blocked})
                    meta["tools_used"].append(f"{sname}_flow_halted")
                messages.append(
                    {
                        "role": "user",
                        "content": _desktop_fail_stop_message(name, result_text, meta=meta),
                    }
                )
                break

        if _desktop_flow_should_stop(meta):
            reply = meta.get("halt_reply") or _desktop_halt_user_facing(
                str(meta.get("desktop_last_failed_tool") or "windows_*"),
                json.dumps(
                    {"error": meta.get("desktop_last_error") or ""},
                    ensure_ascii=False,
                ),
            )
            meta["final_round"] = round_idx
            meta["savable"] = False
            yield ("reply", {"text": reply})
            yield (
                "done",
                {
                    "total_rounds": round_idx + 1,
                    "plan": {},
                    "meta": meta,
                    "reply": reply,
                    "failed": True,
                    "savable": False,
                    "partial": True,
                },
            )
            return

    yield ("error", f"工具调用轮数超过上限（{_max_tool_rounds()}），请缩短任务")
