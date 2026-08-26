# -*- coding: utf-8 -*-
"""对现有用例做 locator 检测与 AI 修复建议。

诚实约束（Y5）：
- Web 可做静态扫描 +（有条件的）运行时自愈管线
- Desktop：静态扫描 + **有限**运行时自愈（标题/别名/UIA 放宽）；禁止对外宣称「Desktop 已自愈」
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def heal_capability_matrix() -> Dict[str, Any]:
    """Self-heal Hub 能力矩阵（诚实声明，非营销完成态）。"""
    return {
        "hub": "self_heal",
        "marketing_claim_allowed": False,
        "disclaimer": (
            "本矩阵为工程能力真相源。Desktop 仅为有限运行时自愈（非通用 UIA）；"
            "不得宣传「跨端/桌面已自愈」。Web 运行时自愈依赖浏览器会话与开关。"
        ),
        "layers": {
            "web": {
                "static_scan": True,
                "runtime_heal": True,
                "status": "supported",
                "note": "定位器预修复 / Hermes·视觉降级（需会话与 AI_HERMES_HEAL_ENABLE）",
            },
            "api": {
                "static_scan": True,
                "runtime_heal": False,
                "status": "scan_only",
                "note": "可扫缺 api_spec；HTTP 失败不走选择器自愈",
            },
            "android": {
                "static_scan": True,
                "runtime_heal": "partial",
                "status": "partial",
                "note": "静态缺 selector 可扫；运行时视觉 healer 有限，勿等同 Web",
            },
            "desktop": {
                "static_scan": True,
                "runtime_heal": "partial",
                "status": "partial",
                "note": (
                    "运行时：attach 标题放宽 / launch 别名重解析 / "
                    "click·input 有限 UIA 放宽（DESKTOP_RUNTIME_HEAL）；"
                    "非通用 UIA 自愈；失败不假绿"
                ),
            },
            "hitl": {
                "static_scan": False,
                "runtime_heal": False,
                "status": "n_a",
                "note": "人机门禁不属于自愈范围",
            },
        },
        "y5": {
            "id": "Y5",
            "title": "Self-heal Hub + Desktop 有限运行时自愈（含有限 UIA）",
            "desktop_runtime_heal": "partial",
            "closed": True,
            "done_definition": (
                "Hub 能力矩阵可查询；Desktop 运行时自愈覆盖 attach 标题放宽、"
                "launch 别名重解析、click/input 有限 UIA 放宽；失败不假绿；"
                "禁止宣传「通用 UIA/Desktop 已自愈」"
            ),
            "progress": "closed_partial_desktop_heal",
        },
    }


def _layer_of(step: Dict[str, Any]) -> str:
    layer = (step.get("automation_layer") or step.get("layer") or "web").strip().lower()
    if layer in ("mobile", "android_app"):
        return "android"
    if layer in ("win", "windows", "uia"):
        return "desktop"
    return layer or "web"


def analyze_steps_for_self_heal(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """返回步骤健康检查摘要（按层诚实标注）。"""
    issues: List[str] = []
    suggestions: List[str] = []
    layer_counts: Dict[str, int] = {}
    desktop_steps = 0

    for idx, step in enumerate(steps or [], start=1):
        if not isinstance(step, dict):
            continue
        act = (step.get("action") or "").strip().lower()
        layer = _layer_of(step)
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

        if act == "api_request" or layer == "api":
            if not (step.get("api_spec") or step.get("input_value") or "").strip():
                issues.append(f"第{idx}步 API 步骤缺少 api_spec")
            continue

        if layer == "desktop":
            desktop_steps += 1
            if act == "launch_app":
                spec = step.get("desktop_spec") if isinstance(step.get("desktop_spec"), dict) else {}
                path = (
                    (step.get("input_value") or "").strip()
                    or (spec.get("path") or spec.get("exe") or "").strip()
                    or (spec.get("alias") or "").strip()
                )
                if not path:
                    issues.append(f"第{idx}步 Desktop launch_app 缺少 path/别名")
            elif act == "attach_window":
                spec = step.get("desktop_spec") if isinstance(step.get("desktop_spec"), dict) else {}
                if not (
                    spec.get("window_title_re")
                    or spec.get("window_title")
                    or spec.get("title_re")
                    or (step.get("input_value") or "").strip()
                ):
                    issues.append(f"第{idx}步 Desktop attach_window 缺少窗口标题条件")
            continue

        if layer == "android" and not (step.get("selector_value") or "").strip():
            if act in ("tap", "input_text", "assert_text", "assert_element", "click", "input"):
                issues.append(f"第{idx}步 Android 步骤缺少 selector_value")
        if layer == "web" and act in ("click", "input", "verify", "assert") and not (
            step.get("selector_value") or ""
        ).strip():
            issues.append(f"第{idx}步 Web 步骤缺少 selector_value")
            desc = (step.get("description") or "").strip()
            if desc and act in ("click", "input"):
                try:
                    from modules.hermes.hermes_heal_bridge import build_vlm_ground_heal_candidate

                    if build_vlm_ground_heal_candidate(desc):
                        suggestions.append(
                            f"第{idx}步可启用智能画面定位（已支持根据描述「{desc[:40]}」自动找控件）"
                        )
                except Exception:
                    pass
        if act == "navigate" and not (step.get("input_value") or step.get("url") or "").strip():
            issues.append(f"第{idx}步 navigate 缺少 URL")

    if desktop_steps:
        suggestions.append(
            f"本用例含 {desktop_steps} 个 Desktop 步骤：静态扫描 + 有限运行时自愈"
            "（标题放宽/别名重解析/有限 UIA 放宽，DESKTOP_RUNTIME_HEAL）；"
            "非通用 UIA 自愈，失败须诚实失败（禁止宣传 Desktop 已自愈）。"
        )
    if issues:
        suggestions.append(
            "可在「AI 自愈优化」页执行定位器预修复（Web），或在步骤页使用 AI 助手优化单步。"
        )

    caps = heal_capability_matrix()
    return {
        "issues": issues,
        "issue_count": len(issues),
        "healthy": len(issues) == 0,
        "suggestions": suggestions,
        "layer_counts": layer_counts,
        "desktop_steps": desktop_steps,
        "desktop_runtime_heal": "partial",
        "capabilities": caps["layers"],
        "disclaimer": caps["disclaimer"],
        "marketing_claim_allowed": False,
    }


def batch_scan_project(
    project_id: str,
    max_cases: int = 50,
) -> Dict[str, Any]:
    caps = heal_capability_matrix()
    results: Dict[str, Any] = {
        "project_id": project_id,
        "total_cases": 0,
        "healthy_cases": 0,
        "issues": [],
        "desktop_case_count": 0,
        "layer_totals": {},
        "capabilities": caps["layers"],
        "disclaimer": caps["disclaimer"],
        "marketing_claim_allowed": False,
        "desktop_runtime_heal": "partial",
    }
    try:
        from db_helper import query_fetchall

        rows = query_fetchall(
            "SELECT id, steps_json FROM test_cases WHERE project_id = %s ORDER BY id LIMIT %s",
            (project_id, max_cases),
        )
    except Exception:
        rows = []
    if not rows:
        return results
    for row in rows:
        case_id = row[0]
        steps_raw = row[1] if len(row) > 1 else None
        try:
            import json

            steps = json.loads(steps_raw) if isinstance(steps_raw, str) else (steps_raw or [])
        except Exception:
            steps = []
        case_health = analyze_steps_for_self_heal(steps)
        results["total_cases"] += 1
        if case_health.get("desktop_steps"):
            results["desktop_case_count"] += 1
        for layer, n in (case_health.get("layer_counts") or {}).items():
            results["layer_totals"][layer] = results["layer_totals"].get(layer, 0) + int(n)
        if case_health["healthy"]:
            results["healthy_cases"] += 1
        else:
            for issue in case_health["issues"]:
                results["issues"].append({"case_id": case_id, "issue": issue})

    total = results["total_cases"]
    results["health_ratio"] = round(results["healthy_cases"] / max(1, total), 2)
    return results


def summarize_heal_claim(*, layer: Optional[str] = None) -> Dict[str, Any]:
    """供报告/UI：某层是否允许「已自愈」话术。"""
    caps = heal_capability_matrix()
    ly = (layer or "").strip().lower() or None
    if ly == "desktop":
        return {
            "allowed": False,
            "reason": "DESKTOP_HEAL_PARTIAL_ONLY",
            "message": (
                "Desktop 仅有限运行时自愈（标题放宽/别名重解析/有限 UIA），"
                "不得宣传「Desktop 已自愈」或通用 UIA 自愈完成"
            ),
            "capabilities": caps["layers"]["desktop"],
        }
    if ly and ly in caps["layers"]:
        entry = caps["layers"][ly]
        runtime = entry.get("runtime_heal")
        return {
            "allowed": bool(runtime is True),
            "reason": "ok" if runtime is True else "RUNTIME_HEAL_LIMITED",
            "message": entry.get("note") or "",
            "capabilities": entry,
        }
    return {
        "allowed": False,
        "reason": "UNSPECIFIED_LAYER",
        "message": caps["disclaimer"],
        "capabilities": caps["layers"],
    }
