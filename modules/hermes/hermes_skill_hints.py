# -*- coding: utf-8 -*-
"""Hermes explore 时按平台 / 能力注册表按需加载 bundled skill 提示。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

BUNDLED_SKILL_BY_PLATFORM: Dict[str, str] = {
    "web": "testory-web-browser",
    "mobile": "testory-android-mobile",
    "desktop": "testory-windows-desktop",
    "api": "testory-api-http",
    "ui": "testory-ui-design",
    "cross": "testory-cross-end",
    "security": "testory-risk-guard",
    "risk": "testory-risk-guard",
    "explore": "testory-ai-explore",
    "dialog": "testory-ai-dialog",
}

DEFAULT_WEB_SKILLS = ["testory-web-browser", "testory-ai-explore"]
DEFAULT_MOBILE_SKILLS = ["testory-android-mobile", "testory-ai-dialog"]
DEFAULT_DESKTOP_SKILLS = ["testory-windows-desktop", "testory-ai-explore"]
DEFAULT_API_SKILLS = ["testory-api-http", "testory-cross-end"]
DEFAULT_UI_SKILLS = ["testory-ui-design", "testory-web-browser"]
DEFAULT_CROSS_SKILLS = ["testory-cross-end"]
DEFAULT_AUTO_SKILLS = [
    "testory-web-browser",
    "testory-windows-desktop",
    "testory-api-http",
    "testory-cross-end",
]


def skills_for_platform(platform: str) -> List[str]:
    p = (platform or "web").strip().lower()
    if p in ("auto", "all", "cross"):
        # 不默认塞 mobile：由能力注册表按需追加，避免无设备时幻觉
        return list(DEFAULT_AUTO_SKILLS if p != "cross" else DEFAULT_CROSS_SKILLS)
    if p == "mobile" or p == "android":
        return list(DEFAULT_MOBILE_SKILLS)
    if p == "desktop":
        return list(DEFAULT_DESKTOP_SKILLS)
    if p == "api":
        return list(DEFAULT_API_SKILLS)
    if p == "ui":
        return list(DEFAULT_UI_SKILLS)
    if p == "explore":
        return list(DEFAULT_WEB_SKILLS)
    if p == "dialog":
        return list(DEFAULT_MOBILE_SKILLS)
    return list(DEFAULT_WEB_SKILLS)


def skills_from_registry(platform: str = "auto") -> List[str]:
    """按当前可用能力裁剪 skill 列表（禁止无脑预加载全集）。"""
    base = skills_for_platform(platform)
    try:
        from modules.ai.agent_capability_registry import skills_for_available_caps

        available = skills_for_available_caps()
        if not available:
            return base
        # 交集优先；若交集为空则退回 base 中与 available 的并集裁剪
        inter = [s for s in base if s in available]
        if inter:
            # 可用但 base 没有的（如 mobile 刚连上）也追加
            for s in available:
                if s not in inter and s.startswith("testory-"):
                    if s == "testory-android-mobile" or s in base or platform in ("auto", "all"):
                        if s not in inter:
                            inter.append(s)
            return inter
        # 显式平台（如 mobile）在能力表无交集时仍用 base，禁止被桌面/API skill 顶替
        p = (platform or "").strip().lower()
        if p and p not in ("auto", "all"):
            return base
        return [s for s in available if s.startswith("testory-")][:6] or base
    except Exception:
        return base


def desktop_gateway_auth_hint() -> str:
    """注入给 Hermes 的桌面执行边界（启动智能体时自动挂 MCP + :8766）。"""
    try:
        from modules.desktop.desktop_service_bootstrap import resolve_desktop_gateway_secret

        resolve_desktop_gateway_secret(persist_to_hermes=True)
    except Exception:
        try:
            from modules.desktop.desktop_service_bootstrap import _ensure_desktop_env_defaults

            _ensure_desktop_env_defaults(force=True)
        except Exception:
            pass
    import os
    import sys

    url = (os.environ.get("DESKTOP_AGENT_GATEWAY_URL") or "http://127.0.0.1:8766").rstrip("/")
    mcp_url = (os.environ.get("TESTORY_DESKTOP_MCP_URL") or "http://127.0.0.1:9820/mcp").rstrip("/")
    lines = [
        "【桌面执行边界 — 启动智能体后默认可用】\n",
        "- Hermes Gateway `:8642`：跨层探索脑；**Windows 桌面短任务优先由平台外层 windows_* 直接执行**。\n",
        f"- MCP `testory-desktop`（`{mcp_url}`）与 Desktop Gateway `{url}` 已就绪。\n",
        "- 若你仍被调用：只调用 MCP windows_* / get_screen_*，"
        "**禁止**用 terminal/curl 探测 MCP，**禁止**因缺 Git Bash 空转重试。\n",
        "- 优先：windows_focus_app → get_screen_* → type/press/click。\n",
    ]
    if sys.platform == "darwin":
        lines.append(
            "- macOS 可选：`skill_view('computer-use')` + `computer_use`（需 cua-driver）。\n"
        )
    else:
        lines.append(
            "- Windows 上官方 computer_use/cua 通常不可用；不要空等 computer_use，直接用 MCP windows_*。\n"
        )
    lines.append(
        "- 上游模型余额不足/鉴权失败或空流时禁止反复重试；用中文说明原因"
        "（充值/换模型，或请用户点「停止」再「启动」智能体），勿提及环境变量名。\n"
    )
    return "".join(lines)


def web_browser_cdp_hint() -> str:
    """注入给 Hermes 的网页 CDP 执行边界（DOM 优先，禁止 navigate/视觉空转）。
    
    改进：增加 JavaScript 备选方案，优化 browser_snapshot 使用策略。
    """
    import os

    cdp = (os.environ.get("HERMES_CDP_ENDPOINT") or "").strip()
    mode = (os.environ.get("HERMES_BROWSER_MODE") or "cdp_attach").strip()
    lines = [
        "【网页执行边界 — 本机浏览器已由平台打开并 CDP attach】\n",
        f"- HERMES_BROWSER_MODE={mode}；只用 browser_* 操作**当前已打开标签页**。\n",
        "- **禁止** 调用任何 skill_* 类工具（含查看技能文档）；规则已全部内联。\n",
        "- **禁止** terminal / curl / bash / windows_* / 桌面 MCP。\n",
        "- **禁止** 新开空白标签页；平台已导航则 **禁止 browser_navigate**（重复造轮子）。\n",
        "- **DOM 优先**：指令内「页面 DOM/可交互控件」是主定位源；有清单则直接 browser_click/browser_type；"
        "browser_snapshot=无障碍树/DOM ref（不是截图）：仅难定位时兜底一次"
        "（全程最多 2 次，禁止连续反复）；视觉/截图仅最终兜底。\n",
    ]
    if cdp:
        lines.append("- CDP 已同步；勿再探测调试端口。\n")
    lines.extend(
        [
            "- 正确顺序：读 DOM 清单 → browser_click / browser_type / browser_fill → "
            "必要时再 snapshot 核验。\n",
            "- **备选方案**：若 DOM 清单不足且 snapshot 也无法定位，"
            "使用 browser_console(expression=\"...\") 执行 JavaScript 直接操作 DOM：\n"
            "  - document.querySelector / getElementById 定位元素\n"
            "  - element.click() / element.value = text / element.dispatchEvent 触发事件\n"
            "  - document.querySelectorAll 获取元素列表\n",
            "- **重要**：snapshot 返回的 ref ID（如 @e5）仅在当前会话有效，"
            "若页面刷新或重新导航后必须重新 snapshot。\n",
            "- 同一工具连续 2 次无进展：换策略（改用 JavaScript）或 NEED_USER_ACTION。\n",
            "- 遇登录/验证码：NEED_USER_ACTION，请用户在本机窗口完成。\n",
        ]
    )
    return "".join(lines)


def build_explore_instruction(message: str, meta: Optional[Dict[str, Any]] = None) -> str:
    """为 Hermes explore 构建带平台边界的 instruction（网页任务不再要求 skill_view）。"""
    meta = meta if isinstance(meta, dict) else {}
    platform = (meta.get("platform") or "auto").strip().lower()
    skills = meta.get("skills")
    if not isinstance(skills, list) or not skills:
        skills = skills_from_registry(platform)
    skill_names = [str(s).strip() for s in skills if str(s).strip()]
    # 纯网页任务：去掉桌面/移动 skill，避免 Agent 去调 windows_* / terminal 探 MCP
    if platform == "web":
        skill_names = [
            s
            for s in skill_names
            if "desktop" not in s and "android" not in s and "mobile" not in s
        ] or ["testory-web-browser"]
    skill_line = "、".join(f"`{n}`" for n in skill_names)
    caps_note = ""
    if meta.get("capabilities_summary"):
        caps_note = f"当前能力：{meta.get('capabilities_summary')}\n"
    vision_note = ""
    if meta.get("vision_summary"):
        vision_note = f"【屏幕视觉观察】\n{str(meta.get('vision_summary'))[:800]}\n\n"
    ctx_prefix = (meta.get("context_prefix") or "").strip()
    if ctx_prefix and not ctx_prefix.endswith("\n"):
        ctx_prefix += "\n"

    already_on_page = bool(meta.get("already_on_target_page"))
    start_url = str(meta.get("start_url") or "").strip()

    if platform == "web":
        nav_line = (
            f"- 平台已打开目标页{('：' + start_url) if start_url else ''}；"
            "用 DOM 清单直接操作；禁止 navigate / 连续 snapshot / skill / terminal。\n"
            if already_on_page
            else (
                f"- 若当前不是目标页，仅允许 **一次** browser_navigate"
                f"{(' 到 ' + start_url) if start_url else ''}，然后按 DOM 操作；禁止重复 navigate。\n"
            )
        )
        prefix = (
            f"【Testory 平台上下文 platform=web — 网页专用，与桌面任务隔离】\n"
            f"{caps_note}"
            f"{web_browser_cdp_hint()}"
            f"{nav_line}"
            "你是网页自动化执行代理：只通过 browser_*（CDP）操作本机已打开浏览器。\n"
            "【元素定位策略 — 按优先级】\n"
            "  1. 优先使用下方「页面 DOM/可交互控件」清单中的 ref ID\n"
            "  2. 若清单不足，使用一次 browser_snapshot 获取最新 ref ID\n"
            "  3. 若 snapshot 仍无法定位，使用 browser_console(expression=\"...\") 执行 JavaScript：\n"
            "     - document.querySelector('#id') 或 document.querySelector('.class') 定位\n"
            "     - element.click() / element.focus() / element.value = 'text' 操作\n"
            "     - 触发 React/Vue 事件：element.dispatchEvent(new Event('input', {bubbles:true}))\n"
            "     - 获取所有匹配：document.querySelectorAll('selector')\n"
            "  禁止连续反复调用 browser_snapshot（全程最多 2 次）\n"
            "DOM 优先，snapshot 兜底，JavaScript 最终手段；未核验勿声称已登录/已搜索。\n"
            "若遇 401/空流：立刻停止并说明原因。\n"
            "若需要验证码/扫码，回复 NEED_USER_ACTION:<原因>。\n\n"
        )
    elif platform in ("desktop",):
        prefix = (
            f"【Testory 平台上下文 platform=desktop — 桌面专用】\n"
            f"{caps_note}"
            f"{desktop_gateway_auth_hint()}\n"
            f"参考技能（需要时最多 skill_view 一次）：{skill_line}\n"
            "Windows 桌面：优先 MCP windows_* / get_screen_*；勿空等 computer_use。\n"
            "每步 observe→act→observe；未核验勿声称已输入/已发送。\n"
            "若遇 401/空流：立刻停止并说明原因。\n"
            "若需要人工步骤，回复 NEED_USER_ACTION:<原因>。\n\n"
        )
    else:
        surface_note = ""
        if any("desktop" in s for s in skill_names) or platform in ("auto", "all", "cross"):
            surface_note += desktop_gateway_auth_hint() + "\n"
        if any("web-browser" in s or s == "testory-web-browser" for s in skill_names) or platform in (
            "auto",
            "all",
            "cross",
        ):
            surface_note = web_browser_cdp_hint() + "\n" + surface_note
        prefix = (
            f"【Testory 平台上下文 platform={platform}】\n"
            f"{caps_note}"
            f"{surface_note}"
            f"按任务类型二选一（勿混用）：网页→仅 browser_*；桌面→仅 windows_*。"
            f"参考技能 {skill_line}：**不要**反复 skill_view。\n"
            "网页已打开目标页则禁止 navigate；禁止 terminal 探 CDP。\n"
            "每步 observe→act→observe。勿另起独立浏览器。\n"
            "若遇 401/空流：立刻停止。需要人工时 NEED_USER_ACTION:<原因>。\n\n"
        )
    body = (message or meta.get("message") or "").strip()
    return ctx_prefix + prefix + vision_note + body
