# -*- coding: utf-8 -*-
"""人机接管（HITL）：验证码/登录确认等需用户介入时的会话级状态。

两类索引：
- ``_PENDING``：按 user_id（AI Agent SSE 路径）
- ``_GATES``：按 gate_id / session_id（跨端编排阻塞等待，与单用户会话隔离）

事件日志（Phase B-3 / R09）：
- ``_EVENTS``：open / resume / cancel / timed_out / wait_*，供 Trace 证据包引用
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
# user_id -> {session_id, reason, hint, status, updated_at, gate_id?}
_PENDING: Dict[str, Dict[str, Any]] = {}
# gate_id -> {status, reason, hint, scope, user_id, created_at, updated_at}
_GATES: Dict[str, Dict[str, Any]] = {}
# 环形事件缓冲（按时间追加）
_EVENTS: List[Dict[str, Any]] = []
_MAX_EVENTS = 800


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_hitl_event(
    kind: str,
    gate_id: str = "",
    *,
    reason: str = "",
    hint: str = "",
    user_id: str = "",
    scope: str = "",
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """写入 HITL 结构化事件（供 Trace / 阶段结果引用）。"""
    ev = {
        "event_id": f"hitl-{uuid.uuid4().hex[:10]}",
        "kind": str(kind or "note"),
        "gate_id": str(gate_id or ""),
        "reason": str(reason or ""),
        "hint": str(hint or ""),
        "user_id": str(user_id or ""),
        "scope": str(scope or ""),
        "at": _utc_iso(),
        "ts": time.time(),
        "detail": dict(detail or {}),
    }
    with _LOCK:
        _EVENTS.append(ev)
        if len(_EVENTS) > _MAX_EVENTS:
            del _EVENTS[: len(_EVENTS) - _MAX_EVENTS]
    return dict(ev)


def get_hitl_events(
    gate_id: str = "",
    *,
    limit: int = 100,
    since_ts: float = 0.0,
) -> List[Dict[str, Any]]:
    """读取 HITL 事件（可按 gate_id 过滤）。返回副本。"""
    gid = (gate_id or "").strip()
    try:
        lim = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        lim = 100
    with _LOCK:
        rows = list(_EVENTS)
    if since_ts:
        rows = [e for e in rows if float(e.get("ts") or 0) >= float(since_ts)]
    if gid:
        rows = [e for e in rows if (e.get("gate_id") or "") == gid]
    return [dict(e) for e in rows[-lim:]]


def hitl_outcome_from_events(events: Optional[List[Dict[str, Any]]]) -> str:
    """从事件序列推断终态：resumed|timed_out|cancelled|missing|unknown。"""
    if not events:
        return "unknown"
    for e in reversed(events):
        k = str((e or {}).get("kind") or "")
        if k in ("resumed", "wait_resumed"):
            return "resumed"
        if k in ("timed_out", "wait_timed_out"):
            return "timed_out"
        if k in ("cancelled", "wait_cancelled"):
            return "cancelled"
        if k in ("missing", "wait_missing"):
            return "missing"
    return "unknown"


def set_need_user_action(
    user_id: str,
    *,
    session_id: str,
    reason: str,
    hint: str = "",
) -> Dict[str, Any]:
    payload = {
        "session_id": session_id,
        "gate_id": session_id,
        "reason": reason,
        "hint": hint,
        "status": "waiting",
        "updated_at": time.time(),
    }
    with _LOCK:
        _PENDING[str(user_id)] = payload
    return dict(payload)


def clear_user_action(user_id: str) -> None:
    with _LOCK:
        _PENDING.pop(str(user_id), None)


def get_pending(user_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        p = _PENDING.get(str(user_id))
        return dict(p) if p else None


def mark_user_resumed(user_id: str) -> bool:
    """将用户级 pending 标为 resumed，并联动其 session_id/gate_id 对应 gate。"""
    gid = ""
    reason = ""
    hint = ""
    scope = ""
    with _LOCK:
        p = _PENDING.get(str(user_id))
        if not p:
            return False
        p["status"] = "resumed"
        p["updated_at"] = time.time()
        gid = (p.get("gate_id") or p.get("session_id") or "").strip()
        reason = str(p.get("reason") or "")
        hint = str(p.get("hint") or "")
        scope = str(p.get("scope") or "")
        if gid and gid in _GATES:
            g = _GATES[gid]
            if g.get("status") in ("waiting", "resumed"):
                g["status"] = "resumed"
                g["updated_at"] = time.time()
    if gid:
        _emit_hitl_event(
            "resumed",
            gid,
            reason=reason,
            hint=hint,
            user_id=str(user_id or ""),
            scope=scope,
            detail={"via": "mark_user_resumed"},
        )
    return True


def open_hitl_gate(
    gate_id: str = "",
    *,
    reason: str = "",
    hint: str = "",
    user_id: str = "",
    scope: str = "cross_end",
) -> Dict[str, Any]:
    """打开阻塞式 HITL 门禁。同 gate_id 重复打开会覆盖为 waiting（新一轮等待）。"""
    gid = (gate_id or "").strip() or f"hitl-{uuid.uuid4().hex[:12]}"
    now = time.time()
    payload = {
        "gate_id": gid,
        "status": "waiting",
        "reason": reason or "等待人工确认",
        "hint": hint or "",
        "scope": (scope or "cross_end").strip() or "cross_end",
        "user_id": str(user_id or ""),
        "created_at": now,
        "updated_at": now,
    }
    with _LOCK:
        _GATES[gid] = payload
        uid = str(user_id or "").strip()
        if uid:
            _PENDING[uid] = {
                "session_id": gid,
                "gate_id": gid,
                "reason": payload["reason"],
                "hint": payload["hint"],
                "status": "waiting",
                "updated_at": now,
                "scope": payload["scope"],
            }
    _emit_hitl_event(
        "opened",
        gid,
        reason=payload["reason"],
        hint=payload["hint"],
        user_id=payload["user_id"],
        scope=payload["scope"],
    )
    return dict(payload)


def get_hitl_gate(gate_id: str) -> Optional[Dict[str, Any]]:
    gid = (gate_id or "").strip()
    if not gid:
        return None
    with _LOCK:
        g = _GATES.get(gid)
        return dict(g) if g else None


def list_hitl_gates(*, status: str = "") -> List[Dict[str, Any]]:
    """列出门禁快照；status 可过滤 waiting/resumed/cancelled/timed_out。"""
    want = (status or "").strip().lower()
    with _LOCK:
        rows = [dict(g) for g in _GATES.values()]
    if want:
        rows = [g for g in rows if str(g.get("status") or "").lower() == want]
    rows.sort(key=lambda g: float(g.get("updated_at") or g.get("created_at") or 0), reverse=True)
    return rows


def resume_hitl_gate(gate_id: str) -> bool:
    """幂等：waiting→resumed；已 resumed 仍返回 True；timed_out/cancelled 返回 False。"""
    gid = (gate_id or "").strip()
    if not gid:
        return False
    already = False
    reason = ""
    hint = ""
    user_id = ""
    scope = ""
    with _LOCK:
        g = _GATES.get(gid)
        if not g:
            return False
        st = g.get("status")
        if st == "resumed":
            already = True
        elif st != "waiting":
            return False
        else:
            g["status"] = "resumed"
            g["updated_at"] = time.time()
            uid = str(g.get("user_id") or "").strip()
            if uid and uid in _PENDING:
                p = _PENDING[uid]
                if (p.get("gate_id") or p.get("session_id")) == gid:
                    p["status"] = "resumed"
                    p["updated_at"] = time.time()
        reason = str(g.get("reason") or "")
        hint = str(g.get("hint") or "")
        user_id = str(g.get("user_id") or "")
        scope = str(g.get("scope") or "")
    _emit_hitl_event(
        "resumed",
        gid,
        reason=reason,
        hint=hint,
        user_id=user_id,
        scope=scope,
        detail={"idempotent": already},
    )
    return True


def cancel_hitl_gate(gate_id: str) -> bool:
    gid = (gate_id or "").strip()
    if not gid:
        return False
    already = False
    reason = ""
    hint = ""
    user_id = ""
    scope = ""
    with _LOCK:
        g = _GATES.get(gid)
        if not g:
            return False
        if g.get("status") in ("timed_out", "cancelled"):
            already = True
        else:
            g["status"] = "cancelled"
            g["updated_at"] = time.time()
        reason = str(g.get("reason") or "")
        hint = str(g.get("hint") or "")
        user_id = str(g.get("user_id") or "")
        scope = str(g.get("scope") or "")
    _emit_hitl_event(
        "cancelled",
        gid,
        reason=reason,
        hint=hint,
        user_id=user_id,
        scope=scope,
        detail={"idempotent": already},
    )
    return True


def clear_hitl_gate(gate_id: str) -> None:
    gid = (gate_id or "").strip()
    if not gid:
        return
    with _LOCK:
        g = _GATES.pop(gid, None)
        if not g:
            return
        uid = str(g.get("user_id") or "").strip()
        if uid and uid in _PENDING:
            p = _PENDING[uid]
            if (p.get("gate_id") or p.get("session_id")) == gid:
                _PENDING.pop(uid, None)


def wait_hitl_gate(
    gate_id: str,
    timeout_s: float = 300.0,
    *,
    poll_interval_s: float = 0.25,
    auto_open: bool = False,
    reason: str = "",
    hint: str = "",
    user_id: str = "",
    scope: str = "cross_end",
) -> bool:
    """阻塞直到 gate resumed / cancelled / 超时。

    返回 True 仅当显式 resume；超时与取消均为 False（防假绿）。
    """
    gid = (gate_id or "").strip()
    if not gid:
        _emit_hitl_event("wait_missing", "", reason=reason, hint=hint, user_id=user_id, scope=scope)
        return False

    try:
        timeout = float(timeout_s)
    except (TypeError, ValueError):
        timeout = 300.0
    if timeout <= 0:
        # 立即失败：不允许「零超时当成功」
        with _LOCK:
            g = _GATES.get(gid)
            if g and g.get("status") == "resumed":
                _GATES.pop(gid, None)
                _emit_hitl_event(
                    "wait_resumed",
                    gid,
                    reason=reason or str(g.get("reason") or ""),
                    hint=hint or str(g.get("hint") or ""),
                    user_id=user_id or str(g.get("user_id") or ""),
                    scope=scope or str(g.get("scope") or ""),
                    detail={"timeout_s": 0, "pre_resumed": True},
                )
                return True
            if g:
                g["status"] = "timed_out"
                g["updated_at"] = time.time()
        _emit_hitl_event(
            "wait_timed_out",
            gid,
            reason=reason,
            hint=hint,
            user_id=user_id,
            scope=scope,
            detail={"timeout_s": 0},
        )
        clear_hitl_gate(gid)
        return False

    try:
        interval = float(poll_interval_s)
    except (TypeError, ValueError):
        interval = 0.25
    interval = max(0.05, min(interval, 2.0))

    existing = get_hitl_gate(gid)
    if existing is None:
        if not auto_open:
            _emit_hitl_event(
                "wait_missing",
                gid,
                reason=reason,
                hint=hint,
                user_id=user_id,
                scope=scope,
                detail={"auto_open": False},
            )
            return False
        open_hitl_gate(
            gid,
            reason=reason or "等待人工确认",
            hint=hint,
            user_id=user_id,
            scope=scope,
        )
    elif auto_open and existing.get("status") not in ("waiting", "resumed"):
        open_hitl_gate(
            gid,
            reason=reason or "等待人工确认",
            hint=hint,
            user_id=user_id,
            scope=scope,
        )

    _emit_hitl_event(
        "wait_started",
        gid,
        reason=reason or (existing or {}).get("reason") or "等待人工确认",
        hint=hint or (existing or {}).get("hint") or "",
        user_id=user_id,
        scope=scope,
        detail={"timeout_s": timeout},
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        with _LOCK:
            g = _GATES.get(gid)
            if not g:
                _emit_hitl_event("wait_missing", gid, reason=reason, hint=hint, user_id=user_id, scope=scope)
                return False
            st = g.get("status")
            if st == "resumed":
                _GATES.pop(gid, None)
                uid = str(g.get("user_id") or "").strip()
                if uid and uid in _PENDING:
                    p = _PENDING[uid]
                    if (p.get("gate_id") or p.get("session_id")) == gid:
                        _PENDING.pop(uid, None)
                snap = dict(g)
                # emit outside lock below
            elif st in ("cancelled", "timed_out"):
                _GATES.pop(gid, None)
                snap = dict(g)
            else:
                snap = None
        if snap is not None:
            if snap.get("status") == "resumed":
                _emit_hitl_event(
                    "wait_resumed",
                    gid,
                    reason=str(snap.get("reason") or reason),
                    hint=str(snap.get("hint") or hint),
                    user_id=user_id or str(snap.get("user_id") or ""),
                    scope=scope or str(snap.get("scope") or ""),
                )
                return True
            kind = "wait_cancelled" if snap.get("status") == "cancelled" else "wait_timed_out"
            _emit_hitl_event(
                kind,
                gid,
                reason=str(snap.get("reason") or reason),
                hint=str(snap.get("hint") or hint),
                user_id=user_id or str(snap.get("user_id") or ""),
                scope=scope or str(snap.get("scope") or ""),
            )
            return False
        time.sleep(interval)

    # 超时：再读一次，避免与 resume 竞态丢成功
    with _LOCK:
        g = _GATES.get(gid)
        if g and g.get("status") == "resumed":
            _GATES.pop(gid, None)
            snap = dict(g)
            raced = True
        else:
            raced = False
            if g:
                g["status"] = "timed_out"
                g["updated_at"] = time.time()
                snap = dict(g)
            else:
                snap = None
    if raced:
        _emit_hitl_event(
            "wait_resumed",
            gid,
            reason=str((snap or {}).get("reason") or reason),
            hint=str((snap or {}).get("hint") or hint),
            user_id=user_id or str((snap or {}).get("user_id") or ""),
            scope=scope or str((snap or {}).get("scope") or ""),
            detail={"race_after_deadline": True},
        )
        return True
    _emit_hitl_event(
        "wait_timed_out",
        gid,
        reason=reason or str((snap or {}).get("reason") or ""),
        hint=hint or str((snap or {}).get("hint") or ""),
        user_id=user_id,
        scope=scope,
        detail={"timeout_s": timeout},
    )
    clear_hitl_gate(gid)
    return False


def reset_hitl_state_for_tests() -> None:
    """仅供单测清空全局状态。"""
    with _LOCK:
        _PENDING.clear()
        _GATES.clear()
        _EVENTS.clear()


def looks_like_hitl_needed(
    text: str,
    *,
    tools_used: Optional[List[str]] = None,
    cross_end_vars: Optional[Dict[str, Any]] = None,
) -> bool:
    """仅在真正需要用户介入时返回 True。

    关键排除条件（AI 有能力自动处理时不触发 HITL）：
    - 有 sms_otp 变量（mobile_extract_otp 已成功取码）→ 验证码关键词不触发
    - 有 mobile_extract_otp 工具调用过 → 验证码/登录相关关键词不触发
    - 只有 NEED_USER_ACTION 显式标记 或 扫码/人机验证（AI 无法自动处理的）才触发
    """
    raw = text or ""
    t = raw.lower()
    # 显式 NEED_USER_ACTION 标记才触发最高优先级
    if "NEED_USER_ACTION:" in raw:
        return True
    if "need_user_action" in t and ("need_user_action:" in t or "NEED_USER_ACTION" in raw):
        return True
    # 鉴权/兜底失败不是 HITL
    if any(
        k in t
        for k in (
            "auth_fatal",
            "platform_desktop_fallback",
            "missing authentication",
            "401",
            "app_query",
            "hermes_auth",
        )
    ):
        return False
    # AI 可自动处理的条件：已成功调用 mobile_extract_otp 或已有 sms_otp
    tools_used = tools_used or []
    cross_end_vars = cross_end_vars or {}
    has_otp_capability = (
        "mobile_extract_otp" in tools_used
        or bool(cross_end_vars.get("sms_otp"))
    )
    # 扫码登录、人机验证（滑动/点选）AI 无法自动处理 → 触发
    must_hitl_keys = ("人机验证", "captcha", "扫码登录")
    if any(k in raw or k in t for k in must_hitl_keys):
        return True
    # 其他关键词只有在 AI 不具备自动处理能力时才触发
    weak_keys = (
        "验证码",
        "等待人工",
        "请先登录",
        "需要登录后",
    )
    if any(k in raw or k in t for k in weak_keys):
        if not has_otp_capability:
            return True
    return False
