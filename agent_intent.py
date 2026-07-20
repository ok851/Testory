"""
轻量「对话网关」意图解析：不调用 LLM，用关键词 + URL 抽取识别可执行命令。
与 /api/ai/agent/gateway-stream 配套；复杂意图仍走 /api/ai/task/chat。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Literal, Tuple

IntentKind = Literal["execute_plan", "navigate_url", "hermes_explore", "none"]

_URL_RE = re.compile(r"https?://[^\s\]\}\"'<>]+", re.I)

# 明确像闲聊/问答：不预启浏览器
_CHAT_HINTS = (
    "你是谁",
    "你是什么",
    "你叫什么",
    "你好",
    "您好",
    "谢谢",
    "感谢",
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

# 明确需要真实操作 / 浏览器或桌面自动化
_AUTOMATION_HINTS = (
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
    "操作页面",
    "操作浏览器",
    "启动应用",
    "打开软件",
    "控制面板",
    "记事本",
    "计算器",
    "navigate",
    "click",
    "goto",
    "open http",
    "http://",
    "https://",
)


def _first_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    if not m:
        return ""
    u = m.group(0).rstrip(").,;]}\"'")
    return u


def message_needs_automation(message: str) -> bool:
    """粗判用户是否要做真实操作（浏览器/桌面）。闲聊/问答返回 False，避免先拉起浏览器。"""
    t = (message or "").strip()
    if not t:
        return False
    tl = t.lower()
    if _first_url(t):
        return True
    if any(h in t for h in _AUTOMATION_HINTS) or any(h in tl for h in _AUTOMATION_HINTS):
        return True
    # 短句 + 闲聊特征 → 对话
    if len(t) <= 40 and any(h in t for h in _CHAT_HINTS):
        return False
    if any(h in t for h in _CHAT_HINTS) and not any(h in t for h in ("打开", "点击", "登录", "测试")):
        return False
    # 默认不预启浏览器；是否调用 hermes_execute 交给 LLM 意图判断
    return False


def message_needs_browser(message: str) -> bool:
    """是否需要本机浏览器（Web）。桌面-only 任务返回 False。"""
    if not message_needs_automation(message):
        return False
    t = (message or "").strip()
    tl = t.lower()
    desktop_hints = (
        "记事本",
        "计算器",
        "控制面板",
        "资源管理器",
        "桌面应用",
        "启动应用",
        "打开软件",
        "windows 设置",
        "系统设置",
        "本地电脑",
        "本机",
        "本地的",
        "我本地",
        "电脑上",
        "操作系统",
        "微信",
        "wechat",
        "企业微信",
        "uia",
        "launch_app",
    )
    web_hints = (
        "http://",
        "https://",
        "网页",
        "网站",
        "浏览器",
        "页面",
        "登录页",
        "打开百度",
        "打开谷歌",
    )
    has_desktop = any(h in t for h in desktop_hints) or any(h in tl for h in desktop_hints)
    has_web = bool(_first_url(t)) or any(h in t for h in web_hints) or any(h in tl for h in web_hints)
    if has_desktop and not has_web:
        return False
    return True


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
