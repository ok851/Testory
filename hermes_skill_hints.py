# -*- coding: utf-8 -*-
"""Hermes explore 时按平台预加载 bundled skill 提示。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

BUNDLED_SKILL_BY_PLATFORM: Dict[str, str] = {
    "web": "testory-web-browser",
    "mobile": "testory-android-mobile",
    "desktop": "testory-windows-desktop",
    "ui": "testory-ui-design",
    "cross": "testory-cross-end",
    "explore": "testory-ai-explore",
    "dialog": "testory-ai-dialog",
}

DEFAULT_WEB_SKILLS = ["testory-web-browser", "testory-ai-explore"]
DEFAULT_MOBILE_SKILLS = ["testory-android-mobile", "testory-ai-dialog"]
DEFAULT_DESKTOP_SKILLS = ["testory-windows-desktop", "testory-ai-explore"]
DEFAULT_UI_SKILLS = ["testory-ui-design", "testory-web-browser"]
DEFAULT_CROSS_SKILLS = ["testory-cross-end"]


def skills_for_platform(platform: str) -> List[str]:
    p = (platform or "web").strip().lower()
    if p == "mobile":
        return list(DEFAULT_MOBILE_SKILLS)
    if p == "desktop":
        return list(DEFAULT_DESKTOP_SKILLS)
    if p == "ui":
        return list(DEFAULT_UI_SKILLS)
    if p == "cross":
        return list(DEFAULT_CROSS_SKILLS)
    if p == "explore":
        return list(DEFAULT_WEB_SKILLS)
    if p == "dialog":
        return list(DEFAULT_MOBILE_SKILLS)
    return list(DEFAULT_WEB_SKILLS)


def build_explore_instruction(message: str, meta: Optional[Dict[str, Any]] = None) -> str:
    """为 Hermes explore 构建带 skill 加载提示的 instruction。"""
    meta = meta if isinstance(meta, dict) else {}
    platform = (meta.get("platform") or "web").strip().lower()
    skills = meta.get("skills")
    if not isinstance(skills, list) or not skills:
        skills = skills_for_platform(platform)
    skill_names = [str(s).strip() for s in skills if str(s).strip()]
    skill_line = "、".join(f"`{n}`" for n in skill_names)
    prefix = (
        f"【Testory 平台上下文 platform={platform}】\n"
        f"请先用 skill_view 加载以下技能：{skill_line}。\n"
        "遵循技能中的 CDP attach / gateway / bridge 铁律，勿使用与平台冲突的独立浏览器或外部 ClawHub 依赖。\n\n"
    )
    body = (message or meta.get("message") or "").strip()
    return prefix + body
