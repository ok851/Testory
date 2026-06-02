# -*- coding: utf-8 -*-
"""从需求/接口文档生成 API 用例（占位，后续实现）。"""

from __future__ import annotations

from typing import Any, Dict, List


def generate_api_cases_from_document(doc_text: str, project_name: str = "") -> Dict[str, Any]:
    """占位：解析文档文本并生成 API 测试用例结构。"""
    return {
        "success": False,
        "error": "api_from_doc 模块尚未实现，请使用现有接口测试模块手动导入。",
        "cases": [],
        "project_name": project_name,
        "source_length": len(doc_text or ""),
    }
