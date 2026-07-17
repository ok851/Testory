"""AI 自主探索测试引擎：路径规划 + 可配置深度/预算/范围。"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class ExplorationBudget:

    def __init__(
        self,
        max_depth: int = 5,
        max_steps: int = 20,
        max_duration_s: float = 120.0,
        scope_urls: Optional[List[str]] = None,
    ):
        self.max_depth = max_depth
        self.max_steps = max_steps
        self.max_duration_s = max_duration_s
        self.scope_urls: Set[str] = set(scope_urls or [])
        self.steps_taken = 0
        self.current_depth = 0
        self.visited: Set[str] = set()
        self._start_ts: float = time.monotonic()
        self.timed_out: bool = False

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self._start_ts

    def can_continue(self) -> bool:
        if self.steps_taken >= self.max_steps:
            return False
        if self.current_depth > self.max_depth:
            return False
        if self.max_duration_s > 0 and self.elapsed_s >= self.max_duration_s:
            self.timed_out = True
            return False
        return True

    def record_step(self, identifier: str = "") -> None:
        self.steps_taken += 1
        if identifier:
            self.visited.add(identifier)

    def is_in_scope(self, url: str) -> bool:
        if not self.scope_urls:
            return True
        for prefix in self.scope_urls:
            if url.startswith(prefix):
                return True
        return False

    @property
    def progress_ratio(self) -> float:
        ratios: List[float] = []
        if self.max_steps > 0:
            ratios.append(self.steps_taken / self.max_steps)
        if self.max_duration_s > 0:
            ratios.append(self.elapsed_s / self.max_duration_s)
        return min(1.0, max(ratios)) if ratios else 0.0


class ExplorationStrategy:

    RANDOM = "random"
    GREEDY = "greedy"
    MODEL_DRIVEN = "model_driven"

    def __init__(self, mode: str = GREEDY):
        self.mode = mode

    def select_next(
        self,
        candidates: List[Dict[str, Any]],
        visited: Set[str],
    ) -> Optional[Dict[str, Any]]:

        unvisited = []
        for c in candidates:
            ident = c.get("identifier") or c.get("selector") or c.get("text", "")
            if ident not in visited:
                unvisited.append(c)

        if not unvisited:
            return None

        if self.mode == self.RANDOM:
            return random.choice(unvisited)

        priority_candidates = [c for c in unvisited if c.get("priority", 0) > 0]
        if priority_candidates:
            priority_candidates.sort(key=lambda x: x.get("priority", 0), reverse=True)
            return priority_candidates[0]

        return unvisited[0]


class ExplorationContext:

    def __init__(self):
        self.actions_taken: List[Dict[str, Any]] = []
        self.screenshots: List[str] = []
        self.errors: List[Dict[str, Any]] = []

    def record_action(self, action: Dict[str, Any]) -> None:
        self.actions_taken.append(action)

    def record_screenshot(self, path: str) -> None:
        self.screenshots.append(path)

    def record_error(self, error: Dict[str, Any]) -> None:
        self.errors.append(error)
