# -*- coding: utf-8 -*-
"""TestRunState：多 Agent 共享状态（vars / 阶段 / 证据 / 幂等键 / 事件时间线）。"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_CACHE: Dict[str, "TestRunState"] = {}

SCHEMA_VERSION = "1.0"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data_dir() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2] / "data"
    d = root / "agent_team_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(run_id: str) -> str:
    return "".join(c for c in str(run_id) if c.isalnum() or c in ("-", "_"))[:64] or "unknown"


@dataclass
class AgentEvent:
    """派单 / 完成 / 失败等控制面事件。"""

    event_id: str
    agent: str
    kind: str  # dispatch | complete | fail | note
    at: str
    message: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TestRunState:
    """一条联动 TestRun 的共享状态。"""

    __test__ = False  # 避免 pytest 误收集

    run_id: str
    team_id: str = "testory-cross-end-qa-team"
    schema_version: str = SCHEMA_VERSION
    status: str = "created"  # created|planning|executing|verifying|success|failed
    goal: str = ""
    plan: Optional[Dict[str, Any]] = None
    vars: Dict[str, Any] = field(default_factory=dict)
    stage_results: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    report: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    idempotency_key: str = ""
    user_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    # 透传执行摘要（诚实字段，不另造成功）
    execution: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _utc_now()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.idempotency_key:
            self.idempotency_key = f"idem-{self.run_id}"

    def touch(self) -> None:
        self.updated_at = _utc_now()

    def emit(
        self,
        agent: str,
        kind: str,
        message: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ev = AgentEvent(
            event_id=f"ev-{uuid.uuid4().hex[:10]}",
            agent=str(agent or "system"),
            kind=str(kind or "note"),
            at=_utc_now(),
            message=str(message or ""),
            payload=dict(payload or {}),
        )
        d = ev.to_dict()
        self.events.append(d)
        self.touch()
        return d

    def set_status(self, status: str) -> None:
        self.status = str(status or self.status)
        self.touch()
        if self.status in ("success", "failed"):
            self.finished_at = self.updated_at

    def agent_kinds_seen(self) -> List[str]:
        names: List[str] = []
        for e in self.events:
            a = str((e or {}).get("agent") or "")
            if a and a not in names:
                names.append(a)
        return names

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "team_id": self.team_id,
            "schema_version": self.schema_version,
            "status": self.status,
            "goal": self.goal,
            "plan": self.plan,
            "vars": self.vars,
            "stage_results": self.stage_results,
            "evidence": self.evidence,
            "report": self.report,
            "events": self.events,
            "errors": self.errors,
            "idempotency_key": self.idempotency_key,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "execution": self.execution,
            "agents_seen": self.agent_kinds_seen(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TestRunState":
        known = {
            "run_id",
            "team_id",
            "schema_version",
            "status",
            "goal",
            "plan",
            "vars",
            "stage_results",
            "evidence",
            "report",
            "events",
            "errors",
            "idempotency_key",
            "user_id",
            "created_at",
            "updated_at",
            "finished_at",
            "execution",
        }
        kwargs = {k: data[k] for k in known if k in data}
        if "run_id" not in kwargs:
            kwargs["run_id"] = f"run-{uuid.uuid4().hex[:12]}"
        return cls(**kwargs)

    @classmethod
    def create(
        cls,
        *,
        goal: str = "",
        user_id: str = "",
        team_id: str = "testory-cross-end-qa-team",
        idempotency_key: str = "",
        run_id: str = "",
    ) -> "TestRunState":
        rid = (run_id or "").strip() or f"run-{uuid.uuid4().hex[:12]}"
        st = cls(
            run_id=rid,
            team_id=team_id,
            goal=str(goal or ""),
            user_id=str(user_id or ""),
            idempotency_key=str(idempotency_key or f"idem-{rid}"),
        )
        st.emit("system", "note", "TestRunState created")
        return st


def persist_path(run_id: str) -> Path:
    return _data_dir() / f"{_safe_id(run_id)}.json"


def save_run(state: TestRunState) -> Path:
    state.touch()
    path = persist_path(state.run_id)
    with _LOCK:
        _CACHE[state.run_id] = state
        path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return path


def load_run(run_id: str) -> Optional[TestRunState]:
    rid = str(run_id or "").strip()
    if not rid:
        return None
    with _LOCK:
        if rid in _CACHE:
            return _CACHE[rid]
    path = persist_path(rid)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    st = TestRunState.from_dict(data)
    with _LOCK:
        _CACHE[st.run_id] = st
    return st


def list_run_ids(limit: int = 50) -> List[str]:
    d = _data_dir()
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: List[str] = []
    for p in files[: max(1, int(limit))]:
        out.append(p.stem)
    return out
