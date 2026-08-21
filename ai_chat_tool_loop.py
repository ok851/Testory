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


# ──────── session 级运行时上下文：每个 user_id 独立一份 ────────
_runtime_ctx_lock = threading.Lock()
_runtime_chat_ctx: Dict[str, Dict[str, Any]] = {}


def _runtime_ctx_key(user_id: Any) -> str:
    return f"u_{int(user_id or 0)}"


def clear_runtime_chat_context(*, user_id: Any = 0) -> None:
    """用户清空会话时：丢弃该用户累积的 typed_texts / succeeded_action_fps / obs_count / cross_vars 等 meta。"""
    k = _runtime_ctx_key(user_id)
    with _runtime_ctx_lock:
        _runtime_chat_ctx.pop(k, None)


def get_runtime_chat_context(*, user_id: Any = 0) -> Dict[str, Any]:
    k = _runtime_ctx_key(user_id)
    with _runtime_ctx_lock:
        ctx = _runtime_chat_ctx.get(k)
        if ctx is None:
            ctx = {}
            _runtime_chat_ctx[k] = ctx
        return ctx


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
        "你是 Testory 网页自动化执行器。\n\n"
        "【🚫 禁止操作】\n"
        "- 不要调用 browser_navigate / browser_goto，除非指令明确要求导航\n"
        "- 不要使用 skill_view、terminal、bash、curl、windows_* 等工具\n"
        "- 不要编造未实际执行的操作结果\n\n"
        "【📋 工具使用规则 - 严格遵守】\n"
        "\n"
        "✅ browser_snapshot：获取页面结构快照，用于定位元素\n"
        "   - 这是你定位元素的主要工具\n"
        "   - 返回的 @e1、@e2 等 ref 可直接用于 browser_click/browser_type\n"
        "   - 在执行任何操作前，必须先调用一次 browser_snapshot\n"
        "\n"
        "✅ browser_click(ref)：点击元素\n"
        "   - ref 来自 browser_snapshot 返回的 @e1、@e2 等\n"
        "   - 示例：browser_click(ref='@e5')\n"
        "\n"
        "✅ browser_type(ref, text)：在元素中输入文本\n"
        "   - ref 来自 browser_snapshot 返回的 @e1、@e2 等\n"
        "   - 示例：browser_type(ref='@e3', text='hello')\n"
        "\n"
        "✅ browser_press(key)：模拟按键\n"
        "   - 用于 Enter、Tab、ArrowDown 等\n"
        "\n"
        "❌ browser_console：仅用于读取控制台日志\n"
        "   - 不要用它来定位元素或获取页面结构\n"
        "   - 不要用它来执行 DOM 操作\n"
        "   - 只有当你需要检查控制台错误时才使用\n\n"
        "【🎯 标准执行流程】\n"
        "1. 调用 browser_snapshot 获取页面结构\n"
        "2. 从返回结果中找到目标元素的 ref（如 @e5）\n"
        "3. 使用 browser_click(ref) 或 browser_type(ref, text) 操作元素\n"
        "4. 操作完成后，可以再次调用 browser_snapshot 验证结果\n\n"
        "【📌 重要提示】\n"
        "- 页面已就绪时不要调用 browser_navigate\n"
        "- 遇到验证码/扫码：等待用户在浏览器窗口完成\n"
        "- 操作失败时换另一种方式，不要死循环\n"
    )


def _desktop_hermes_system_prompt() -> str:
    return (
        "【桌面自动化：UIA + OCR + VLM 三模融合定位】\n"
        "1. windows_click_element 支持 UIA 树、OCR 文本、视觉模型三种定位方式自动融合，系统会自动选择最佳策略；\n"
        "2. description 仅写短控件名（如「登录」「确定」「搜索」），禁止把用户整句当目标；\n"
        "3. 点击失败时系统自动：触发屏幕观察 → 从 OCR/VLM 候选取最相似文本 → 重试；仍失败再返回详细错误信息；\n"
        "4. 对 Electron / DirectUI / 微信 / QQ 等应用：系统自动降级为 OCR + VLM 视觉定位，无需特殊配置；\n"
        "5. 连续两次同类工具无进展 → 输出 NEED_USER_ACTION 并停止，禁止死循环；\n"
        "6. 任务涉及手机验证码 → PC 回填：先调 mobile_extract_otp（等待短信/通知），再用 windows_type_text 填 text='{{sms_otp}}'；\n"
        "7. 【浏览器导航流程】启动 → Ctrl+L 聚焦地址栏 → 输入 URL → Enter 确认 → 等待 1-2 秒加载 → 操作页面内元素；\n"
        "8. 输入 URL 后可能提示「未核验内容」，这是正常现象——直接紧跟 windows_press_key('Enter') 即可；\n"
        "9. 表单填写流程：windows_click_element('用户名/账号输入框') → windows_type_text('账号') → windows_click_element('密码输入框') → windows_type_text('密码') → windows_click_element('登录')。\n"
        "10. 元素描述建议：\n"
        "    ✅ 好: '登录' '确定' '搜索' '保存' '删除'\n"
        "    ✅ 好: '用户名输入框' '密码输入框' '搜索框'\n"
        "    ❌ 差: '点击页面上的登录按钮' '那个蓝色的按钮' '页面右上角的东西'\n"
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
                    "【平台=仅桌面本机GUI·非浏览器】将指定桌面应用窗口激活到前台并设为目标。"
                    "【适用】微信、钉钉、记事本、Word、WPS、企业微信、QQ 等非浏览器本机应用。"
                    "【严禁】浏览器/网页任务（打开网站、访问URL、搜索网页等）绝对不要调用此工具！"
                    "浏览器任务必须用 hermes_execute，它会自动启动真正的浏览器并操作DOM。"
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
                    "【平台=仅桌面本机GUI·非浏览器】启动本机应用并绑定目标窗口。"
                    "【适用】记事本/notepad、计算器/calc、画图/mspaint、钉钉、飞书、企业微信、微信、QQ、Word、Excel、WPS 等**非浏览器**应用。"
                    "【严禁 1】浏览器/网页任务（打开网站、访问 URL、搜索、输入网址等）绝对不要调用！"
                    "     👉 浏览器任务必须用 hermes_execute（会自动启动 Edge/Chrome 并通过 CDP 操作页面）。"
                    "【严禁 2】不要用 launch_app('Edge') + press_key('Ctrl+T') + type_text(网址) 这种桌面模拟方式操作浏览器！"
                    "     👉 桌面模拟会把焦点所在窗口（比如 Testory 软件自己）当作目标，导致按键/输入按错地方！"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app_name": {
                            "type": "string",
                            "description": "应用名或别名（仅限非浏览器应用），如「记事本」「notepad」「计算器」「钉钉」",
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
                    "【平台=仅桌面本机GUI·非浏览器】按短控件名点击桌面可见控件（UIA→OCR→VLM 三模定位）。"
                    "【适用】微信/钉钉/Word/记事本/WPS 等本机应用的按钮、菜单、输入框标签。"
                    "【前置条件】调用前必须先 get_screen_text 获取当前屏幕文本候选，确认是目标应用再点击。"
                    "【严禁】浏览器/网页任务不要调用！浏览器页面元素点击请用 hermes_execute。"
                    "【严禁】不要在 Testory 软件自己的窗口上找「登录」「账号」等网页控件，这些在真实浏览器里。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "短控件名，如「确定」「保存」「登录」；不要整句",
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
                    "【平台=仅桌面本机GUI·非浏览器】向当前聚焦的桌面应用窗口输入框键入文本。"
                    "【适用】记事本编辑内容、微信消息框、钉钉输入框、Word文档、Excel单元格等。"
                    "【前置条件】先 windows_focus_app 聚焦目标应用，再 get_screen_text 确认窗口正确，再输入。"
                    "【严禁】浏览器地址栏/网页输入框不要用此工具！必须用 hermes_execute 在浏览器内操作。"
                    "【失败含义】若返回「UIA/OCR 均未确认内容出现」，说明当前聚焦窗口不是预期目标（可能聚焦到了 Testory 软件窗口），应先 focus_app + get_screen_text 确认窗口，再重试。"
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
                    "【平台=仅桌面本机GUI·非浏览器】向当前聚焦的桌面窗口发送按键或组合键。"
                    "【适用】记事本 Ctrl+S 保存、微信 Enter 发送消息、Word Ctrl+N 新建文档等。"
                    "【严禁 1】浏览器任务不要调用！不要用 Ctrl+T / Ctrl+L / Enter 来模拟浏览器操作——"
                    "因为焦点很可能在 Testory 软件窗口或其他非目标窗口，会按错！"
                    "【严禁 2】浏览器任务必须用 hermes_execute，它会在真实浏览器窗口里通过 CDP 协议精准操作 DOM。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "完整按键名，如 Enter / Ctrl+S（不要只写 ctrl）",
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
                    "【平台=仅桌面本机GUI】短暂等待或验证桌面窗口变化。"
                    "如桌面应用启动后等待窗口出现、页面跳转后等待稳定；桌面任务步骤之间可用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "duration_ms": {"type": "integer", "description": "等待毫秒数"},
                        "condition": {
                            "type": "string",
                            "description": "stable | desktop_change | window:标题关键词",
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
                    "【共享屏幕·OCR】获取当前整块屏幕的可见文字与坐标。"
                    "【必须调用的场景】"
                    "  1) 桌面任务第一次 windows_click_element / windows_type_text / windows_press_key 之前，先观察确认是目标应用；"
                    "  2) windows_* 任何工具失败后，必须立即调用此工具看真实画面文字是什么，再决定下一步；"
                    "  3) windows_launch_app / windows_focus_app 调用之后，必须调用一次验证窗口正确，否则可能在 Testory 窗口乱点。"
                    "【返回内容】屏幕上 OCR 到的全部文字和坐标，你根据返回文字判断当前聚焦窗口是不是目标应用。"
                    "【窗口误判提示】如果返回文字包含 'Testory' / '自主测试' / 'AI 测试' 等，说明焦点在 Testory 软件窗口，应先 focus_app。"
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
                    "【共享屏幕·VLM 视觉】用视觉大模型理解当前屏幕画面，返回结构化语义描述。比 OCR 更全面，能识别图标/颜色/布局。"
                    "【必须调用的场景】"
                    "  1) windows_launch_app 或 windows_focus_app 之后：验证真的是目标应用（不是 Testory 软件窗口）；"
                    "  2) 桌面任务连续 2 次失败后：调用此工具获取画面真实状态；"
                    "  3) get_screen_text 返回空 / 没有目标文字但界面应该有（Electron / DirectUI / 微信 / QQ 等自定义渲染应用）；"
                    "  4) 需要判断界面布局、按钮、图标等视觉特征而非仅文字。"
                    "【可提问】prompt 可直接问：'当前屏幕是什么应用？有没有浏览器地址栏？是不是目标登录页？'"
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
    """是否注册外层 windows_*。

    设计理念：**尽量挂载，让 AI 自行选择**。
    除非明确设置 PLATFORM_OUTER_DESKTOP_TOOLS=0 或平台为 android，否则一律挂载。
    AI 会根据任务语义判断何时使用 windows_*、hermes_execute 或 mobile_*。
    """
    import os

    raw = (os.environ.get("PLATFORM_OUTER_DESKTOP_TOOLS") or "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    plat = (platform_type or "").strip().lower()
    if plat == "android":
        return False
    # 其他情况一律挂载，让 AI 自行选择
    return True


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


def _prepare_element_context(
    params: "ChatToolLoopParams",
    name: str,
    args: Dict[str, Any],
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """为元素操作准备多模态上下文（智能路由）。

    检查屏幕观察者缓存的 OCR 结果，注入 _ocr_hints / _ocr_blocks。
    这是连接屏幕观察者与元素定位器的关键桥梁。
    """
    call_args = dict(args or {})
    if name not in ("windows_click_element", "windows_type_text"):
        return call_args
    observer = getattr(params, "screen_observer", None)
    if observer is not None and observer.is_running:
        try:
            obs = observer.get_latest_analysis(force_refresh=False)
            if obs and isinstance(obs, dict):
                texts = obs.get("texts", [])
                blocks = obs.get("blocks", [])
                if texts:
                    call_args["_ocr_hints"] = texts
                if blocks:
                    call_args["_ocr_blocks"] = blocks
                call_args["_screen_frame_hash"] = obs.get("frame_hash", "")
        except Exception:
            pass
    pending_hints = (meta or {}).get("pending_ocr_hints") or []
    pending_blocks = (meta or {}).get("pending_ocr_blocks") or []
    if pending_hints and not call_args.get("_ocr_hints"):
        call_args["_ocr_hints"] = pending_hints
    if pending_blocks and not call_args.get("_ocr_blocks"):
        call_args["_ocr_blocks"] = pending_blocks
    return call_args


def _retry_failed_element_operation(
    params: "ChatToolLoopParams",
    name: str,
    args: Dict[str, Any],
    meta: Dict[str, Any],
    original_result: str,
) -> str:
    """元素操作失败后的自恢复：触发屏幕观察 → 生成替代描述 → 重试。

    Returns:
        重试后的结果 JSON 字符串（如果重试成功），或原始失败结果
    """
    original_ok = _desktop_tool_succeeded(original_result)
    if original_ok:
        return original_result
    observer = getattr(params, "screen_observer", None)
    if observer is None:
        return original_result
    try:
        failure_ctx = observer.on_tool_failure(name, original_result[:300])
        obs_texts = failure_ctx.get("texts", [])
        if not obs_texts:
            return original_result
        original_desc = str(args.get("description") or args.get("target") or "")
        if not original_desc:
            return original_result
        new_desc = _generate_retry_description(original_desc, obs_texts)
        if new_desc == original_desc:
            return original_result
        uat_logger.info("element_retry: desc %r -> %r based on OCR candidates", original_desc, new_desc)
        retry_args = dict(args)
        retry_args["description"] = new_desc
        retry_args["_ocr_hints"] = obs_texts
        retry_args["_ocr_blocks"] = failure_ctx.get("blocks", [])
        retry_result = _dispatch_desktop_or_screen_tool(name, retry_args)
        if _desktop_tool_succeeded(retry_result):
            observer.on_tool_success()
            return retry_result
        combined = {
            "success": False,
            "error": f"重试失败: 原始描述='{original_desc}', 重试描述='{new_desc}'",
            "original_result": json.loads(original_result) if original_result.startswith("{") else original_result,
            "retry_result": json.loads(retry_result) if retry_result.startswith("{") else retry_result,
            "retry_description": new_desc,
        }
        observer._consecutive_failures = max(observer._consecutive_failures, 0)
        return json.dumps(combined, ensure_ascii=False)
    except Exception as e:
        uat_logger.warning("element_retry error: %s", e)
        return original_result


def _generate_retry_description(original: str, ocr_texts: List[Dict[str, Any]]) -> str:
    """从 OCR 候选中生成替代描述。"""
    if not ocr_texts:
        return original
    from element_confidence import ElementConfidence
    best_score = 0.0
    best_text = ""
    for item in ocr_texts:
        text = str(item.get("text", "")).strip()
        if not text or len(text) < 1:
            continue
        score = ElementConfidence.score_candidate_match(original, text, partial=True)
        if score > best_score:
            best_score = score
            best_text = text
    if best_text and best_score >= 0.4:
        return best_text
    expanded = ElementConfidence.semantic_expand(original)
    if len(expanded) > 1:
        for alias in expanded:
            for item in ocr_texts:
                text = str(item.get("text", "")).strip()
                if alias.lower() in text.lower():
                    return text
    return original


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
    """桌面/手机步骤失败后整任务停：禁止进入下一轮 LLM。"""
    m = meta or {}
    return bool(m.get("desktop_flow_halted") or m.get("mobile_flow_halted"))


def _mobile_halt_user_facing(tool_name: str, result_text: str) -> str:
    err = ""
    try:
        parsed = json.loads(result_text or "")
        if isinstance(parsed, dict):
            err = str(parsed.get("error") or "")[:240]
    except Exception:
        err = (result_text or "")[:240]
    tip = err or "手机本机执行失败"
    return (
        f"手机双手工具 {tool_name} 连续失败，已停止自动重试。"
        f"原因：{tip}"
        " 请确认步骤 IR（推荐 action=open_app + package_name）后重试。"
    )


def _record_mobile_tool_outcome(meta: Dict[str, Any], name: str, result_text: str) -> None:
    """mobile_* 失败计入次数；满 2 次则停（允许一轮修正）。"""
    if not (name or "").startswith("mobile_"):
        return
    ok = True
    try:
        parsed = json.loads(result_text or "")
        if isinstance(parsed, dict):
            if parsed.get("success") is False or parsed.get("ok") is False:
                ok = False
    except Exception:
        low = (result_text or "").lower()
        ok = '"success": false' not in low and '"ok": false' not in low
    if ok:
        meta["mobile_fail_streak"] = 0
        return
    streak = int(meta.get("mobile_fail_streak") or 0) + 1
    meta["mobile_fail_streak"] = streak
    meta["mobile_last_failed_tool"] = name
    meta["mobile_last_error"] = (result_text or "")[:500]
    if streak >= 2:
        meta["mobile_flow_halted"] = True
        meta["halt_reply"] = _mobile_halt_user_facing(name, result_text)


def _record_cross_end_or_api_to_recorder(
    params: Any,
    tool_name: str,
    args: Optional[Dict[str, Any]],
    result_text: str,
) -> None:
    """把跨端工具（mobile_extract_otp/desktop_type_text）和 api_call 记录到 ActionRecorder。
    这些工具不经 Hermes SSE，需在此单独记录，否则生成用例时步骤缺失。"""
    rec = getattr(params, "recorder", None)
    if rec is None:
        return
    try:
        ok = True
        verified = None
        try:
            parsed = json.loads(result_text or "")
            if isinstance(parsed, dict):
                if parsed.get("success") is False or parsed.get("ok") is False:
                    ok = False
                if parsed.get("sms_otp"):
                    verified = True
        except Exception:
            ok = True
        # 构造 target：api_call 用 url，mobile_extract_otp 用 sender_hint，desktop_type_text 用 text 前 40 字
        a = args or {}
        if tool_name == "api_call":
            target = str(a.get("url") or a.get("method") or "api_call")[:80]
        elif tool_name == "mobile_extract_otp":
            target = str(a.get("sender_hint") or "短信验证码")[:80]
        elif tool_name == "desktop_type_text":
            target = str(a.get("text") or "")[:80] or "desktop_input"
        else:
            target = str(tool_name)[:80]
        rec.capture_from_tool_event(
            name=tool_name,
            args=a,
            result=result_text,
            status="ok" if ok else "error",
        )
    except Exception:
        pass


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

def _emit_tool_registry_audit_event() -> None:
    try:
        from execution_events import ExecutionEventCollector, TOOL_REGISTERED
        from agent_tool_registry import describe_registry
        collector = ExecutionEventCollector()
        collector.emit(TOOL_REGISTERED, registry=describe_registry())
    except Exception:
        pass


def _api_execution_tool_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "api_call",
            "description": "以 API 通道执行请求或验证（与 UI 执行并列）。适用于可靠接口调用、前置校验、结果核对或 UI 失败后的降级执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {"type": "string", "description": "API 请求说明或 spec 引用"},
                    "method": {"type": "string", "description": "GET/POST/PUT/DELETE 等"},
                    "url": {"type": "string", "description": "请求地址"},
                    "expected_status": {"type": "integer", "description": "期望状态码"},
                    "expected_body": {"type": "string", "description": "期望响应片段"},
                },
                "required": ["spec"],
            },
        },
    }

def chat_tool_schemas(
    *,
    allow_openclaw: bool = True,
    allow_hermes: Optional[bool] = None,
    platform_type: str = "web",
    allow_screen_tools: bool = False,
    allow_desktop_windows_tools: Optional[bool] = None,
    message: str = "",
    allow_refine_test_plan: bool = True,
    connected_hands: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """构建工具列表。

    设计理念：**把工具选择权交还给 AI，而非硬编码过滤**。
    根据任务意图和已连接设备挂载全部可用工具，通过 System Prompt 引导 AI 正确选择。
    AI 具备强大的语义理解能力，能自行判断：
    - 有 URL/网页关键词 → 使用 hermes_execute（浏览器自动化）
    - 有手机短信/验证码 → 使用 mobile_* 工具
    - 有桌面应用操作 → 使用 windows_* 工具
    - 多端联动 → 组合使用多个工具
    """
    allow = allow_hermes if allow_hermes is not None else allow_openclaw
    schemas: List[Dict[str, Any]] = []
    hands = connected_hands if isinstance(connected_hands, dict) else None
    plat = (platform_type or "web").strip().lower()

    # ===== 1. 判断哪些端的工具可用 =====
    # hermes_execute：只要允许就挂载，不管任务类型
    # windows_*：只要有桌面连接或用户允许就挂载
    # mobile_*：只要有手机连接就挂载

    # Windows 桌面工具可用性
    if hands is not None and hands.get("desktop") is True:
        enable_win = True if allow_desktop_windows_tools is not False else False
    elif allow_desktop_windows_tools is not None:
        enable_win = allow_desktop_windows_tools
    else:
        enable_win = _should_enable_desktop_windows_tools(platform_type, message)

    # cross_end desktop_* / mobile_* 工具
    include_desk = True if (hands is None or hands.get("desktop")) else False
    include_phone = True if (hands is None or hands.get("phone")) else False

    # ===== 2. 挂载可用工具 =====
    if allow:
        schemas.append(_agent_execute_tool_schema())

    if enable_win:
        schemas.extend(_desktop_windows_tool_schemas())
        if allow_screen_tools:
            schemas.extend(_screen_observation_tool_schemas())
    elif allow_screen_tools:
        schemas.extend(_screen_observation_tool_schemas())

    try:
        from mobile_cross_end_tools import cross_end_tool_schemas
        schemas.extend(
            cross_end_tool_schemas(
                include_desktop=include_desk,
                include_mobile=include_phone,
            )
        )
    except Exception:
        pass

    if allow_refine_test_plan:
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
            },
        )

    import os as _os
    if (_os.getenv("AGENT_API_EXECUTION_ENABLE") or "").strip().lower() in ("1", "true", "yes", "on"):
        schemas.append(_api_execution_tool_schema())

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


def _is_cross_end_agent_tool(name: str) -> bool:
    try:
        from mobile_cross_end_tools import DESKTOP_ALIAS_TOOL_NAMES, MOBILE_TOOL_NAMES

        n = (name or "").strip()
        return n in MOBILE_TOOL_NAMES or n in DESKTOP_ALIAS_TOOL_NAMES
    except Exception:
        return False


def _dispatch_cross_end_agent_tool(
    name: str,
    args: Dict[str, Any],
    *,
    abort_event: Any = None,
    on_tick: Any = None,
) -> str:
    from mobile_cross_end_tools import dispatch_cross_end_tool

    result = dispatch_cross_end_tool(
        name,
        args or {},
        abort_event=abort_event,
        on_tick=on_tick,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def _run_mobile_tool_with_progress(
    name: str,
    call_args: Dict[str, Any],
    *,
    abort_event: Any = None,
    timeout_cap: Optional[float] = None,
):
    """在独立线程执行 mobile_* await，主线程可 yield 进度。

    yield ("progress", text) … 最后 yield ("result", result_json_str)
    """
    import queue
    import threading

    args = dict(call_args or {})
    if timeout_cap is not None:
        try:
            req = float(args.get("timeout_sec") or timeout_cap)
        except Exception:
            req = float(timeout_cap)
        # 给外层收尾留一点余量；真正卡住点是手机不领任务，不是数字本身
        args["timeout_sec"] = max(8.0, min(req, float(timeout_cap)))

    progress_q: "queue.Queue[str]" = queue.Queue()
    result_box: List[str] = []
    error_box: List[BaseException] = []

    def _on_tick(job: Dict[str, Any]) -> None:
        st = str((job or {}).get("status") or "pending")
        jid = str((job or {}).get("job_id") or "")
        step_count = len((job or {}).get("steps") or [])
        claimed_at = float((job or {}).get("claimed_at") or 0)
        elapsed = int(time.time() - claimed_at) if claimed_at > 0 else 0
        if st == "pending":
            msg = (
                f"仍在等待手机领取任务（job={jid or '?'}，status=pending）。"
                "若长时间不动：请确认无障碍已开启且 APK 显示已连接。"
            )
        elif st == "running":
            elapsed_hint = f"已执行 {elapsed}s" if elapsed > 0 else ""
            step_hint = f"，共 {step_count} 步" if step_count > 0 else ""
            msg = f"手机已领取任务，本机回放中（{elapsed_hint}{step_hint}）…"
        else:
            msg = f"手机任务状态：{st}（job={jid or '?'}）"
        try:
            progress_q.put_nowait(msg)
        except Exception:
            pass
    def _worker() -> None:
        try:
            result_box.append(
                _dispatch_cross_end_agent_tool(
                    name,
                    args,
                    abort_event=abort_event,
                    on_tick=_on_tick,
                )
            )
        except BaseException as e:
            error_box.append(e)

    th = threading.Thread(target=_worker, name=f"mobile-tool-{name}", daemon=True)
    th.start()
    yield (
        "progress",
        f"已开始调用 {name}：下发任务并等待手机本机执行（非桌面瞬时工具）…",
    )
    while th.is_alive():
        try:
            msg = progress_q.get(timeout=0.45)
            yield ("progress", msg)
        except queue.Empty:
            pass
        if abort_event is not None and abort_event.is_set():
            # worker 内 wait 会感知 abort；这里继续等到线程退出
            yield ("progress", f"{name}：收到中止信号，正在结束等待…")
    th.join(timeout=2.0)
    while not progress_q.empty():
        try:
            yield ("progress", progress_q.get_nowait())
        except Exception:
            break
    if error_box:
        yield (
            "result",
            json.dumps(
                {"ok": False, "success": False, "error": str(error_box[0])},
                ensure_ascii=False,
            ),
        )
        return
    yield ("result", result_box[0] if result_box else json.dumps(
        {"ok": False, "error": f"{name} 无返回"}, ensure_ascii=False
    ))


_CROSS_END_VAR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _resolve_cross_end_vars(obj: Any, vars_map: Optional[Dict[str, Any]]) -> Any:
    """把工具参数里的 {{sms_otp}} 等替换为 meta.cross_end_vars。"""
    if not isinstance(vars_map, dict) or not vars_map:
        return obj
    if isinstance(obj, str):
        def _repl(m: re.Match) -> str:
            key = (m.group(1) or "").strip()
            if key in vars_map and vars_map[key] is not None:
                return str(vars_map[key])
            return m.group(0)
        return _CROSS_END_VAR_RE.sub(_repl, obj)
    if isinstance(obj, dict):
        return {k: _resolve_cross_end_vars(v, vars_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_cross_end_vars(v, vars_map) for v in obj]
    return obj


def _seed_cross_end_vars_from_message(message: str) -> Dict[str, str]:
    try:
        from agent_intent import extract_cross_end_seed_vars

        return dict(extract_cross_end_seed_vars(message or "") or {})
    except Exception:
        return {}


def _cross_end_strategy_lines() -> List[str]:
    """通用跨端原则（非固定业务步骤模板）。"""
    return [
        "",
        "## 跨端工具原则（能力面，非固定剧本）",
        "- 【平台选择优先】浏览器/网页任务（打开网站、访问 URL、操作浏览器页面）→ 一律 hermes_execute 走 CDP，"
        "**绝对不要**用 desktop_* / windows_* 启动应用或按键盘，禁止在非浏览器窗口乱点！",
        "- 桌面 GUI（非浏览器本机应用：微信、钉钉、Word、记事本等）：用 desktop_* / windows_*；"
        "每步根据工具返回再决定下一步，禁止臆造成功。",
        "- 需要短信/通知验证码：调用 mobile_extract_otp，只用工具返回值；禁止编造验证码。",
        "- 需要手机本机跑步骤或用例：mobile_run_steps / mobile_run_case。",
        "- mobile_run_steps 的 steps 须用手机 IR action："
        "open_app（推荐，带 package_name 如 com.tencent.mobileqq）、"
        "tap/input/wait/home/back；禁止 invent launch_app/start_app/shell/find_and_tap。",
        "- 打开应用示例："
        '{"action":"open_app","description":"打开目标App","package_name":"com.example.app"}',
        "- 应用内点击必须带可见文案定位（勿只写 description）："
        '{"action":"tap","description":"点击登录","selector_type":"text","selector_value":"登录"}',
        "- 勾选协议/复选框：description 含「勾选」且 prefer_checkable（平台会自动补）；"
        "禁止用协议链接文案当唯一目标，应定位勾选框旁短文案。",
        "- 应用内输入：input_value=内容，selector_value=输入框提示文案："
        '{"action":"input","description":"输入手机号","selector_type":"text",'
        '"selector_value":"手机号","input_value":"13800000000"}',
        "- 【禁止】用 swipe/scroll 来「寻找」登录元素或「探索页面」。"
        "如果找不到目标元素，应直接向用户报告当前页面状态，而非用滑动猜测。"
        "swipe/scroll 仅用于明确的翻页需求（如列表滚动到指定项）。",
        "- 【广告/弹窗处理】很多 App 启动后会显示广告、开屏页、弹窗公告等。"
        "mobile_run_steps 中 open_app 之后建议加 wait 步骤（如 3000ms）等待广告展示完毕；"
        "如果广告有关闭按钮（如「跳过」「关闭」「X」），可在 steps 中加入 tap 关闭。"
        "手机端会在元素未找到时自动重试等待（最多约 7 秒），但主动加入 wait 更可靠。",
        "- mobile_* 工具返回的 success 只表示手势层结果；必须阅读 steps_digest / error。"
        "禁止在工具未全部 OK、或存在未勾选/未推进错误时向用户宣称「已完成」。",
        "- 跨工具共享变量在平台侧累积；参数中可写 {{var}}（如 {{sms_otp}} / {{phone_number}}），平台会替换。",
        "- 用户目标可能是登录、注册、换绑或其他：按当前界面与目标自行选择控件描述与顺序，勿套死模板。",
        "- 任一步失败：最多再调整尝试 1 次（共 2 轮）；仍失败则停止并向用户如实说明，禁止长时间循环猜测。",
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
        "## 意图判断（最重要，先读再动）",
        "请先判断用户输入的意图，并**严格按平台边界选工具**：",
        "- 如果用户在闲聊、询问你的身份/能力、表达感谢或抱怨 → 直接自然语言回答，不要调用任何工具。",
        "- 【浏览器/Web 任务】打开网站、访问 URL、搜索网页、浏览器页面操作、输入网址 → **只调用 hermes_execute**（同一任务只调一次）。"
            "**严禁**用 windows_* / desktop_* 启动应用或按键！**严禁**在非浏览器窗口（如 Testory 软件本身、其他应用窗口）上点击！"
            "hermes_execute 会启动真实 Edge/Chrome 浏览器并通过 CDP 操作，用户可见整个过程。",
        "- 【Windows 桌面任务（非浏览器）】操作本机已安装应用（微信、钉钉、记事本、Word、WPS、企业微信等）→ **直接调用 windows_* 或 desktop_* 逐步执行**。"
            "每步根据工具返回再决定下一步；禁止只调 hermes_execute 后空等；禁止臆造「已输入/已发送」。",
        "- 涉及手机取码、本机执行或桌面+手机联动：按「跨端工具原则」选用 desktop_* / mobile_*；禁止臆造 sms_otp。",
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
    if plat == "web":
        parts_agent = [
            "【浏览器/Web 场景】当用户任务涉及网页、URL、浏览器操作时：",
            "  ✓ 首选工具：hermes_execute（启动真实 Edge/Chrome，通过 CDP 协议操作）",
            "  ✓ 备选工具：mobile_*（如需手机获取验证码）、windows_*（如需桌面配合）",
            "  ✗ 不推荐：windows_launch_app + press_key 模拟浏览器操作（容易发到错误窗口）",
            "",
            "正确流程：调用 hermes_execute，把完整任务写在 instruction 里（含 URL、要走的流程、验收点）。",
            "平台会确保浏览器窗口启动并最大化；Hermes 将在真实浏览器中通过 DOM/CDP 自主 navigate/click/input，用户可见整个过程。",
            "起始 URL 优先从用户消息解析（平台无独立 URL 输入框）；若消息中有网址，instruction 须明确写出。",
            "若消息是短指令（如「打开百度搜索 AI」），instruction 中先写清楚目标：访问哪个网址、做什么操作、期望什么结果。",
            "",
            "多端联动：如果任务需要手机验证码，在 hermes_execute 的 instruction 中说明，",
            "平台会自动调用 mobile_extract_otp 获取验证码并回填。",
            (
                "【收尾】开启生成用例：hermes_execute 完成后一两句中文汇报即可；"
                "平台从轨迹自动生成用例，禁止 refine_test_plan / 手写 JSON。"
                if generate_case_after_run
                else "【收尾】未开启生成用例：hermes_execute 完成后一两句中文汇报，禁止 refine_test_plan / 用例 JSON。"
            ),
        ]
    elif plat == "auto":
        parts_agent = [
            "【智能 Agent 模式】根据用户任务语义自行选择最合适的工具：",
            "- 闲聊、问身份/能力、要建议 → 直接自然语言回答，不要调用工具。",
            "- 网页/浏览器任务（含 URL、打开网站、搜索网页）→ 首选 hermes_execute（自动启动真实 Edge/Chrome），而非 windows_launch_app + press_key 模拟。",
            "- Windows 桌面 GUI（本机应用如微信/钉钉/记事本）→ 使用 windows_* 工具。",
            "- 手机操作（取验证码、查消息）→ 使用 mobile_* 工具。",
            "- 多端联动任务 → 组合使用多个工具族。",
            "",
            "工具选择决策参考：",
            "  ✓ hermes_execute：涉及 URL/网页时首选，自动启动浏览器并通过 CDP 操作",
            "  ✓ windows_*：涉及本机桌面应用时使用",
            "  ✓ mobile_*：涉及手机操作时使用",
            "  ✗ 避免：用 windows_launch_app + press_key 模拟浏览器操作（不可靠，容易发到错误窗口）",
            "",
            "每步看工具返回再继续；同轮不要一次提交多个互依赖动作；若上一步失败/flow_halt，停止并说明原因。",
            (
                "- 开启生成用例：成功后简短汇报；用例由平台从动作轨迹自动生成，禁止 refine_test_plan / 手写大段 JSON。"
                if generate_case_after_run
                else "- 未开启生成用例：完成后简短汇报，禁止 refine_test_plan / 用例 JSON。"
            ),
            "- 收到 NEED_USER_ACTION / stream_empty / auth_fatal 时向用户说明；",
            "禁止编造未实际执行的 steps JSON。",
            "",
            "禁止在未确认用户要操作真实环境时调用自动化工具。",
        ]
    elif plat == "desktop":
        parts_agent = [
            "【重要】当前为 **Windows 桌面** 场景（可同时使用手机 await 工具）。用 windows_* / desktop_* 逐步操控本机 GUI。",
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
            "【严禁编造能力限制】你具备 windows_* 与跨端 mobile_*，可操作本机已安装/已打开的桌面应用，并可 await 已配对手机。",
            "禁止回答「只能测网页」「某某应用无法自动化所以不做」等推脱。",
            (
                "【收尾】开启生成用例：windows_* 成功后一两句中文汇报即可；"
                "平台从动作轨迹自动生成用例，禁止 refine_test_plan / 手写大段 JSON。"
                if generate_case_after_run
                else "【收尾】未开启生成用例：目标完成后立刻用一两句中文汇报结果并结束，禁止再调工具、禁止输出用例 JSON。"
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
    parts.extend(_cross_end_strategy_lines())
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
    # 当前登录用户（mobile_* enqueue / 权限）
    user_id: int = 0
    # 统一 Agent 会话（PC / 手机共用）
    agent_session_id: Optional[str] = None
    # 已连接双手快照；None=不按连接裁剪（兼容旧调用）
    connected_hands: Optional[Dict[str, Any]] = None
    # 屏幕观察者 V2（用于失败时自动分析屏幕）
    screen_observer: Any = None


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
    """任务起始 URL：优先用户消息原文（前端无独立 URL 框），再 plan / probe / 工具参数。

    修复：不再从 instruction 中提取 URL（AI 生成的 instruction 可能包含操作指令）。
    """
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
    # 【修复】不再从 instruction 中提取 URL - 它是 AI 生成的指令文本，不是 URL 来源
    # candidates.append(str((args or {}).get("instruction") or "").strip())
    
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
    # 优先匹配 start_url 所在 host 页面，其次取第一个非空白页
    if not cur_url or cur_url.lower() in ("about:blank", "chrome://newtab/", "edge://newtab/"):
        try:
            from web_capture.cdp_browser import fetch_cdp_pages, _snap as _cdp_snap, _is_blank_page_url
            from urllib.parse import urlparse as _urlparse

            port = int((_cdp_snap() or {}).get("debug_port") or 0)
            _pages = fetch_cdp_pages(port)
            _best_any = None
            _best_match = None
            _target_netloc = ""
            if start_url:
                try:
                    _target_netloc = (_urlparse(start_url).netloc or "").lower()
                except Exception:
                    pass
            for item in _pages:
                u = str(item.get("url") or "").strip()
                if not u or _is_blank_page_url(u) or not u.lower().startswith(("http://", "https://")):
                    continue
                if _best_any is None:
                    _best_any = item
                if _target_netloc:
                    try:
                        if (_urlparse(u).netloc or "").lower() == _target_netloc:
                            _best_match = item
                            break
                    except Exception:
                        pass
            _chosen = _best_match or _best_any
            if _chosen:
                cur_url = str(_chosen.get("url") or "").strip()
                cur_title = str(_chosen.get("title") or cur_title or "").strip()
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

    # 注入平台已采集的丰富页面上下文（DOM + 视觉 + JavaScript 建议）
    rich_context = ""
    try:
        from ai_external_browser_bridge import get_rich_page_context

        rich_context = get_rich_page_context(instr)
    except Exception:
        rich_context = ""

    # 备用：如果丰富上下文获取失败，尝试获取基本 DOM 信息
    if not rich_context:
        dom_pack = ""
        try:
            from ai_external_browser_bridge import get_dom_context_pack, get_page_snapshot

            dom_pack = (get_dom_context_pack() or "").strip()
            if not dom_pack:
                dom_pack = (get_page_snapshot() or "").strip()
        except Exception:
            dom_pack = ""
        rich_context = dom_pack

    if already_on and cur_url:
        instr = (
            f"【当前浏览器状态】URL={cur_url}，标题={cur_title}。\n"
            f"⚠️ **重要警告**：浏览器已在目标页面，**绝对禁止**调用 browser_navigate！\n"
            f"   - browser_navigate 会导致页面重新加载，之前的操作都会丢失\n"
            f"   - 请直接使用下方的 DOM 信息和 JavaScript 操作页面元素\n"
            f"   - 如需刷新页面，使用 browser_evaluate 执行 location.reload() 即可\n\n"
            f"**禁止** skill_view / terminal / 新开标签 / 反复 browser_snapshot。\n\n"
            + (f"{rich_context[:8000]}\n\n" if rich_context else "")
            + instr
        )
    elif start_url:
        instr = (
            f"【起始 URL】{start_url}\n"
            f"平台通常已预导航到此 URL；若下方有 DOM 清单或页面状态信息，**绝对禁止** browser_navigate。\n"
            f"⚠️ **警告**：只有在确认浏览器仍在 about:blank 空白页时，才允许 **仅一次** browser_navigate。\n"
            f"   - 调用前请先检查下方的【页面状态】信息\n"
            f"   - 如果 URL 已经是目标地址，直接使用 DOM/JavaScript 操作\n"
            f"   - 到达目标页后 **立即禁止** 再次 navigate\n"
            f"   - 禁止新开空白标签\n\n"
            + (f"{rich_context[:8000]}\n\n" if rich_context else "")
            + instr
        )
    elif rich_context:
        instr = (
            f"【浏览器状态】页面已就绪，请直接使用下方的 DOM/视觉/JavaScript 信息操作。\n"
            f"⚠️ **禁止** browser_navigate（除非确认在 about:blank）、skill_view、terminal。\n\n"
            f"{rich_context[:8000]}\n\n"
            + instr
        )

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
    elif explore_plat in ("desktop", "windows", "pc") or bool(meta.get("desktop_mode")):
        hermes_system = _desktop_hermes_system_prompt()
        # 桌面模式：默认打开 hermes_cdp_attached 判断，这里仅兜底
        try:
            if meta:
                meta.setdefault("obs_count", int((meta or {}).get("obs_count") or 0))
        except Exception:
            pass

    try:
        result_text = None
        traces: List[str] = []
        tool_events: List[Dict[str, Any]] = []
        # Hermes 同名工具死循环熔断（skill_view / terminal / browser_navigate / 连续 snapshot / 连续 console）
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
                "browser_console",
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
            is_snapshot_loop = looped == "browser_snapshot"
            is_console_loop = looped == "browser_console"
            if is_snapshot_loop:
                err = (
                    "页面快照获取失败，已自动停止该操作。\n"
                    "建议刷新页面后重试，或改用其他方式操作。"
                )
            elif is_console_loop:
                err = (
                    "控制台日志读取失败，已自动停止该操作。\n"
                    "建议改用 browser_snapshot 获取页面结构，再用 browser_click/browser_type 操作元素。"
                )
            else:
                err = (
                    f"工具「{looped}」连续调用失败，已自动停止。\n"
                    "建议换一种方式操作。"
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
                    "hint": "请用中文简要说明情况，不要展示代码或技术细节。",
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
            # 【关键保护】浏览器任务：即使 Hermes 鉴权失败也不回退到桌面工具
            try:
                from agent_intent import message_needs_browser
                _plat = (getattr(params, "platform_type", "") or "auto").strip().lower()
                if _plat == "web" or (user_msg and message_needs_browser(user_msg)):
                    windows_enabled = False
                elif getattr(params, "allow_desktop_windows_tools", None) is True:
                    windows_enabled = True
                elif getattr(params, "allow_desktop_windows_tools", None) is None:
                    windows_enabled = _should_enable_desktop_windows_tools(
                        getattr(params, "platform_type", "") or "auto",
                        user_msg,
                    )
            except Exception:
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


def _prepare_unified_agent_meta(params: "ChatToolLoopParams", plat: str) -> Dict[str, Any]:
    """初始化 meta：合并统一会话 vars + 本轮种子。"""
    seeded = _seed_cross_end_vars_from_message(getattr(params, "message", "") or "")
    uid = int(getattr(params, "user_id", 0) or 0)
    sid = getattr(params, "agent_session_id", None)
    if uid > 0:
        try:
            from agent_unified_session import get_or_create_session, merge_cross_end_vars

            sess = get_or_create_session(uid, sid)
            merged = dict(sess.get("cross_end_vars") or {})
            merged.update(seeded)
            merge_cross_end_vars(uid, seeded, session_id=sid)
            cross_vars = merged
        except Exception:
            cross_vars = seeded
    else:
        cross_vars = seeded
    return {
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
        "cross_end_vars": cross_vars,
        "agent_session_id": sid or "default",
    }


def _persist_unified_agent_session(params: "ChatToolLoopParams", meta: Dict[str, Any], reply: str = "") -> None:
    uid = int(getattr(params, "user_id", 0) or 0)
    if uid <= 0:
        return
    try:
        from agent_unified_session import merge_cross_end_vars, set_session_meta

        vars_map = meta.get("cross_end_vars") if isinstance(meta.get("cross_end_vars"), dict) else {}
        merge_cross_end_vars(uid, vars_map, session_id=getattr(params, "agent_session_id", None))
        set_session_meta(
            uid,
            session_id=getattr(params, "agent_session_id", None),
            tools_used=list(meta.get("tools_used") or []),
            last_reply=reply or "",
            connected_hands=getattr(params, "connected_hands", None),
        )
    except Exception:
        pass


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
        connected_hands=getattr(params, "connected_hands", None),
    )
    agent_client = get_agent_gateway_client()

    ic_note = ""
    if params.interaction_context:
        try:
            ic_note = json.dumps(params.interaction_context, ensure_ascii=False)[:2000]
        except Exception:
            ic_note = str(params.interaction_context)[:2000]

    # ===== 智能任务分析：为 AI 提供语义引导，而非强制路由 =====
    # AI 具备强大的语义理解能力，只需给予正确的上下文和示例
    _msg = (params.message or "").strip()
    _task_decision_prefix = ""
    try:
        from agent_intent import _score_surfaces, _first_url
        _scores = _score_surfaces(_msg, ui_platform=plat)
        
        # 检测关键信号
        _has_url = bool(_first_url(_msg))
        _has_web_keywords = any(kw in _msg.lower() for kw in ("浏览器", "网页", "网站", "url", "http", ".com", ".cn", "访问", "打开"))
        _has_mobile_otp = any(kw in _msg for kw in ("验证码", "短信", "短信验证码", "sms", "otp", "手机号"))
        _is_multi_device = _has_url and _has_mobile_otp
        
        _lines: List[str] = []
        _lines.append("\n\n## === 任务语义分析与工具选择指南 ===")
        
        # 平台状态
        _connected = connected_hands or {}
        _parts = []
        if allow_agent:
            _parts.append("hermes_execute(浏览器)")
        if _connected.get("desktop"):
            _parts.append("windows_*(桌面)")
        if _connected.get("phone"):
            _parts.append("mobile_*(手机)")
        _lines.append(f"当前可用工具族：{'、'.join(_parts) if _parts else '无'}")
        
        # 核心引导
        if _has_url or _has_web_keywords:
            _lines.append("")
            _lines.append("📌 **检测到 URL 或网页关键词 → 这是一个浏览器/Web 任务**")
            _lines.append("   你应该使用 hermes_execute 工具，它会：")
            _lines.append("   1. 自动启动用户本地的 Edge/Chrome 浏览器")
            _lines.append("   2. 通过 CDP 协议在真实浏览器中操作")
            _lines.append("   3. 用户可以看到整个浏览器操作过程")
            _lines.append("")
            _lines.append("   ⚠️ 重要提示：")
            _lines.append("   - 不要使用 windows_launch_app + press_key 来模拟浏览器操作")
            _lines.append("   - 这种方式容易把按键发到 Testory 软件或其他错误窗口")
            _lines.append("   - 直接调用 hermes_execute，把完整流程写在 instruction 参数中")
            
            if _is_multi_device:
                _lines.append("")
                _lines.append("📱 **多端联动场景检测：需要手机验证码**")
                _lines.append("   对于「网页 + 手机验证码」的组合任务：")
                _lines.append("   1. 先调用 hermes_execute 打开网页、填写表单")
                _lines.append("   2. 在 instruction 中注明「需要获取手机短信验证码」")
                _lines.append("   3. 当网页需要验证码时，平台会自动调用 mobile_extract_otp")
                _lines.append("   4. 获取验证码后自动回填到网页表单")
                _lines.append("   5. 完成登录流程")
        elif _has_mobile_otp and not _has_url:
            _lines.append("")
            _lines.append("📱 **检测到手机验证码需求**")
            _lines.append("   如果需要从手机获取验证码：")
            _lines.append("   - 使用 mobile_extract_otp 工具")
            _lines.append("   - 获取的验证码会存入 {{sms_otp}} 变量")
            _lines.append("   - 如果后续需要在桌面应用中使用，用 windows_type_text 输入")
        else:
            _lines.append("")
            _lines.append("💻 **桌面 GUI 任务（无 URL 检测）**")
            _lines.append("   如果是操作本地应用（如微信、钉钉、记事本等）：")
            _lines.append("   1. 使用 windows_focus_app 或 windows_launch_app 启动/聚焦目标应用")
            _lines.append("   2. 使用 windows_click_element / windows_type_text / windows_press_key 进行操作")
            _lines.append("   3. 如果涉及手机配合，同时使用 mobile_* 工具")
        
        _lines.append("")
        _lines.append("🔧 **工具选择原则：**")
        _lines.append("   - 有 URL/网页 → hermes_execute（首选）")
        _lines.append("   - 有手机操作 → mobile_*")
        _lines.append("   - 有桌面应用操作 → windows_*")
        _lines.append("   - 多端联动 → 组合使用多个工具族")
        _lines.append("   - 不确定时 → 先用 hermes_execute 尝试（如果有 URL）")
        _lines.append("")
        _lines.append("## ============================================\n")
        
        _task_decision_prefix = "\n".join(_lines)
        uat_logger.info(
            "task_semantic_analysis: plat=%s has_url=%s has_mobile_otp=%s multi_device=%s scores=%s",
            plat, _has_url, _has_mobile_otp, _is_multi_device, _scores,
        )
    except Exception as ex:
        uat_logger.warning("build semantic analysis failed: %s", ex)
        _task_decision_prefix = ""

    system_prompt = _build_system_prompt(
        project_name=params.project_name,
        current_plan=params.current_plan if isinstance(params.current_plan, dict) else {},
        page_snapshot=params.page_snapshot or "",
        dom_pack=params.dom_context_pack or "",
        memory_context=(params.memory_context or "") + _task_decision_prefix,
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
    # 在 user 消息前附加任务决策上下文，确保 LLM 读完任务决策再读用户任务
    _user_with_ctx = params.message
    if _task_decision_prefix:
        _user_with_ctx = _task_decision_prefix + "\n\n【用户任务】\n" + (_msg or "")
    messages.append({"role": "user", "content": _user_with_ctx})

    last_plan: Dict[str, Any] = dict(params.current_plan) if isinstance(params.current_plan, dict) else {}
    meta: Dict[str, Any] = _prepare_unified_agent_meta(params, plat)
    prof: Optional[Dict[str, Any]] = params.profile if isinstance(params.profile, dict) else None
    max_result = agent_tool_result_max_chars()
    _abort = abort_event or params.abort_event

    _last_profile_sig = ""

    def _refresh_profile_if_changed():
        nonlocal prof, _last_profile_sig
        try:
            from ai_multi_provider import get_active_llm_profile
            latest = get_active_llm_profile()
            if not latest:
                return
            sig = f"{latest.get('id')}|{latest.get('model_id')}"
            if sig != _last_profile_sig:
                prof = latest
                _last_profile_sig = sig
        except Exception:
            pass

    from windows_desktop_tools import SCREEN_TOOL_NAMES, WINDOWS_TOOL_NAMES

    for round_idx in range(_max_tool_rounds()):
        if _abort is not None and _abort.is_set():
            raise InterruptedError(_abort_user_message(_abort, params))
        if _deadline_exceeded(params):
            raise InterruptedError("任务已超过设定超时时间")
        if _desktop_flow_should_stop(meta):
            meta["final_round"] = round_idx
            meta["savable"] = False
            if meta.get("mobile_flow_halted"):
                reply = meta.get("halt_reply") or _mobile_halt_user_facing(
                    str(meta.get("mobile_last_failed_tool") or "mobile_*"),
                    json.dumps(
                        {"error": meta.get("mobile_last_error") or ""},
                        ensure_ascii=False,
                    ),
                )
            else:
                reply = meta.get("halt_reply") or _desktop_halt_user_facing(
                    str(meta.get("desktop_last_failed_tool") or "windows_*"),
                    json.dumps(
                        {"error": meta.get("desktop_last_error") or "", "suggestion": ""},
                        ensure_ascii=False,
                    ),
                )
            meta["halt_reply"] = reply
            _persist_unified_agent_session(params, meta, reply)
            return {}, [], meta
        _refresh_profile_if_changed()
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
                # OBS 观察计数：get_screen_text / get_screen_description 把 obs_count 置 1
                if name in SCREEN_TOOL_NAMES:
                    meta["obs_count"] = int((meta or {}).get("obs_count") or 0) + 1
                    try:
                        from ai_screen_observer import (
                            ensure_screen_observation_cached,
                            invalidate_screen_observation_cache,
                        )
                        invalidate_screen_observation_cache()
                        meta["last_screen_obs"] = ensure_screen_observation_cached(meta)
                    except Exception:
                        pass
                elif name in ("windows_click_element", "windows_type_text"):
                    obs_count = int((meta or {}).get("obs_count") or 0)
                    if obs_count == 0:
                        try:
                            from ai_screen_observer import ensure_screen_observation_cached
                            meta["last_screen_obs"] = ensure_screen_observation_cached(meta)
                            meta["obs_count"] = 1
                        except Exception:
                            pass
                    try:
                        cached = meta.get("last_screen_obs") or {}
                        if isinstance(cached, dict) and (cached.get("text_hints") or cached.get("blocks")):
                            meta["pending_ocr_hints"] = cached.get("text_hints") or []
                            meta["pending_ocr_blocks"] = cached.get("blocks") or []
                    except Exception:
                        pass
                skip_json = _should_skip_replay_desktop_tool(name, args or {}, meta)
                if skip_json:
                    result_text = skip_json
                    meta["tools_used"].append(f"{name}_skipped_replay")
                else:
                    call_args = _resolve_cross_end_vars(
                        dict(args or {}), meta.get("cross_end_vars")
                    )
                    if (
                        name == "windows_type_text"
                        and str(meta.get("desktop_phase") or meta.get("wechat_phase") or "")
                        in ("item_selected", "compose", "body_typed", "chat_open")
                    ):
                        call_args.setdefault("field", "compose")
                    if name in ("windows_click_element", "windows_type_text"):
                        call_args = _prepare_element_context(params, name, call_args, meta)
                    result_text = _dispatch_desktop_or_screen_tool(name, call_args)
                    if name in ("windows_click_element", "windows_type_text"):
                        if not _desktop_tool_succeeded(result_text):
                            result_text = _retry_failed_element_operation(
                                params, name, call_args, meta, result_text
                            )
                    meta["tools_used"].append(name)
                    _record_succeeded_desktop_action(meta, name, call_args, result_text)
            elif _is_cross_end_agent_tool(name):
                call_args = _resolve_cross_end_vars(
                    dict(args or {}), meta.get("cross_end_vars")
                )
                if getattr(params, "user_id", None) and not call_args.get("user_id"):
                    call_args["user_id"] = int(params.user_id)
                rem_tool = _remaining_deadline_sec(params)
                if name.startswith("mobile_"):
                    # 阻塞路径无 UI 进度；仍传 abort，避免空等
                    for pevt, pdata in _run_mobile_tool_with_progress(
                        name,
                        call_args,
                        abort_event=_abort,
                        timeout_cap=rem_tool,
                    ):
                        if pevt == "result":
                            result_text = str(pdata)
                else:
                    result_text = _dispatch_cross_end_agent_tool(
                        name, call_args, abort_event=_abort
                    )
                meta["tools_used"].append(name)
                if name.startswith("mobile_") and isinstance(meta.get("cross_end_vars"), dict) is False:
                    meta["cross_end_vars"] = {}
                try:
                    parsed_ce = json.loads(result_text)
                    if isinstance(parsed_ce, dict) and isinstance(parsed_ce.get("variables"), dict):
                        meta.setdefault("cross_end_vars", {}).update(parsed_ce["variables"])
                        if parsed_ce.get("sms_otp"):
                            meta.setdefault("cross_end_vars", {})["sms_otp"] = parsed_ce["sms_otp"]
                except Exception:
                    pass
                if name.startswith("mobile_"):
                    _record_mobile_tool_outcome(meta, name, result_text)
                # 把跨端工具调用记录到 ActionRecorder（供生成用例）
                _record_cross_end_or_api_to_recorder(params, name, call_args, result_text)
            elif name == "api_call":
                # api_call: API执行通道，与UI执行并列
                try:
                    from agent_api_runner import run_temp_http, run_api_case, summarize_for_agent
                    _case_id = (args or {}).get("case_id") or (call_args or {}).get("case_id")
                    if _case_id:
                        api_result = run_api_case(int(_case_id))
                    else:
                        api_result = run_temp_http(
                            method=str((args or {}).get("method") or "GET"),
                            url=str((args or {}).get("url") or ""),
                            headers=(args or {}).get("headers") if isinstance((args or {}).get("headers"), dict) else None,
                            body=(args or {}).get("body"),
                            timeout_sec=float((args or {}).get("timeout_sec") or 30.0),
                        )
                    result_text = summarize_for_agent(api_result)
                except Exception as _api_ex:
                    result_text = json.dumps({"ok": False, "error": f"api_call执行失败: {_api_ex}"}, ensure_ascii=False)
                meta["tools_used"].append("api_call")
                _record_cross_end_or_api_to_recorder(params, name, args or {}, result_text)
            else:
                result_text = json.dumps({"ok": False, "error": f"未知工具 {name}"}, ensure_ascii=False)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": result_text,
                }
            )
            if meta.get("mobile_flow_halted"):
                reply = meta.get("halt_reply") or _mobile_halt_user_facing(name, result_text)
                meta["halt_reply"] = reply
                meta["final_round"] = round_idx
                meta["savable"] = False
                _persist_unified_agent_session(params, meta, reply)
                return {}, [], meta
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


def run_unified_agent_blocking(
    *,
    local_ai_service: Any,
    params: ChatToolLoopParams,
    abort_event: Optional[threading.Event] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """同步跑统一 Agent（PC/手机入口共用）；返回 (plan, meta, reply)。"""
    last_reply = ""
    last_meta: Dict[str, Any] = {}
    last_plan: Dict[str, Any] = {}
    err = ""
    for evt, data in run_ai_chat_with_tools_stream(
        local_ai_service=local_ai_service,
        params=params,
        abort_event=abort_event,
    ):
        if evt == "reply" and isinstance(data, dict):
            t = (data.get("text") or "").strip()
            if t:
                last_reply = t
        elif evt == "done" and isinstance(data, dict):
            last_meta = data.get("meta") if isinstance(data.get("meta"), dict) else last_meta
            if isinstance(data.get("plan"), dict):
                last_plan = data["plan"]
            if (data.get("reply") or "").strip():
                last_reply = str(data.get("reply")).strip()
        elif evt == "error":
            err = str(data or "")
    if not last_reply and err:
        last_reply = err
    if not last_meta:
        last_meta = {"cross_end_vars": {}, "tools_used": []}
    if not last_reply:
        last_reply = last_meta.get("halt_reply") or last_meta.get("reply_text") or "（无文本回复）"
    _persist_unified_agent_session(params, last_meta, last_reply)
    return last_plan, last_meta, last_reply


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
        connected_hands=getattr(params, "connected_hands", None),
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
    meta: Dict[str, Any] = _prepare_unified_agent_meta(params, plat)
    prof: Optional[Dict[str, Any]] = params.profile if isinstance(params.profile, dict) else None
    max_result = agent_tool_result_max_chars()
    _abort = abort_event or params.abort_event

    _last_profile_sig = ""

    def _refresh_profile_if_changed_stream():
        nonlocal prof, _last_profile_sig
        try:
            from ai_multi_provider import get_active_llm_profile
            latest = get_active_llm_profile()
            if not latest:
                return
            sig = f"{latest.get('id')}|{latest.get('model_id')}"
            if sig != _last_profile_sig:
                prof = latest
                _last_profile_sig = sig
        except Exception:
            pass

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
            if meta.get("mobile_flow_halted"):
                reply = meta.get("halt_reply") or _mobile_halt_user_facing(
                    str(meta.get("mobile_last_failed_tool") or "mobile_*"),
                    json.dumps(
                        {"error": meta.get("mobile_last_error") or ""},
                        ensure_ascii=False,
                    ),
                )
                think_msg = "手机步骤连续失败，已停止自动重试"
            else:
                reply = meta.get("halt_reply") or _desktop_halt_user_facing(
                    str(meta.get("desktop_last_failed_tool") or "windows_*"),
                    json.dumps(
                        {"error": meta.get("desktop_last_error") or ""},
                        ensure_ascii=False,
                    ),
                )
                think_msg = "步骤失败，任务已停止"
            meta["final_round"] = round_idx
            meta["savable"] = False
            yield ("thinking", {"round": round_idx, "content": think_msg})
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
        last_args_think = 0
        _refresh_profile_if_changed_stream()
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
                                "content": f"模型选定工具：{tname}，正在生成参数（尚未真正执行）…",
                            },
                        )
                    alen = int((evt_data or {}).get("arguments_len") or 0)
                    if alen and alen - last_args_think >= 120:
                        last_args_think = alen
                        yield (
                            "thinking",
                            {
                                "round": round_idx,
                                "content": f"正在生成工具参数…（已约 {alen} 字符）",
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
                or (
                    f"{len(args.get('steps') or [])} steps"
                    if isinstance(args.get("steps"), list)
                    else None
                )
                or (f"case_id={args.get('case_id')}" if args.get("case_id") else None)
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
                # OBS 观察计数 + 前置 OCR（与 stream 分支保持一致）
                if name in SCREEN_TOOL_NAMES:
                    meta["obs_count"] = int((meta or {}).get("obs_count") or 0) + 1
                    try:
                        from ai_screen_observer import (
                            ensure_screen_observation_cached,
                            invalidate_screen_observation_cache,
                        )
                        invalidate_screen_observation_cache()
                        meta["last_screen_obs"] = ensure_screen_observation_cached(meta)
                    except Exception:
                        pass
                elif name in ("windows_click_element", "windows_type_text"):
                    obs_count = int((meta or {}).get("obs_count") or 0)
                    if obs_count == 0:
                        try:
                            from ai_screen_observer import ensure_screen_observation_cached
                            meta["last_screen_obs"] = ensure_screen_observation_cached(meta)
                            meta["obs_count"] = 1
                        except Exception:
                            pass
                    try:
                        cached = meta.get("last_screen_obs") or {}
                        if isinstance(cached, dict) and (cached.get("text_hints") or cached.get("blocks")):
                            meta["pending_ocr_hints"] = cached.get("text_hints") or []
                            meta["pending_ocr_blocks"] = cached.get("blocks") or []
                    except Exception:
                        pass
                skip_json = _should_skip_replay_desktop_tool(name, args or {}, meta)
                if skip_json:
                    result_text = skip_json
                    meta["tools_used"].append(f"{name}_skipped_replay")
                else:
                    call_args = _resolve_cross_end_vars(
                        dict(args or {}), meta.get("cross_end_vars")
                    )
                    if (
                        name == "windows_type_text"
                        and str(meta.get("desktop_phase") or meta.get("wechat_phase") or "")
                        in ("item_selected", "compose", "body_typed", "chat_open")
                    ):
                        call_args.setdefault("field", "compose")
                    if name in ("windows_click_element", "windows_type_text"):
                        call_args = _prepare_element_context(params, name, call_args, meta)
                    result_text = _dispatch_desktop_or_screen_tool(name, call_args)
                    if name in ("windows_click_element", "windows_type_text"):
                        if not _desktop_tool_succeeded(result_text):
                            result_text = _retry_failed_element_operation(
                                params, name, call_args, meta, result_text
                            )
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
            elif _is_cross_end_agent_tool(name):
                call_args = _resolve_cross_end_vars(
                    dict(args or {}), meta.get("cross_end_vars")
                )
                if getattr(params, "user_id", None) and not call_args.get("user_id"):
                    try:
                        call_args["user_id"] = int(params.user_id)
                    except Exception:
                        pass
                rem_tool = _remaining_deadline_sec(params)
                if name.startswith("mobile_"):
                    for pevt, pdata in _run_mobile_tool_with_progress(
                        name,
                        call_args,
                        abort_event=_abort,
                        timeout_cap=rem_tool,
                    ):
                        if pevt == "progress":
                            yield (
                                "thinking",
                                {"round": round_idx, "content": str(pdata)},
                            )
                        elif pevt == "result":
                            result_text = str(pdata)
                else:
                    result_text = _dispatch_cross_end_agent_tool(
                        name, call_args, abort_event=_abort
                    )
                meta["tools_used"].append(name)
                try:
                    parsed_ce = json.loads(result_text)
                    if isinstance(parsed_ce, dict):
                        if isinstance(parsed_ce.get("variables"), dict):
                            meta.setdefault("cross_end_vars", {}).update(parsed_ce["variables"])
                        if parsed_ce.get("sms_otp"):
                            meta.setdefault("cross_end_vars", {})["sms_otp"] = parsed_ce["sms_otp"]
                        preview = (
                            parsed_ce.get("sms_otp")
                            or parsed_ce.get("error")
                            or parsed_ce.get("job_id")
                            or ""
                        )
                        if preview:
                            yield ("vision_result", {"text": f"cross_end:{name} {preview}"[:300]})
                except Exception:
                    pass
                if name.startswith("mobile_"):
                    _record_mobile_tool_outcome(meta, name, result_text)
                _record_cross_end_or_api_to_recorder(params, name, call_args, result_text)
            elif name == "api_call":
                # api_call: API执行通道（流式路径）
                try:
                    from agent_api_runner import run_temp_http, run_api_case, summarize_for_agent
                    _case_id = (args or {}).get("case_id") or (call_args or {}).get("case_id")
                    if _case_id:
                        api_result = run_api_case(int(_case_id))
                    else:
                        api_result = run_temp_http(
                            method=str((args or {}).get("method") or "GET"),
                            url=str((args or {}).get("url") or ""),
                            headers=(args or {}).get("headers") if isinstance((args or {}).get("headers"), dict) else None,
                            body=(args or {}).get("body"),
                            timeout_sec=float((args or {}).get("timeout_sec") or 30.0),
                        )
                    result_text = summarize_for_agent(api_result)
                except Exception as _api_ex:
                    result_text = json.dumps({"ok": False, "error": f"api_call执行失败: {_api_ex}"}, ensure_ascii=False)
                meta["tools_used"].append("api_call")
                _record_cross_end_or_api_to_recorder(params, name, args or {}, result_text)
            else:
                result_text = json.dumps({"ok": False, "error": f"未知工具 {name}"}, ensure_ascii=False)

            yield ("tool_call_result", {
                "round": round_idx, "tool": name,
                "result_preview": result_text[:500],
            })

            if meta.get("mobile_flow_halted"):
                reply = meta.get("halt_reply") or _mobile_halt_user_facing(name, result_text)
                meta["halt_reply"] = reply
                meta["final_round"] = round_idx
                meta["savable"] = False
                yield ("thinking", {"round": round_idx, "content": "手机步骤连续失败，已停止自动重试"})
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
            if meta.get("mobile_flow_halted"):
                reply = meta.get("halt_reply") or _mobile_halt_user_facing(
                    str(meta.get("mobile_last_failed_tool") or "mobile_*"),
                    json.dumps(
                        {"error": meta.get("mobile_last_error") or ""},
                        ensure_ascii=False,
                    ),
                )
            else:
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

