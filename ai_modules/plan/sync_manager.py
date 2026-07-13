from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .context_bus import CrossEndContext

_POLL_INTERVAL_S = 0.5
_POLL_MAX_S = 30.0
_API_POLL_INTERVAL_S = 1.0
_API_POLL_MAX_S = 30.0


class SyncPointManager:

    def __init__(self, context: CrossEndContext):
        self.context = context
        self._plan_stages: List[Dict[str, Any]] = []

    def wait_for_data_sync(self, required_keys: List[str]) -> bool:
        missing = [k for k in required_keys if self.context.get_variable(k) is None]
        if missing:
            return False
        return True

    def wait_for_ui_state(
        self,
        check_fn: Callable[[], bool],
        max_wait_s: float = 30.0,
        interval_s: float = 0.5,
        label: str = "ui_state",
    ) -> Tuple[bool, float]:
        waited = 0.0
        while waited < max_wait_s:
            if check_fn():
                return True, waited
            time.sleep(interval_s)
            waited += interval_s
        return False, waited

    def wait_for_api_state(
        self,
        poll_fn: Callable[[], Optional[Any]],
        target_value: Any,
        json_path: str = "",
        max_wait_s: float = 30.0,
        interval_s: float = 1.0,
        compare: str = "equals",
    ) -> Tuple[bool, Any, float]:
        waited = 0.0
        last_val = None
        while waited < max_wait_s:
            val = poll_fn()
            last_val = val
            if val is not None:
                if compare == "equals" and val == target_value:
                    return True, val, waited
                if compare == "in" and isinstance(target_value, list) and val in target_value:
                    return True, val, waited
                if compare == "not_null":
                    return True, val, waited
                if compare == "gt" and isinstance(val, (int, float)) and val > target_value:
                    return True, val, waited
            time.sleep(interval_s)
            waited += interval_s
        return False, last_val, waited

    def wait_for_human(self, prompt: str, timeout_s: float = 300.0) -> bool:

        return True

    def acquire(self, stage_id: str, depends_on: List[str]) -> bool:
        if not depends_on:
            return True
        for dep_sync in depends_on:
            found = False
            for sid, sdata in self.context._stage_results.items():
                for ps in self._plan_stages:
                    if isinstance(ps, dict) and ps.get("id") == sid and ps.get("sync_point") == dep_sync:
                        if sdata.get("ok") or sdata.get("ok_assert"):
                            found = True
                            break
                if found:
                    break
            if not found:
                return False
        return True

    def set_plan_stages(self, stages: List[Dict[str, Any]]) -> None:
        self._plan_stages = list(stages or [])
