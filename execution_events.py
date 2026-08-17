# -*- coding: utf-8 -*-
"""Standard execution event types and helper structures.

Centralizes the minimum auditable event set for timeline, replay, and debugging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time


ROUTE_DECIDED = "route_decided"
TOOL_REGISTERED = "tool_registered"
TOOL_CALL_START = "tool_call_start"
TOOL_CALL_END = "tool_call_end"
OBSERVATION_START = "observation_start"
OBSERVATION_END = "observation_end"
ASSERTION_START = "assertion_start"
ASSERTION_END = "assertion_end"
HEAL_ATTEMPT = "heal_attempt"
RISK_DECISION = "risk_decision"
DONE = "done"

STANDARD_EVENT_TYPES = (
    ROUTE_DECIDED,
    TOOL_REGISTERED,
    TOOL_CALL_START,
    TOOL_CALL_END,
    OBSERVATION_START,
    OBSERVATION_END,
    ASSERTION_START,
    ASSERTION_END,
    HEAL_ATTEMPT,
    RISK_DECISION,
    DONE,
)


@dataclass
class ExecutionEvent:
    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0

    def __post_init__(self) -> None:
        if self.ts <= 0:
            self.ts = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "ts": self.ts,
            "data": dict(self.data),
        }


@dataclass
class ExecutionEventCollector:
    events: List[ExecutionEvent] = field(default_factory=list)

    def emit(self, event_type: str, **data: Any) -> ExecutionEvent:
        event = ExecutionEvent(event_type=event_type, data=data)
        self.events.append(event)
        return event

    def as_dicts(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.events]

    def find_by_type(self, event_type: str) -> List[ExecutionEvent]:
        return [e for e in self.events if e.event_type == event_type]
