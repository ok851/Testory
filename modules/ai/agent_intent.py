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
# 无协议主机：打开/访问 xxx.com/path
_BARE_HOST_RE = re.compile(
    r"(?:打开|访问|导航到?|跳转到?|前往|进入|登录到?|打开网址|访问网址|打开网站)"
    r"[\s:：]*"
    r"((?:www\.)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}"
    r"(?::\d{2,5})?(?:/[^\s\]\}\"'<>]*)?)",
    re.I,
)
_WWW_HOST_RE = re.compile(
    r"(?<![/@\w])(www\.(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}"
    r"(?::\d{2,5})?(?:/[^\s\]\}\"'<>]*)?)",
    re.I,
)
# 内网 IP / localhost（常不带协议）
_HOSTPORT_RE = re.compile(
    r"(?:打开|访问|导航到?|跳转到?|前往|进入|登录到?)[\s:：]*"
    r"((?:localhost|127\.0\.0\.1|(?:\d{1,3}\.){3}\d{1,3})(?::\d{2,5})?(?:/[^\s\]\}\"'<>]*)?)",
    re.I,
)


def _sanitize_extracted_url(raw: str) -> str:
    """清理从文本中提取的 URL，移除中文、空格和其他非法字符。
    
    注意：此函数只应用于已提取的 URL 片段，不适用于整个输入文本。
    """
    u = (raw or "").strip()
    if not u:
        return ""
    # 中文冒号、全角斜杠等
    u = u.replace("：", ":").replace("／", "/").replace("．", ".")
    
    # 清理 URL 中的中文字符（只对已提取的 URL 片段执行）
    # 找到第一个中文字符的位置，截断 URL
    _CHINESE_RE = re.compile(r'[\u4e00-\u9fff]')
    m = _CHINESE_RE.search(u)
    if m:
        u = u[:m.start()].rstrip()
    
    # 清理 URL 尾部的非法字符
    u = u.rstrip(").,;]}、，。；》〉\"'")
    # 清理尾部的空格和制表符
    u = u.rstrip()
    
    return u


def _ensure_http_scheme(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if re.match(r"^https?://", u, re.I):
        return u
    return "http://" + u.lstrip("/")


def _first_url(text: str) -> str:
    """从文本取首个 http(s) URL（兼容中文标点）。
    
    修复：先在整个文本中匹配 URL，再对提取的 URL 片段进行清理。
    避免对整个文本进行中文截断导致 URL 丢失。
    """
    t = (text or "").strip()
    if not t:
        return ""
    # 先把 http：// 归一（仅替换标点，不截断中文）
    t_norm = re.sub(r"https?\s*：\s*//", lambda m: m.group(0).replace("：", ":").replace(" ", ""), t, flags=re.I)
    t_norm = t_norm.replace("http：//", "http://").replace("https：//", "https://")
    m = _URL_RE.search(t_norm)
    if not m:
        return ""
    # 对提取的 URL 片段进行清理（移除中文等非法字符）
    return _sanitize_extracted_url(m.group(0))


def extract_task_url(text: str, *, allow_seed: bool = True) -> str:
    """
    从用户任务原文解析浏览器起始 URL（无独立 URL 输入框时的唯一来源）。

    优先级：显式 http(s) → 打开/访问+主机或 IP → www.主机 → 常见站点种子（百度等）。
    """
    t = (text or "").strip()
    if not t:
        return ""
    hit = _first_url(t)
    if hit:
        return hit

    for rx in (_HOSTPORT_RE, _BARE_HOST_RE, _WWW_HOST_RE):
        m = rx.search(t)
        if m:
            return _ensure_http_scheme(_sanitize_extracted_url(m.group(1)))

    if allow_seed:
        try:
            from modules.ai.ai_local_inference import _goal_suggests_seed_url

            seed = (_goal_suggests_seed_url(t) or "").strip()
            if seed:
                return seed
        except Exception:
            pass
    return ""

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
    "打开搜狗",
    "打开必应",
    "打开新浪",
    "打开知乎",
    "打开b站",
    "打开bilibili",
    "打开抖音",
    "打开小红书",
    "打开github",
    "打开stackoverflow",
    "访问百度",
    "访问谷歌",
    "访问淘宝",
    "访问京东",
    "访问网页",
    "访问网站",
    "web 测试",
    "web测试",
    "web页面",
    "h5",
    "url",
    "http",
    "https",
    ".com",
    ".cn",
    ".net",
    ".org",
    "搜索一下",
    "搜一下",
    "搜索引擎",
    "网页版",
    "网页中",
    "网站上",
    "网站里",
    "bilibili",
    "知乎",
    "豆瓣",
    "微博",
    "小红书",
    "抖音",
    "b站",
    "闲鱼",
    "拼多多",
    "美团",
    "饿了么",
    "当当",
    "亚马逊",
    "amazon",
    "ebay",
    "shopify",
    "wordpress",
    "csdn",
    "掘金",
    "博客园",
    "简书",
    "v2ex",
    "hacker news",
    "product hunt",
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
    "vscode",
    "visual studio",
    # 注意：chrome / edge / firefox 属于浏览器，归类为 Web 任务，不得放在桌面 App 列表！
    # 否则会造成评分冲突："打开 Edge" → 同时命中 desktop+=5 和 web+=3
    "wps",
    "截图",
    "任务管理器",
    "taskmgr",
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

# 手机侧 await 能力（取码 / 本机执行）—— 不计为「纯 android 巡检」平台分
_MOBILE_AWAIT_HINTS = (
    "验证码",
    "短信验证码",
    "短信",
    "通知栏",
    "取码",
    "取验证码",
    "获取验证码",
    "sms_otp",
    "mobile_extract",
    "本机执行",
    "手机本机",
    "从手机",
    "到手机",
    "在手机",
    "移动端取",
    "从移动端",
    "移动端获取",
    "通知验证码",
    "mobile_run",
    "跑手机",
)

_CROSS_END_HINTS = (
    "跨端",
    "联动",
    "多端",
    "回填",
    "两端",
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
    # 需要手机本机 await（OTP / run_steps 等），与 platform=android 解耦
    needs_mobile_await: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _score_mobile_await(message: str) -> int:
    t = (message or "").strip()
    tl = t.lower()
    score = 0
    for h in _MOBILE_AWAIT_HINTS:
        if h in t or h in tl:
            score += 3
    return score


def _has_cross_end_hint(message: str) -> bool:
    t = (message or "").strip()
    return any(h in t for h in _CROSS_END_HINTS)


def message_needs_mobile_await(message: str) -> bool:
    """是否需要手机侧 await 工具（取码/本机跑步骤），不绑定具体业务剧本。"""
    return _score_mobile_await(message) > 0


_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")


def extract_cross_end_seed_vars(message: str) -> Dict[str, str]:
    """从用户话可选抽取初始变量（便利，非强制剧本）。"""
    out: Dict[str, str] = {}
    t = (message or "").strip()
    if not t:
        return out
    m = _PHONE_RE.search(t)
    if m:
        out["phone_number"] = m.group(1)
    m2 = re.search(
        r"(?:登录|登陆|打开|启动|注册)\s*"
        r"([^\s，,。：:的到并和与]{1,24})",
        t,
    )
    if m2:
        name = (m2.group(1) or "").strip()
        skip = {
            "一下", "软件", "应用", "手机", "移动端", "桌面", "本机",
            "系统", "这个", "那个", "一下吧",
        }
        if name and name not in skip and not name.isdigit():
            out["app_name"] = name
    return out


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
        from modules.desktop.agent_desktop_fastpath import is_desktop_nl_task, looks_like_wechat_send_task

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
    mobile_await_s = _score_mobile_await(t)
    needs_mobile_await = mobile_await_s > 0
    cross_hint = _has_cross_end_hint(t)
    action = _has_action_signal(t) or needs_mobile_await or cross_hint
    chatish = any(h in t for h in _CHAT_HINTS) and not action

    if chatish and web_s == 0 and desk_s == 0 and andr_s == 0 and not needs_mobile_await:
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
            needs_mobile_await=False,
        )

    # 能力面：桌面操作 + 手机 await，或明确跨端词 → 挂桌面外层工具，勿因「手机」掉进 android-only
    desk_ops = desk_s > 0 or any(
        k in t for k in ("登录", "登陆", "注册", "填写", "回填", "输入", "打开", "启动", "提交")
    )
    # 【修复】浏览器任务优先：如果 web_score 明显高于 desktop，即使有 mobile_await 也走 web
    if web_s >= 3 and web_s > desk_s and needs_mobile_await:
        return TaskRoute(
            mode="automation",
            platform="web",
            needs_automation=True,
            needs_browser=True,
            needs_desktop_tools=False,
            ui_platform=ui,
            reason="cross_end_web_priority",
            web_score=web_s,
            desktop_score=desk_s,
            android_score=andr_s,
            needs_mobile_await=True,
        )
    if needs_mobile_await and (desk_ops or cross_hint or desk_s > 0 or "桌面" in t):
        return TaskRoute(
            mode="automation",
            platform="desktop",
            needs_automation=True,
            needs_browser=False,
            needs_desktop_tools=True,
            ui_platform=ui,
            reason="cross_end_capabilities",
            web_score=web_s,
            desktop_score=max(desk_s, 1),
            android_score=andr_s,
            needs_mobile_await=True,
        )
    # 仅手机 await（取码 / 本机跑），无桌面信号：仍自动化，但不抢 desktop 工具
    if needs_mobile_await and not desk_ops and desk_s == 0 and not cross_hint:
        return TaskRoute(
            mode="automation",
            platform="android" if ui != "desktop" else "desktop",
            needs_automation=True,
            needs_browser=False,
            needs_desktop_tools=(ui == "desktop"),
            ui_platform=ui,
            reason="mobile_await_only",
            web_score=web_s,
            desktop_score=desk_s,
            android_score=max(andr_s, 1),
            needs_mobile_await=True,
        )

    # 无自动化信号 → 默认 chat（避免误启浏览器）
    # 但 UI 已显式选 desktop/android/web 时，按 UI 走自动化（弱话术仍可执行）
    if not action and web_s == 0 and desk_s == 0 and andr_s == 0:
        if ui == "desktop":
            return TaskRoute(
                mode="automation",
                platform="desktop",
                needs_automation=True,
                needs_browser=False,
                needs_desktop_tools=True,
                ui_platform=ui,
                reason="ui_desktop_no_message_signal",
                web_score=web_s,
                desktop_score=desk_s,
                android_score=andr_s,
            )
        if ui == "android":
            return TaskRoute(
                mode="automation",
                platform="android",
                needs_automation=True,
                needs_browser=False,
                needs_desktop_tools=False,
                ui_platform=ui,
                reason="ui_android_no_message_signal",
                web_score=web_s,
                desktop_score=desk_s,
                android_score=andr_s,
            )
        if ui == "web":
            return TaskRoute(
                mode="automation",
                platform="web",
                needs_automation=True,
                needs_browser=True,
                needs_desktop_tools=False,
                ui_platform=ui,
                reason="ui_web_no_message_signal",
                web_score=web_s,
                desktop_score=desk_s,
                android_score=andr_s,
            )
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

    # 由分数选消息面平台（手机 await 分不计入 android 平台分，避免误抢）
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
        needs_mobile_await=needs_mobile_await,
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
    url = extract_task_url(t, allow_seed=False) or _first_url(t)
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
