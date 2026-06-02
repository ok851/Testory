# -*- coding: utf-8 -*-
"""对现有用例做 locator 检测与 AI 修复建议（占位）。"""

from __future__ import annotations

from typing import Any, Dict, List


def analyze_steps_for_self_heal(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """占位：返回步骤健康检查摘要。"""
    issues: List[str] = []
    for idx, step in enumerate(steps or [], start=1):
        if not isinstance(step, dict):
            continue
        layer = (step.get("automation_layer") or "web").strip().lower()
        if layer == "android" and not (step.get("selector_value") or "").strip():
            act = (step.get("action") or "").strip().lower()
            if act in ("tap", "input_text", "assert_text", "assert_element"):
                issues.append(f"第{idx}步 Android 步骤缺少 selector_value")
    return {"issues": issues, "issue_count": len(issues)}
