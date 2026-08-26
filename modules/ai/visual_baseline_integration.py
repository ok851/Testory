# -*- coding: utf-8 -*-
"""Visual baseline benchmark integration with agent execution.

Integrates visual baseline benchmark results into the agent execution loop
to inform desktop/heal strategy decisions.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional


# Default benchmark thresholds
_THRESHOLDS = {
    "uia_min_rate": 0.7,
    "ocr_min_rate": 0.6,
    "vision_min_rate": 0.5,
    "overall_min_rate": 0.6,
}


def load_baseline_report(path: str) -> Optional[Dict[str, Any]]:
    """Load baseline report from JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_visual_capability_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    """Extract visual capability summary from baseline report."""
    rates = report.get("rates", {})
    summary = report.get("summary", {})
    
    return {
        "uia_rate": rates.get("uia", 0.0),
        "ocr_rate": rates.get("ocr", 0.0),
        "vision_rate": rates.get("vision", 0.0),
        "total_cases": sum(s.get("total", 0) for s in summary.values()) // max(len(summary), 1),
        "uia_ok": summary.get("uia", {}).get("ok", 0),
        "ocr_ok": summary.get("ocr", {}).get("ok", 0),
        "vision_ok": summary.get("vision", {}).get("ok", 0),
    }


def evaluate_visual_capability(report: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate visual capability based on baseline report."""
    summary = get_visual_capability_summary(report)
    
    uia_rate = summary["uia_rate"]
    ocr_rate = summary["ocr_rate"]
    vision_rate = summary["vision_rate"]
    
    # Determine capability levels
    capabilities = {
        "uia_available": uia_rate >= _THRESHOLDS["uia_min_rate"],
        "ocr_available": ocr_rate >= _THRESHOLDS["ocr_min_rate"],
        "vision_available": vision_rate >= _THRESHOLDS["vision_min_rate"],
        "overall_healthy": (uia_rate + ocr_rate + vision_rate) / 3 >= _THRESHOLDS["overall_min_rate"],
    }
    
    # Determine recommended strategy
    if capabilities["uia_available"]:
        recommended_strategy = "uia"
    elif capabilities["ocr_available"]:
        recommended_strategy = "ocr"
    elif capabilities["vision_available"]:
        recommended_strategy = "vision"
    else:
        recommended_strategy = "manual"
    
    capabilities["recommended_strategy"] = recommended_strategy
    capabilities["rates"] = {
        "uia": uia_rate,
        "ocr": ocr_rate,
        "vision": vision_rate,
    }
    
    return capabilities


def get_desktop_heal_recommendation(capabilities: Dict[str, Any]) -> Dict[str, Any]:
    """Get desktop heal recommendation based on visual capabilities."""
    recommended = capabilities.get("recommended_strategy", "manual")
    
    if recommended == "uia":
        return {
            "allow_heal": True,
            "strategy": "uia_first",
            "fallback": "ocr",
            "confidence": "high",
            "message": "UIA 定位稳定，可执行桌面自愈",
        }
    elif recommended == "ocr":
        return {
            "allow_heal": True,
            "strategy": "ocr_first",
            "fallback": "vision",
            "confidence": "medium",
            "message": "OCR 定位可用，可执行有限桌面自愈",
        }
    elif recommended == "vision":
        return {
            "allow_heal": False,
            "strategy": "vision_only",
            "fallback": "manual",
            "confidence": "low",
            "message": "视觉定位不稳定，建议人工确认后执行",
        }
    else:
        return {
            "allow_heal": False,
            "strategy": "manual",
            "fallback": "none",
            "confidence": "none",
            "message": "视觉定位能力不足，不建议自动自愈",
        }


def emit_visual_capability_event(
    collector: Any,
    report_path: str,
) -> Optional[Dict[str, Any]]:
    """Emit visual capability assessment event."""
    report = load_baseline_report(report_path)
    if not report:
        return None
    
    capabilities = evaluate_visual_capability(report)
    recommendation = get_desktop_heal_recommendation(capabilities)
    
    # Emit to collector
    try:
        collector.emit(
            "visual_capability_assessment",
            capabilities=capabilities,
            recommendation=recommendation,
            report_path=report_path,
        )
    except Exception:
        pass
    
    return {
        "capabilities": capabilities,
        "recommendation": recommendation,
    }
