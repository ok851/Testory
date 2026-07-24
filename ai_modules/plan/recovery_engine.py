from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

RECOVERY_RETRY = "retry"
RECOVERY_RECHECK_SYNC = "recheck_sync"
RECOVERY_SKIP = "skip"
RECOVERY_ABORT = "abort"

_DEFAULT_MAX_RETRIES = 2

# on_failure 合法值
_ON_FAILURE_CONTINUE = frozenset({"continue", "skip"})
_ON_FAILURE_RETRY = frozenset({"retry"})


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

        policy = (on_failure or "abort").strip().lower()
        entry: Dict[str, Any] = {
            "stage_id": stage_id,
            "attempt": attempts,
            "error": error_message,
            "on_failure": policy,
        }

        if policy in _ON_FAILURE_CONTINUE:
            action = RECOVERY_SKIP
        elif policy in _ON_FAILURE_RETRY and attempts <= self.max_retries:
            action = RECOVERY_RETRY
        else:
            # retry 耗尽或 abort / 未知策略 → abort
            action = RECOVERY_ABORT
            if policy in _ON_FAILURE_RETRY and attempts > self.max_retries:
                entry["retry_exhausted"] = True

        entry["action"] = action
        self._recovery_log.append(entry)
        return action

    def should_cleanup_run(self, stage: Dict[str, Any]) -> bool:
        if not stage:
            return False
        return bool(stage.get("cleanup"))

    def get_recovery_log(self) -> List[Dict[str, Any]]:
        return list(self._recovery_log)

    def skipped_stage_ids(self) -> List[str]:
        """最终以 skip 收场的阶段（同一 stage 取最后一次决策）。"""
        last: Dict[str, str] = {}
        for row in self._recovery_log:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("stage_id") or "")
            if sid:
                last[sid] = str(row.get("action") or "")
        return [sid for sid, act in last.items() if act == RECOVERY_SKIP]

    def reset_stage(self, stage_id: str) -> None:
        self._attempts.pop(stage_id, None)
