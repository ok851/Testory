# -*- coding: utf-8 -*-
"""RiskGuard：L0 / L1 / L2 风险分级与 L2 审批令牌。

诚实约束：
- L2 无有效审批令牌 → 拒绝执行（不得静默放行）
- 已拒绝的审批 → 拒绝执行
- 事件可写入 Trace / 审计，供复盘
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_LOCK = threading.RLock()
_APPROVALS: Dict[str, Dict[str, Any]] = {}
_EVENTS: List[Dict[str, Any]] = []
_MAX_EVENTS = 2000

LEVELS = ("L0", "L1", "L2")

# 显式高风险关键词（动作 / skill / label）；cleanup  alone 不自动升 L2（兼容旧计划）
_L2_MARKERS = (
    "clear_data",
    "delete",
    "drop_",
    "uninstall",
    "install_apk",
    "factory_reset",
    "wipe",
    "production_write",
    "write_prod",
    "purge",
    "truncate",
    "rm -rf",
    "format_disk",
)

_L0_MARKERS = (
    "screenshot",
    "inspect",
    "probe",
    "get_text",
    "dump",
    "read_only",
    "capability",
    "health",
)


@dataclass
class RiskDecision:
    ok: bool
    level: str
    decision: str  # allow | require_approval | denied
    error_code: Optional[str] = None
    error: Optional[str] = None
    approval_id: Optional[str] = None
    reason: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def _norm_level(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().upper()
    if s in LEVELS:
        return s
    if s in ("0", "LOW", "READONLY", "READ_ONLY"):
        return "L0"
    if s in ("1", "NORMAL", "LOW_RISK"):
        return "L1"
    if s in ("2", "HIGH", "HIGH_RISK", "CRITICAL"):
        return "L2"
    return None


def _text_blob(stage: Dict[str, Any]) -> str:
    parts: List[str] = []
    for k in ("id", "label", "skill", "action", "risk_action", "layer"):
        v = stage.get(k)
        if v:
            parts.append(str(v))
    if stage.get("cleanup"):
        parts.append("cleanup")
    req = stage.get("request")
    if isinstance(req, dict):
        parts.append(str(req.get("method") or ""))
        parts.append(str(req.get("url") or ""))
    for step in stage.get("actions") or stage.get("steps") or []:
        if isinstance(step, dict):
            parts.append(str(step.get("type") or step.get("action") or ""))
            parts.append(str(step.get("url") or ""))
    return " ".join(parts).lower()


def classify_stage(stage: Optional[Dict[str, Any]]) -> str:
    """返回 L0 / L1 / L2。显式 risk_level 优先。"""
    if not isinstance(stage, dict):
        return "L1"
    explicit = _norm_level(stage.get("risk_level") or stage.get("risk"))
    if explicit:
        return explicit
    # cleanup 默认不升 L2；需显式 risk_level=L2 或破坏性关键词
    blob = _text_blob(stage)
    for m in _L2_MARKERS:
        if m in blob:
            return "L2"
    layer = str(stage.get("layer") or "").strip().lower()
    req = stage.get("request") if isinstance(stage.get("request"), dict) else {}
    method = str(req.get("method") or "").upper()
    if layer == "api" and method in ("GET", "HEAD", "OPTIONS"):
        # 只读 API 默认 L0，除非命中 L2 标记
        return "L0"
    for m in _L0_MARKERS:
        if m in blob:
            return "L0"
    if layer in ("hitl", "human"):
        return "L1"
    return "L1"


def _emit(
    kind: str,
    *,
    approval_id: str = "",
    level: str = "",
    stage_id: str = "",
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ev = {
        "at": time.time(),
        "kind": kind,
        "approval_id": approval_id or "",
        "level": level or "",
        "stage_id": stage_id or "",
        "detail": detail or {},
    }
    with _LOCK:
        _EVENTS.append(ev)
        if len(_EVENTS) > _MAX_EVENTS:
            del _EVENTS[: len(_EVENTS) - _MAX_EVENTS]
    return ev


def get_risk_events(
    *,
    approval_id: str = "",
    stage_id: str = "",
    since_ts: float = 0.0,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = list(_EVENTS)
    out: List[Dict[str, Any]] = []
    for e in rows:
        if since_ts and float(e.get("at") or 0) < since_ts:
            continue
        if approval_id and e.get("approval_id") != approval_id:
            continue
        if stage_id and e.get("stage_id") != stage_id:
            continue
        out.append(dict(e))
        if len(out) >= max(1, int(limit)):
            break
    return out


def list_pending_approvals() -> List[Dict[str, Any]]:
    with _LOCK:
        return [
            dict(v)
            for v in _APPROVALS.values()
            if v.get("status") == "pending"
        ]


def request_approval(
    *,
    stage_id: str = "",
    level: str = "L2",
    reason: str = "",
    action: str = "",
    user_id: str = "",
    plan_id: str = "",
) -> Dict[str, Any]:
    """创建待审批记录；返回含 approval_id 的快照。"""
    lvl = _norm_level(level) or "L2"
    aid = f"risk-{uuid.uuid4().hex[:12]}"
    now = time.time()
    rec = {
        "approval_id": aid,
        "status": "pending",
        "level": lvl,
        "stage_id": stage_id or "",
        "reason": reason or "高风险动作需要审批",
        "action": action or "",
        "user_id": user_id or "",
        "plan_id": plan_id or "",
        "token": None,
        "created_at": now,
        "updated_at": now,
    }
    with _LOCK:
        _APPROVALS[aid] = rec
    _emit(
        "approval_requested",
        approval_id=aid,
        level=lvl,
        stage_id=stage_id,
        detail={"reason": rec["reason"], "action": action},
    )
    return dict(rec)


def approve_risk(
    approval_id: str,
    *,
    token: str = "",
    approver: str = "",
) -> Tuple[bool, Optional[str]]:
    """批准 pending 审批，生成或绑定 token。返回 (ok, token|error)。"""
    aid = (approval_id or "").strip()
    if not aid:
        return False, "approval_id 为空"
    with _LOCK:
        rec = _APPROVALS.get(aid)
        if not rec:
            return False, "审批记录不存在"
        if rec.get("status") == "approved" and rec.get("token"):
            return True, str(rec["token"])
        if rec.get("status") == "denied":
            return False, "审批已被拒绝"
        tok = (token or "").strip() or f"tok-{uuid.uuid4().hex[:16]}"
        rec["status"] = "approved"
        rec["token"] = tok
        rec["approver"] = approver or ""
        rec["updated_at"] = time.time()
    _emit(
        "approval_granted",
        approval_id=aid,
        level=str(rec.get("level") or ""),
        stage_id=str(rec.get("stage_id") or ""),
        detail={"approver": approver or ""},
    )
    return True, tok


def deny_risk(approval_id: str, *, reason: str = "", denier: str = "") -> bool:
    aid = (approval_id or "").strip()
    if not aid:
        return False
    with _LOCK:
        rec = _APPROVALS.get(aid)
        if not rec:
            return False
        rec["status"] = "denied"
        rec["deny_reason"] = reason or "已拒绝"
        rec["denier"] = denier or ""
        rec["updated_at"] = time.time()
        stage_id = str(rec.get("stage_id") or "")
        level = str(rec.get("level") or "")
    _emit(
        "approval_denied",
        approval_id=aid,
        level=level,
        stage_id=stage_id,
        detail={"reason": reason or "", "denier": denier or ""},
    )
    return True


def _find_approval_by_token(token: str) -> Optional[Dict[str, Any]]:
    tok = (token or "").strip()
    if not tok:
        return None
    with _LOCK:
        for rec in _APPROVALS.values():
            if rec.get("token") == tok and rec.get("status") == "approved":
                return dict(rec)
    return None


def _plan_token_for_stage(plan: Optional[Dict[str, Any]], stage: Dict[str, Any]) -> str:
    """从 stage / plan.approvals 解析令牌。"""
    tok = str(stage.get("approval_token") or stage.get("risk_token") or "").strip()
    if tok:
        return tok
    if not isinstance(plan, dict):
        return ""
    approvals = plan.get("approvals") or plan.get("risk_approvals") or {}
    if isinstance(approvals, dict):
        sid = str(stage.get("id") or "")
        if sid and approvals.get(sid):
            return str(approvals.get(sid)).strip()
        if approvals.get("*"):
            return str(approvals.get("*")).strip()
        if approvals.get("token"):
            return str(approvals.get("token")).strip()
    if isinstance(approvals, list):
        sid = str(stage.get("id") or "")
        for item in approvals:
            if not isinstance(item, dict):
                continue
            if item.get("stage_id") == sid or item.get("stage_id") == "*":
                t = str(item.get("token") or "").strip()
                if t:
                    return t
    return str(plan.get("approval_token") or "").strip()


def evaluate_stage_risk(
    stage: Optional[Dict[str, Any]],
    *,
    plan: Optional[Dict[str, Any]] = None,
    user_id: str = "",
    auto_request: bool = True,
) -> RiskDecision:
    """评估阶段是否可执行。

    - L0/L1：默认 allow
    - L2：必须持有已批准 token；否则 require_approval / denied
    """
    stage = stage if isinstance(stage, dict) else {}
    stage_id = str(stage.get("id") or "")
    level = classify_stage(stage)
    since = time.time()

    if level in ("L0", "L1"):
        ev = _emit(
            "risk_allowed",
            level=level,
            stage_id=stage_id,
            detail={"auto": True},
        )
        return RiskDecision(
            ok=True,
            level=level,
            decision="allow",
            reason="低风险自动放行",
            events=get_risk_events(stage_id=stage_id, since_ts=since - 0.05),
        )

    # L2
    token = _plan_token_for_stage(plan, stage)
    if token:
        rec = _find_approval_by_token(token)
        if rec:
            # 可选：绑定 stage
            bound = str(rec.get("stage_id") or "")
            if bound and bound != stage_id and bound != "*":
                ev = _emit(
                    "risk_denied",
                    approval_id=str(rec.get("approval_id") or ""),
                    level=level,
                    stage_id=stage_id,
                    detail={"error": "token 绑定了其他 stage"},
                )
                return RiskDecision(
                    ok=False,
                    level=level,
                    decision="denied",
                    error_code="RISK_TOKEN_STAGE_MISMATCH",
                    error="审批令牌与当前阶段不匹配",
                    approval_id=str(rec.get("approval_id") or ""),
                    reason="token stage mismatch",
                    events=get_risk_events(stage_id=stage_id, since_ts=since - 0.05),
                )
            _emit(
                "risk_allowed",
                approval_id=str(rec.get("approval_id") or ""),
                level=level,
                stage_id=stage_id,
                detail={"via": "token"},
            )
            return RiskDecision(
                ok=True,
                level=level,
                decision="allow",
                approval_id=str(rec.get("approval_id") or ""),
                reason="L2 已持有效审批令牌",
                events=get_risk_events(stage_id=stage_id, since_ts=since - 0.05),
            )
        _emit(
            "risk_denied",
            level=level,
            stage_id=stage_id,
            detail={"error": "无效 token"},
        )
        return RiskDecision(
            ok=False,
            level=level,
            decision="denied",
            error_code="RISK_TOKEN_INVALID",
            error="L2 审批令牌无效或未批准",
            reason="invalid token",
            events=get_risk_events(stage_id=stage_id, since_ts=since - 0.05),
        )

    # 无 token：创建 pending（便于 Demo / UI 审批）后拒绝执行
    approval_id = ""
    if auto_request:
        rec = request_approval(
            stage_id=stage_id,
            level=level,
            reason=str(stage.get("risk_reason") or stage.get("label") or "L2 高风险动作"),
            action=str(stage.get("risk_action") or stage.get("skill") or stage_id),
            user_id=user_id,
            plan_id=str((plan or {}).get("plan_id") or ""),
        )
        approval_id = str(rec.get("approval_id") or "")
    _emit(
        "risk_require_approval",
        approval_id=approval_id,
        level=level,
        stage_id=stage_id,
        detail={},
    )
    return RiskDecision(
        ok=False,
        level=level,
        decision="require_approval",
        error_code="RISK_APPROVAL_REQUIRED",
        error="L2 高风险动作需要审批令牌后才能执行",
        approval_id=approval_id or None,
        reason="approval required",
        events=get_risk_events(stage_id=stage_id, since_ts=since - 0.05),
    )


def reset_risk_guard_for_tests() -> None:
    with _LOCK:
        _APPROVALS.clear()
        _EVENTS.clear()
