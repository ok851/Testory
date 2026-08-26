# -*- coding: utf-8 -*-
"""DevOps review gate integration.

Provides review gate functionality for code-change driven test updates
to ensure review-before-trigger in the DevOps pipeline.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


# Review gate states
REVIEW_STATE_PENDING = "pending_review"
REVIEW_STATE_APPROVED = "approved"
REVIEW_STATE_REJECTED = "rejected"
REVIEW_STATE_APPLIED = "applied"
REVIEW_STATE_IGNORED = "ignored"


def create_review_gate(
    *,
    change_type: str,
    change_id: str,
    description: str,
    impact_summary: Optional[Dict[str, Any]] = None,
    recommended_cases: Optional[List[int]] = None,
    heal_proposals: Optional[List[Dict[str, Any]]] = None,
    auto_approve: bool = False,
) -> Dict[str, Any]:
    """Create a review gate for code-change driven updates."""
    gate = {
        "change_type": change_type,
        "change_id": change_id,
        "description": description,
        "state": REVIEW_STATE_APPROVED if auto_approve else REVIEW_STATE_PENDING,
        "created_at": time.time(),
        "updated_at": time.time(),
        "impact_summary": impact_summary or {},
        "recommended_cases": recommended_cases or [],
        "heal_proposals": heal_proposals or [],
        "auto_approve": auto_approve,
        "review_history": [],
    }
    
    if auto_approve:
        gate["review_history"].append({
            "action": "auto_approve",
            "timestamp": time.time(),
            "reason": "低风险变更，自动批准",
        })
    
    return gate


def approve_review_gate(
    gate: Dict[str, Any],
    *,
    reviewer: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Approve a review gate."""
    gate["state"] = REVIEW_STATE_APPROVED
    gate["updated_at"] = time.time()
    gate["review_history"].append({
        "action": "approve",
        "reviewer": reviewer,
        "reason": reason,
        "timestamp": time.time(),
    })
    return gate


def reject_review_gate(
    gate: Dict[str, Any],
    *,
    reviewer: str,
    reason: str,
) -> Dict[str, Any]:
    """Reject a review gate."""
    gate["state"] = REVIEW_STATE_REJECTED
    gate["updated_at"] = time.time()
    gate["review_history"].append({
        "action": "reject",
        "reviewer": reviewer,
        "reason": reason,
        "timestamp": time.time(),
    })
    return gate


def apply_review_gate(
    gate: Dict[str, Any],
    *,
    applied_by: str,
    applied_cases: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Mark review gate as applied."""
    gate["state"] = REVIEW_STATE_APPLIED
    gate["updated_at"] = time.time()
    gate["applied_at"] = time.time()
    gate["applied_by"] = applied_by
    gate["applied_cases"] = applied_cases or gate.get("recommended_cases", [])
    gate["review_history"].append({
        "action": "apply",
        "applied_by": applied_by,
        "applied_cases": gate["applied_cases"],
        "timestamp": time.time(),
    })
    return gate


def ignore_review_gate(
    gate: Dict[str, Any],
    *,
    ignored_by: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Ignore a review gate."""
    gate["state"] = REVIEW_STATE_IGNORED
    gate["updated_at"] = time.time()
    gate["review_history"].append({
        "action": "ignore",
        "ignored_by": ignored_by,
        "reason": reason,
        "timestamp": time.time(),
    })
    return gate


def get_review_gate_summary(gate: Dict[str, Any]) -> Dict[str, Any]:
    """Get summary of review gate state."""
    return {
        "change_type": gate.get("change_type"),
        "change_id": gate.get("change_id"),
        "state": gate.get("state"),
        "description": gate.get("description"),
        "recommended_cases_count": len(gate.get("recommended_cases", [])),
        "heal_proposals_count": len(gate.get("heal_proposals", [])),
        "auto_approve": gate.get("auto_approve", False),
        "created_at": gate.get("created_at"),
        "updated_at": gate.get("updated_at"),
    }


def emit_review_gate_event(
    collector: Any,
    gate: Dict[str, Any],
    *,
    event_type: str = "review_gate_created",
) -> None:
    """Emit review gate event for audit trail."""
    try:
        collector.emit(
            event_type,
            change_type=gate.get("change_type"),
            change_id=gate.get("change_id"),
            state=gate.get("state"),
            description=gate.get("description"),
            recommended_cases_count=len(gate.get("recommended_cases", [])),
            heal_proposals_count=len(gate.get("heal_proposals", [])),
        )
    except Exception:
        pass
