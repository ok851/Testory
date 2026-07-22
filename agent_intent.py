# -*- coding: utf-8 -*-
"""
任务路由：统一区分 chat / web / desktop / android，避免桌面任务走网页脑或反之。

不调用 LLM：关键词 + URL + 应用名启发式。执行入口应优先使用 resolve_task_route。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Optional, Tuple

IntentKind = Literal["execute_plan", "navigate_url", "hermes_explore", "none"]
TaskMode = Literal["chat", "automation"]
TaskPlatform = Literal["web", "desktop", "android", "auto"]

_URL_RE = re.compile(r"https?://[^\s\]\}\"'<>]+", re.I)

# 明确像闲聊/问答（须整句偏闲聊；正文里的「你好」不能误伤发消息）
_CHAT_HINTS = (
    "你是谁",
    "你是什么",
    "你叫什么",
    "帮忙介绍",
    "介绍一下",
    "有什么能力",
    "能做什么",
    "怎么用",
    "如何使用",
    "什么是",
    "帮我看看方案",
    "测试建议",
    "用例设计",
    "写个思路",
    "解释一下",
    "为什么",
    "是什么意思",
)

_GREETING_ONLY = (
    "你好",
    "您好",
    "谢谢",
    "感谢",
    "嗨",
    "hello",
    "hi",
)

# 通用自动化动作词（不单独决定 web/desktop）
_ACTION_HINTS = (
    "打开",
    "访问",
    "导航",
    "跳转",
    "点击",
    "输入",
    "登录",
    "注册",
    "搜索",
    "提交",
    "上传",
    "下载",
    "关闭",
    "滚动",
    "截图",
    "断言",
    "验证",
    "检查页面",
    "走通",
    "探索",
    "跑一遍",
    "执行用例",
    "执行步骤",
    "执行预览",
    "帮我测",
    "测试一下",
    "自动化",
    "聚焦",
    "发送",
    "发消息",
    "发给",
    "发一句",
    "发一条",
    "发条",
    "navigate",
    "click",
    "goto",
)

_WEB_HINTS = (
    "网页",
    "网站",
    "浏览器",
    "浏览器里",
    "页面",
    "登录页",
    "打开百度",
    "打开谷歌",
    "打开淘宝",
    "打开京东",
    "web 测试",
    "web测试",
    "h5",
    "url",
)

_DESKTOP_APP_HINTS = (
    "微信",
    "wechat",
    "weixin",
    "企业微信",
    "wecom",
    "wxwork",
    "qq",
    "记事本",
    "notepad",
    "计算器",
    "calculator",
    "calc",
    "控制面板",
    "资源管理器",
    "文件资源管理器",
    "explorer",
    "画图",
    "mspaint",
    "powershell",
    "命令提示符",
    "cmd",
    "windows 设置",
    "系统设置",
    "ms-settings",
    "excel",
    "word",
    "outlook",
    "钉钉",
    "飞书",
    "企微",
)

_DESKTOP_CONTEXT_HINTS = (
    "桌面",
    "桌面应用",
    "桌面软件",
    "本地电脑",
    "本机",
    "本地的",
    "我本地",
    "电脑上",
    "操作系统",
    "windows",
    "本地软件",
    "本地应用",
    "启动应用",
    "打开软件",
    "uia",
    "launch_app",
)

_ANDROID_HINTS = (
    "安卓",
    "android",
    "手机 app",
    "手机应用",
    "真机",
    "模拟器",
    "adb",
    "appium",
    "scrcpy",
)


@dataclass(frozen=True)
class TaskRoute:
    """统一路由结果：执行入口只认这一份。"""

    mode: TaskMode
    platform: TaskPlatform
    needs_automation: bool
    needs_browser: bool
    needs_desktop_tools: bool
    ui_platform: str
    reason: str
    web_score: int = 0
    desktop_score: int = 0
    android_score: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _first_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    if not m:
        return ""
    return m.group(0).rstrip(").,;]}\"'")


def _looks_like_greeting_only(message: str) -> bool:
    t = (message or "").strip()
    if not t:
        return False
    compact = re.sub(r"[\s，。！？、,.!?;:：]+", "", t)
    if len(compact) <= 6 and any(g in t for g in _GREETING_ONLY):
        return True
    if len(t) <= 12 and any(t == g or t.startswith(g) for g in _GREETING_ONLY):
        return True
    return False


def _score_surfaces(message: str) -> Tuple[int, int, int]:
    """返回 (web_score, desktop_score, android_score)。"""
    t = (message or "").strip()
    tl = t.lower()
    web = 0
    desk = 0
    andr = 0

    if _first_url(t):
        web += 8
    for h in _WEB_HINTS:
        if h in t or h in tl:
            web += 3
    for h in _DESKTOP_APP_HINTS:
        if h in t or h in tl:
            desk += 5
    for h in _DESKTOP_CONTEXT_HINTS:
        if h in t or h in tl:
            desk += 3
    for h in _ANDROID_HINTS:
        if h in t or h in tl:
            andr += 5

    # 桌面专用动作组合：发消息 + 无 URL/网页词 → 加强 desktop
    if any(k in t for k in ("发消息", "发给", "发一句", "发一条", "发条", "发送")):
        if web == 0:
            desk += 2
    # 「打开 X」且 X 像桌面应用
    if re.search(r"(?:打开|启动|运行)\s*(?:一下)?\s*(?:本地(?:的|电脑)?|本机)?", t):
        if desk > 0:
            desk += 1
        elif web == 0 and andr == 0:
            # 打开某某但无 web 信号：弱 desktop（可能是记事本等未命中表）
            desk += 1

    try:
        from agent_desktop_fastpath import is_desktop_nl_task, looks_like_wechat_send_task

        if is_desktop_nl_task(t) or looks_like_wechat_send_task(t):
            desk = max(desk, 6)
    except Exception:
        pass

    return web, desk, andr


def _has_action_signal(message: str) -> bool:
    t = (message or "").strip()
    tl = t.lower()
    if _first_url(t):
        return True
    if any(h in t for h in _ACTION_HINTS) or any(h in tl for h in _ACTION_HINTS):
        return True
    if any(h in t or h in tl for h in _DESKTOP_APP_HINTS):
        return True
    if any(h in t or h in tl for h in _DESKTOP_CONTEXT_HINTS):
        return True
    if any(h in t or h in tl for h in _WEB_HINTS):
        return True
    if any(h in t or h in tl for h in _ANDROID_HINTS):
        return True
    return False


def resolve_task_route(
    message: str,
    *,
    ui_platform: str = "auto",
) -> TaskRoute:
    """根据用户话 + UI 平台选择，得到最终执行路由。

    规则优先级：
    1. 纯闲聊 → chat
    2. 消息面信号强（web/desktop/android 分）决定自动化平台
    3. UI 为 auto 时完全跟消息；UI 显式指定时，若消息强烈矛盾则以消息为准（防桌面当网页）
    """
    t = (message or "").strip()
    ui = (ui_platform or "auto").strip().lower() or "auto"
    if ui in ("all", "cross", ""):
        ui = "auto"

    if not t:
        return TaskRoute(
            mode="chat",
            platform="auto" if ui == "auto" else ui,  # type: ignore[arg-type]
            needs_automation=False,
            needs_browser=False,
            needs_desktop_tools=False,
            ui_platform=ui,
            reason="empty_message",
        )

    if _looks_like_greeting_only(t):
        return TaskRoute(
            mode="chat",
            platform="auto" if ui == "auto" else ui,  # type: ignore[arg-type]
            needs_automation=False,
            needs_browser=False,
            needs_desktop_tools=False,
            ui_platform=ui,
            reason="greeting_only",
        )

    web_s, desk_s, andr_s = _score_surfaces(t)
    action = _has_action_signal(t)
    chatish = any(h in t for h in _CHAT_HINTS) and not action

    if chatish and web_s == 0 and desk_s == 0 and andr_s == 0:
        return TaskRoute(
            mode="chat",
            platform="auto" if ui == "auto" else ui,  # type: ignore[arg-type]
            needs_automation=False,
            needs_browser=False,
            needs_desktop_tools=False,
            ui_platform=ui,
            reason="chat_question",
            web_score=web_s,
            desktop_score=desk_s,
            android_score=andr_s,
        )

    # 无自动化信号 → 默认 chat（避免误启浏览器）
    if not action and web_s == 0 and desk_s == 0 and andr_s == 0:
        return TaskRoute(
            mode="chat",
            platform="auto" if ui == "auto" else ui,  # type: ignore[arg-type]
            needs_automation=False,
            needs_browser=False,
            needs_desktop_tools=False,
            ui_platform=ui,
            reason="no_automation_signal",
            web_score=web_s,
            desktop_score=desk_s,
            android_score=andr_s,
        )

    # 由分数选消息面平台
    msg_platform: TaskPlatform = "auto"
    reason = "auto_balanced"
    if andr_s > 0 and andr_s >= desk_s and andr_s >= web_s:
        msg_platform = "android"
        reason = "android_signals"
    elif desk_s > 0 and desk_s > web_s:
        msg_platform = "desktop"
        reason = "desktop_signals"
    elif web_s > 0 and web_s >= desk_s:
        msg_platform = "web"
        reason = "web_signals"
    elif desk_s > 0:
        msg_platform = "desktop"
        reason = "desktop_weak"
    elif action:
        # 有动作词但分不清：跟 UI，否则 web（历史默认）
        msg_platform = ui if ui in ("web", "desktop", "android") else "web"
        reason = "action_fallback_ui_or_web"

    # 合并 UI：消息强信号覆盖错误 UI
    final: TaskPlatform
    if ui == "auto":
        final = msg_platform if msg_platform != "auto" else "web"
        reason = f"ui_auto+{reason}"
    elif ui == msg_platform or msg_platform == "auto":
        final = ui  # type: ignore[assignment]
        reason = f"ui_{ui}+{reason}"
    else:
        # 冲突：消息分更高则覆盖 UI（例如 UI=web 但话术是记事本/微信）
        ui_score = {"web": web_s, "desktop": desk_s, "android": andr_s}.get(ui, 0)
        msg_score = {"web": web_s, "desktop": desk_s, "android": andr_s}.get(msg_platform, 0)
        if msg_score >= max(2, ui_score + 2):
            final = msg_platform
            reason = f"override_ui_{ui}_by_message_{msg_platform}"
        else:
            final = ui  # type: ignore[assignment]
            reason = f"keep_ui_{ui}_despite_{msg_platform}"

    needs_desk = final == "desktop"
    needs_br = final == "web"
    # cross/auto 极少落到这里；若仍 auto 当 web 工具链
    if final == "auto":
        final = "web"
        needs_br = True

    return TaskRoute(
        mode="automation",
        platform=final,
        needs_automation=True,
        needs_browser=needs_br,
        needs_desktop_tools=needs_desk,
        ui_platform=ui,
        reason=reason,
        web_score=web_s,
        desktop_score=desk_s,
        android_score=andr_s,
    )


# ---- 兼容旧 API（委托到 resolve_task_route）----

# 保留旧常量名，供外部/测试引用
_AUTOMATION_HINTS = _ACTION_HINTS + _DESKTOP_APP_HINTS + _DESKTOP_CONTEXT_HINTS + (
    "http://",
    "https://",
    "操作页面",
    "操作浏览器",
)


def message_needs_automation(message: str) -> bool:
    """粗判用户是否要做真实操作（浏览器/桌面）。闲聊返回 False。"""
    return bool(resolve_task_route(message, ui_platform="auto").needs_automation)


def message_needs_browser(message: str) -> bool:
    """是否需要本机浏览器（Web）。桌面-only 任务返回 False。"""
    route = resolve_task_route(message, ui_platform="auto")
    return bool(route.needs_automation and route.needs_browser)


def parse_agent_intent(message: str, has_plan_steps: bool) -> Tuple[IntentKind, Dict[str, Any]]:
    """
    has_plan_steps: 当前是否已有用例预览 steps（来自 latestAiPlan）。
    返回 (intent_kind, meta)；meta 对 navigate_url 含 url 键。
    """
    t = (message or "").strip()
    if not t:
        return "none", {}

    tl = t.lower()
    run_triggers = (
        "执行当前预览",
        "执行预览",
        "执行步骤",
        "只执行",
        "跑一遍预览",
        "跑一遍",
        "运行预览",
        "执行用例",
        "run plan",
        "execute plan",
        "execute preview",
    )
    if has_plan_steps and any(k in t for k in run_triggers):
        return "execute_plan", {}
    if has_plan_steps and any(k in tl for k in ("execute plan", "execute preview", "run plan")):
        return "execute_plan", {}

    nav_words = ("打开", "导航", "访问", "跳转", "goto", "navigate", "open ")
    url = _first_url(t)
    if url and any(w in t for w in nav_words):
        return "navigate_url", {"url": url}
    if url and re.match(r"^https?://", t.strip()):
        return "navigate_url", {"url": url}

    explore_triggers = (
        "探索",
        "走通",
        "Hermes",
        "hermes",
        "agent 执行",
        "自然语言执行",
        "帮我测",
    )
    if any(k in t for k in explore_triggers):
        return "hermes_explore", {"message": t}

    return "none", {}
