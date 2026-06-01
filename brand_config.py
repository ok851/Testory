# -*- coding: utf-8 -*-
"""Testory 产品品牌常量（应用 / 官网 / 安装包统一）。"""
from __future__ import annotations

PRODUCT_NAME = "Testory"
PRODUCT_TAGLINE_ZH = "AI 自动化测试平台"
PRODUCT_TAGLINE_EN = "AI Test Automation Platform"
PRODUCT_FULL_NAME_ZH = "Testory · AI 自动化测试"
PRODUCT_WINDOW_TITLE = "Testory"
PRODUCT_COPYRIGHT = "© Testory. All rights reserved."


def brand_context() -> dict:
    return {
        "product_name": PRODUCT_NAME,
        "product_tagline": PRODUCT_TAGLINE_ZH,
        "product_tagline_en": PRODUCT_TAGLINE_EN,
        "product_full_name": PRODUCT_FULL_NAME_ZH,
        "product_window_title": PRODUCT_WINDOW_TITLE,
        "product_copyright": PRODUCT_COPYRIGHT,
    }
