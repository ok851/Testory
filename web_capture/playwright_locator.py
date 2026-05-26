# -*- coding: utf-8 -*-
"""Playwright 定位器转换（仅 web_capture 路径使用）。"""

from __future__ import annotations

from typing import Any, Tuple


def convert_selector(selector_value: str, selector_type: str) -> Tuple[str, str]:
    st = (selector_type or "css").strip().lower()
    sv = str(selector_value or "").strip()
    if st == "id":
        return f"#{sv}" if sv and not sv.startswith("#") else sv, "css"
    if st == "class":
        return f".{sv}" if sv and not sv.startswith(".") else sv, "css"
    if st == "data" and sv.startswith("testid="):
        tid = sv.split("=", 1)[1]
        return f'[data-testid="{tid}"]', "css"
    return sv, st
