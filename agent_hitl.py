# -*- coding: utf-8 -*-
"""人机接管（HITL）：验证码/登录确认等需用户介入时的会话级状态。"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

_LOCK = threading.RLock()
# user_id -> {session_id, reason, hint, status, updated_at}
_PENDING: Dict[str, Dict[str, Any]] = {}


def set_need_user_action(
    user_id: str,
    *,
    session_id: str,
    reason: str,
    hint: str = "",
) -> Dict[str, Any]:
    payload = {
        "session_id": session_id,
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
    with _LOCK:
        p = _PENDING.get(str(user_id))
        if not p:
            return False
        p["status"] = "resumed"
        p["updated_at"] = time.time()
        return True


def looks_like_hitl_needed(text: str) -> bool:
    """仅在真正需要用户介入时返回 True（验证码/扫码登录等）。

    勿匹配「可先手动完成」这类失败建议，否则会把桌面兜底 JSON 误标成 HITL。
    """
    raw = text or ""
    t = raw.lower()
    # 显式标记优先
    if "NEED_USER_ACTION:" in raw or "need_user_action" in t:
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
    keys = (
        "验证码",
        "captcha",
        "人机验证",
        "扫码登录",
        "等待人工",
        "请先登录",
        "需要登录后",
    )
    return any(k in raw or k in t for k in keys)
