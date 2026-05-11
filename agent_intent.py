"""
轻量「对话网关」意图解析：不调用 LLM，用关键词 + URL 抽取识别可执行命令。
与 /api/ai/agent/gateway-stream 配套；复杂意图仍走 /api/ai/task/chat。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Tuple

IntentKind = Literal["execute_plan", "navigate_url", "none"]

_URL_RE = re.compile(r"https?://[^\s\]\}\"'<>]+", re.I)


def _first_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    if not m:
        return ""
    u = m.group(0).rstrip(").,;]}\"'")
    return u


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

    return "none", {}
