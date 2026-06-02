# -*- coding: utf-8 -*-
"""Web Playwright 执行封装。"""

from __future__ import annotations

from typing import Any, Dict, List


def run_web_case_steps(steps: List[Dict[str, Any]], automation: Any) -> List[Dict[str, Any]]:
    """批量 Web 步骤由 PlaywrightAutomation 执行（委托现有路径）。"""
    raise NotImplementedError("请使用 app.api_run_case 或 playwright_automation 现有入口")
