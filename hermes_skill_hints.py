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
        from agent_capability_registry import skills_for_available_caps

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
        return [s for s in available if s.startswith("testory-")][:6] or base
    except Exception:
        return base


def desktop_gateway_auth_hint() -> str:
    """注入给 Hermes 的桌面执行边界（启动智能体时自动挂 MCP + :8766）。"""
    try:
        from desktop_service_bootstrap import resolve_desktop_gateway_secret

        resolve_desktop_gateway_secret(persist_to_hermes=True)
    except Exception:
        try:
            from desktop_service_bootstrap import _ensure_desktop_env_defaults

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


def build_explore_instruction(message: str, meta: Optional[Dict[str, Any]] = None) -> str:
    """为 Hermes explore 构建带 skill 加载提示的 instruction。"""
    meta = meta if isinstance(meta, dict) else {}
    platform = (meta.get("platform") or "auto").strip().lower()
    skills = meta.get("skills")
    if not isinstance(skills, list) or not skills:
        skills = skills_from_registry(platform)
    skill_names = [str(s).strip() for s in skills if str(s).strip()]
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
    desktop_note = ""
    if platform in ("auto", "all", "desktop", "cross") or any(
        "desktop" in s for s in skill_names
    ):
        desktop_note = desktop_gateway_auth_hint() + "\n"
    prefix = (
        f"【Testory 平台上下文 platform={platform}】\n"
        f"{caps_note}"
        f"{desktop_note}"
        f"请先用 skill_view 加载：{skill_line}（桌面任务优先本列表中的 testory-windows-desktop）。\n"
        "你是跨层执行代理（手+眼）。Windows 桌面：优先调用已注册的 MCP windows_* / get_screen_*；"
        "勿空等 computer_use（Windows 上通常不可用）。\n"
        "优先结构化感知（DOM/UIA）；共享屏幕视觉用于确认与弱控件降级。\n"
        "每步 observe→act→observe；未核验勿声称已输入/已发送。\n"
        "Web 遇 OS 弹窗 → MCP 桌面工具或 testory-windows-desktop；需校验数据 → testory-api-http。\n"
        "高风险写操作前先 inspect/只读。勿使用与平台冲突的独立浏览器或外部 ClawHub 依赖。\n"
        "若遇到 401/鉴权失败或空流：禁止重复调用；立刻停止并说明原因。\n"
        "若需要验证码/扫码登录等人工步骤，在回复中明确写出 NEED_USER_ACTION:<原因>。\n\n"
    )
    body = (message or meta.get("message") or "").strip()
    return ctx_prefix + prefix + vision_note + body
