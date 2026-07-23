# -*- coding: utf-8 -*-
"""Testory brand constants (app / website / installer)."""
from __future__ import annotations

PRODUCT_NAME = "Testory"
PRODUCT_TAGLINE_ZH = "AI 自动化测试平台"
PRODUCT_TAGLINE_EN = "AI Test Automation Platform"
PRODUCT_FULL_NAME_ZH = "Testory · AI 自动化测试"
PRODUCT_WINDOW_TITLE = "Testory"
PRODUCT_COPYRIGHT = "© Testory. All rights reserved."
# 生产官网（桌面端升级订阅 / 支付跳转默认地址）
OFFICIAL_WEBSITE_URL = "http://62.234.135.115/"


def brand_context() -> dict:
    return {
        "product_name": PRODUCT_NAME,
        "product_tagline": PRODUCT_TAGLINE_ZH,
        "product_tagline_en": PRODUCT_TAGLINE_EN,
        "product_full_name": PRODUCT_FULL_NAME_ZH,
        "product_window_title": PRODUCT_WINDOW_TITLE,
        "product_copyright": PRODUCT_COPYRIGHT,
        "official_website_url": OFFICIAL_WEBSITE_URL,
    }
