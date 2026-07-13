from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

RECOVERY_RETRY = "retry"
RECOVERY_RECHECK_SYNC = "recheck_sync"
RECOVERY_SKIP = "skip"
RECOVERY_ABORT = "abort"

_DEFAULT_MAX_RETRIES = 2


class RecoveryEngine:

    def __init__(self, max_retries: int = _DEFAULT_MAX_RETRIES):
        self.max_retries = max_retries
        self._attempts: Dict[str, int] = {}
        self._recovery_log: List[Dict[str, Any]] = []

    def decide(
        self,
        stage_id: str,
        error_message: str,
        on_failure: str = "abort",
    ) -> str:
        attempts = self._attempts.get(stage_id, 0) + 1
        self._attempts[stage_id] = attempts

        self._recovery_log.append({
            "stage_id": stage_id,
            "attempt": attempts,
            "error": error_message,
            "on_failure": on_failure,
        })

        if on_failure == "continue":
            return RECOVERY_SKIP
        if on_failure == "retry" and attempts <= self.max_retries:
            return RECOVERY_RETRY
        return RECOVERY_ABORT

    def should_cleanup_run(self, stage: Dict[str, Any]) -> bool:
        if not stage:
            return False
        return bool(stage.get("cleanup"))

    def get_recovery_log(self) -> List[Dict[str, Any]]:
        return list(self._recovery_log)

    def reset_stage(self, stage_id: str) -> None:
        self._attempts.pop(stage_id, None)
