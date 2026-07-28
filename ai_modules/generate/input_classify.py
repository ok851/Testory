# -*- coding: utf-8 -*-
"""AI 用例设计：上传内容分类（需求文档 vs 前端源码等）。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

_FRONTEND_EXT = {".tsx", ".jsx", ".vue", ".svelte", ".html", ".htm"}
_FRONTEND_LIKE_EXT = {".ts", ".js", ".css", ".scss"}  # 需内容启发确认
_DOC_EXT = {".txt", ".md", ".markdown", ".pdf", ".docx", ".doc", ".json", ".yaml", ".yml"}

_FRONTEND_MARKERS = (
    "data-testid",
    "aria-label",
    "export function",
    "export default",
    "<template",
    "react.",
    "from 'react'",
    'from "react"',
    "@component",
    "el-button",
    "antd",
    "createelement",
)


def normalize_platform(platform_type: str) -> str:
    p = (platform_type or "web").strip().lower()
    aliases = {
        "mobile": "android",
        "app": "android",
        "ios": "android",  # 步骤层暂统一 android/mobile agent
        "win": "desktop",
        "windows": "desktop",
        "uia": "desktop",
        "erp": "desktop",
        "system": "os",
        "shell": "os",
        "ops": "os",
    }
    return aliases.get(p, p) or "web"


def classify_upload_filename(filename: str) -> str:
    """返回: frontend_source | requirements_doc | unknown"""
    ext = Path((filename or "").strip()).suffix.lower()
    if ext in _FRONTEND_EXT:
        return "frontend_source"
    if ext in _DOC_EXT:
        return "requirements_doc"
    if ext in _FRONTEND_LIKE_EXT:
        return "frontend_candidate"
    return "unknown"


def looks_like_frontend_source(text: str) -> bool:
    t = (text or "")[:8000].lower()
    if not t.strip():
        return False
    hits = sum(1 for m in _FRONTEND_MARKERS if m in t)
    if hits >= 2:
        return True
    if ("<" in t and ">" in t) and any(
        x in t for x in ("data-testid", "onclick", "v-model", "className".lower())
    ):
        return True
    return False


def classify_design_input(
    *,
    filename: str = "",
    text: str = "",
    explicit_kind: str = "",
) -> str:
    """
    综合文件名与正文判断输入类型。
    返回: frontend_source | requirements_doc
    """
    kind = (explicit_kind or "").strip().lower()
    if kind in ("frontend_source", "frontend", "source", "code"):
        return "frontend_source"
    if kind in ("requirements_doc", "requirements", "doc", "document"):
        return "requirements_doc"

    by_name = classify_upload_filename(filename)
    if by_name == "frontend_source":
        return "frontend_source"
    if by_name == "requirements_doc":
        # 若用户把源码粘进 .txt，仍可用内容启发
        if looks_like_frontend_source(text) and len((text or "").strip()) > 40:
            return "frontend_source"
        return "requirements_doc"
    if by_name == "frontend_candidate":
        return "frontend_source" if looks_like_frontend_source(text) else "requirements_doc"
    if looks_like_frontend_source(text):
        return "frontend_source"
    return "requirements_doc"


def entry_field_meta(platform_type: str) -> Dict[str, str]:
    """前端高级选项：按平台给出入口字段语义（URL 仅 Web 场景）。"""
    p = normalize_platform(platform_type)
    table = {
        "web": {
            "key": "base_url",
            "label": "目标 URL（可选）",
            "placeholder": "https://app.example.com/login — 仅 Web 需要",
        },
        "api": {
            "key": "api_base",
            "label": "接口 Base（可选）",
            "placeholder": "https://api.example.com — 相对路径时拼接",
        },
        "android": {
            "key": "app_id",
            "label": "App 包名/入口（可选）",
            "placeholder": "com.example.app 或 Activity 名",
        },
        "desktop": {
            "key": "app_entry",
            "label": "桌面应用入口（可选）",
            "placeholder": "@erp 别名 / 可执行路径 / 窗口标题",
        },
        "os": {
            "key": "os_entry",
            "label": "系统场景入口（可选）",
            "placeholder": "如：服务名、进程名、配置路径",
        },
    }
    return table.get(p, table["web"])


def split_frontend_snippets(
    *,
    filename: str,
    text: str,
) -> Dict[str, str]:
    """单文件上传 → file_snippets 映射。"""
    path = (filename or "").strip() or "uploaded/Component.tsx"
    # 避免奇怪绝对路径
    path = path.replace("\\", "/").lstrip("/")
    if "/" not in path and not path.startswith("src/"):
        path = f"src/{path}"
    return {path: text or ""}
