# -*- coding: utf-8 -*-
"""Self-heal audit integration with execution events.

Integrates self-heal attempts into the execution audit trail
for traceability and accountability.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def emit_heal_attempt(
    collector: Any,
    *,
    platform: str,
    strategy: str,
    original_selector: str,
    healed_selector: Optional[str] = None,
    success: bool = False,
    requires_confirmation: bool = True,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Emit heal attempt event for audit trail."""
    event_data = {
        "platform": platform,
        "strategy": strategy,
        "original_selector": original_selector,
        "healed_selector": healed_selector,
        "success": success,
        "requires_confirmation": requires_confirmation,
        "timestamp": time.time(),
    }
    
    if error:
        event_data["error"] = error
    
    try:
        collector.emit("heal_attempt", **event_data)
    except Exception:
        pass
    
    return event_data


def emit_heal_decision(
    collector: Any,
    *,
    platform: str,
    strategy: str,
    allowed: bool,
    reason: str,
    confidence: str = "medium",
) -> Dict[str, Any]:
    """Emit heal decision event for audit trail."""
    event_data = {
        "platform": platform,
        "strategy": strategy,
        "allowed": allowed,
        "reason": reason,
        "confidence": confidence,
        "timestamp": time.time(),
    }
    
    try:
        collector.emit("heal_decision", **event_data)
    except Exception:
        pass
    
    return event_data


def emit_heal_result(
    collector: Any,
    *,
    platform: str,
    strategy: str,
    original_selector: str,
    healed_selector: str,
    success: bool,
    verified: bool = False,
    requires_review: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Emit heal result event for audit trail."""
    event_data = {
        "platform": platform,
        "strategy": strategy,
        "original_selector": original_selector,
        "healed_selector": healed_selector,
        "success": success,
        "verified": verified,
        "requires_review": requires_review,
        "timestamp": time.time(),
    }
    
    if error:
        event_data["error"] = error
    
    try:
        collector.emit("heal_result", **event_data)
    except Exception:
        pass
    
    return event_data


def build_heal_audit_summary(
    collector: Any,
) -> Dict[str, Any]:
    """Build summary of heal attempts from audit trail."""
    heal_events = collector.find_by_type("heal_attempt")
    heal_results = collector.find_by_type("heal_result")
    
    total_attempts = len(heal_events)
    # Count successful from both heal_attempt and heal_result events
    successful_attempts = sum(1 for e in heal_events if e.data.get("success"))
    successful_results = sum(1 for e in heal_results if e.data.get("success"))
    successful = max(successful_attempts, successful_results)
    failed = total_attempts - successful
    
    strategies_used = set()
    platforms_used = set()
    
    for event in heal_events:
        strategies_used.add(event.data.get("strategy", "unknown"))
        platforms_used.add(event.data.get("platform", "unknown"))
    
    return {
        "total_attempts": total_attempts,
        "successful": successful,
        "failed": failed,
        "success_rate": successful / max(total_attempts, 1),
        "strategies_used": list(strategies_used),
        "platforms_used": list(platforms_used),
    }

