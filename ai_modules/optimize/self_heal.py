# -*- coding: utf-8 -*-
"""对现有用例做 locator 检测与 AI 修复建议（占位）。"""

from __future__ import annotations

from typing import Any, Dict, List


def analyze_steps_for_self_heal(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """返回步骤健康检查摘要（Web / Android / API 通用规则）。"""
    issues: List[str] = []
    suggestions: List[str] = []
    for idx, step in enumerate(steps or [], start=1):
        if not isinstance(step, dict):
            continue
        act = (step.get("action") or "").strip().lower()
        layer = (step.get("automation_layer") or "web").strip().lower()
        if act == "api_request":
            if not (step.get("api_spec") or step.get("input_value") or "").strip():
                issues.append(f"第{idx}步 API 步骤缺少 api_spec")
            continue
        if layer == "android" and not (step.get("selector_value") or "").strip():
            if act in ("tap", "input_text", "assert_text", "assert_element"):
                issues.append(f"第{idx}步 Android 步骤缺少 selector_value")
        if layer == "web" and act in ("click", "input", "verify", "assert") and not (step.get("selector_value") or "").strip():
            issues.append(f"第{idx}步 Web 步骤缺少 selector_value")
            desc = (step.get("description") or "").strip()
            if desc and act in ("click", "input"):
                try:
                    from hermes_heal_bridge import build_vlm_ground_heal_candidate

                    if build_vlm_ground_heal_candidate(desc):
                        suggestions.append(
                            f"第{idx}步可启用智能画面定位（已支持根据描述「{desc[:40]}」自动找控件）"
                        )
                except Exception:
                    pass
        if act == "navigate" and not (step.get("input_value") or step.get("url") or "").strip():
            issues.append(f"第{idx}步 navigate 缺少 URL")
    if issues:
        suggestions.append("可在「AI 自愈优化」页执行定位器预修复，或在步骤页使用 AI 助手优化单步。")
    return {
        "issues": issues,
        "issue_count": len(issues),
        "healthy": len(issues) == 0,
        "suggestions": suggestions,
    }
