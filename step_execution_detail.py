# -*- coding: utf-8 -*-
"""Structured step execution result for enterprise-grade run history."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StepExecutionDetail:
    """单步骤执行的完整结构化结果。

    由执行引擎（playwright / desktop / mobile）填充，由 app.py 写入 step_results。
    """

    # ── 基础信息 ──
    step_id: int = 0
    step_order: int = 0
    action: str = ""
    selector_value: str = ""
    input_value: str = ""
    description: str = ""
    status: str = "success"
    error: str = ""
    screenshot: str = ""
    duration: float = 0.0

    # ── 时间维度 ──
    started_at: float = 0.0
    selector_resolve_ms: float = 0.0
    action_execute_ms: float = 0.0
    wait_ms: float = 0.0
    retry_count: int = 0

    # ── 选择器维度 ──
    selector_strategy: str = ""
    selector_attempts: int = 1

    # ── 页面上下文 ──
    page_url_before: str = ""
    page_url_after: str = ""
    page_title: str = ""
    iframe_context: str = ""

    # ── 提取/断言维度 ──
    extracted_value: str = ""
    expected_value: str = ""
    compare_result: str = ""

    # ── 诊断维度 ──
    screenshot_before: str = ""
    console_errors: str = ""

    def mark_started(self) -> None:
        """记录步骤开始时间。"""
        self.started_at = time.time()

    def mark_finished(self, *, success: bool, error: str = "") -> None:
        """记录步骤结束并计算总耗时。"""
        self.status = "success" if success else "error"
        if error:
            self.error = error
        if self.started_at > 0:
            self.duration = round((time.time() - self.started_at) * 1000) / 1000

    def to_db_kwargs(self) -> dict:
        """转为 create_step_result_v2 的关键字参数。"""
        from time_utils import utc_now_sqlite_str

        return {
            "step_id": self.step_id,
            "step_order": self.step_order,
            "action": self.action,
            "selector_value": self.selector_value,
            "input_value": self.input_value,
            "description": self.description,
            "status": self.status,
            "error": self.error,
            "screenshot": self.screenshot,
            "duration": self.duration,
            "started_at": utc_now_sqlite_str() if self.started_at > 0 else "",
            "selector_strategy": self.selector_strategy,
            "selector_attempts": self.selector_attempts,
            "selector_resolve_ms": round(self.selector_resolve_ms, 1),
            "action_execute_ms": round(self.action_execute_ms, 1),
            "wait_ms": round(self.wait_ms, 1),
            "retry_count": self.retry_count,
            "page_url_before": self.page_url_before,
            "page_url_after": self.page_url_after,
            "page_title": self.page_title,
            "iframe_context": self.iframe_context,
            "extracted_value": self.extracted_value,
            "expected_value": self.expected_value,
            "compare_result": self.compare_result,
            "screenshot_before": self.screenshot_before,
            "console_errors": self.console_errors,
        }

    # ── 截图策略：仅特定 action 或失败时需要截图 ──

    SCREENSHOT_ACTIONS = frozenset({
        "extract_text", "text_compare", "assert", "verify", "screenshot",
    })

    def should_capture_before(self) -> bool:
        """步骤执行前是否需要截图。"""
        return self.action in self.SCREENSHOT_ACTIONS

    def should_capture_after(self) -> bool:
        """步骤执行后是否需要截图（成功时）。"""
        return self.action == "screenshot"

    def should_capture_on_failure(self) -> bool:
        """失败时是否需要截图。"""
        return self.status == "error"
