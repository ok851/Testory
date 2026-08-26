# -*- coding: utf-8 -*-
"""登录 / SSO / 注销 审计落库（企业合规：谁何时以何方式进入系统）。

与 ``@audit_log`` 装饰器互补：认证发生在 ``login_user`` 前后，须显式调用本模块。
失败登录也记一条（username 可为尝试名，user_id=0），不得静默丢弃。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from database import Database
from modules.core.logger import uat_logger

AUDIT_TARGET_TYPE_AUTH = "auth"

ACTION_LOGIN_SUCCESS = "LOGIN_SUCCESS"
ACTION_LOGIN_FAILURE = "LOGIN_FAILURE"
ACTION_LOGOUT = "LOGOUT"
ACTION_REGISTER = "REGISTER"
ACTION_SSO_LOGIN_SUCCESS = "SSO_LOGIN_SUCCESS"
ACTION_SSO_LOGIN_FAILURE = "SSO_LOGIN_FAILURE"
ACTION_LDAP_LOGIN_SUCCESS = "LDAP_LOGIN_SUCCESS"
ACTION_LDAP_LOGIN_FAILURE = "LDAP_LOGIN_FAILURE"
ACTION_PASSWORD_RESET = "PASSWORD_RESET"


def record_auth_audit(
    *,
    action: str,
    username: str = "",
    user_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    db: Any = None,
) -> Optional[int]:
    """写入 audit_logs；失败仅打日志，不抛到主路径。

    未知用户（登录失败）传 ``user_id=None``，避免 FK 失败导致丢审计。
    """
    try:
        inst = db if db is not None else Database()
        uid = None
        if user_id is not None:
            try:
                uid = int(user_id)
            except (TypeError, ValueError):
                uid = None
            if uid is not None and uid <= 0:
                uid = None
        blob = dict(details or {})
        blob.setdefault("source", "auth")
        return inst.add_audit_log(
            uid,
            (username or "").strip() or "(unknown)",
            str(action or "").strip() or "AUTH_EVENT",
            AUDIT_TARGET_TYPE_AUTH,
            None,
            json.dumps(blob, ensure_ascii=False),
            ip_address,
        )
    except Exception as exc:
        uat_logger.debug("record_auth_audit: %s", exc)
        return None


def list_auth_audit_events(
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    username: Optional[str] = None,
    limit: int = 500,
    db: Any = None,
) -> list:
    """按日期窗拉取 auth 类审计（供客户审计包）。"""
    try:
        inst = db if db is not None else Database()
    except Exception:
        return []
    conn = inst._sqlite_connect()
    cursor = conn.cursor()
    where = ["target_type = ?"]
    params: list = [AUDIT_TARGET_TYPE_AUTH]
    if start_date:
        where.append("DATE(created_at) >= ?")
        params.append(start_date)
    if end_date:
        where.append("DATE(created_at) <= ?")
        params.append(end_date)
    if username and str(username).strip():
        where.append("username LIKE ?")
        params.append("%" + str(username).strip() + "%")
    lim = max(1, min(int(limit or 500), 2000))
    cursor.execute(
        f"""
        SELECT id, user_id, username, action, details, ip_address, created_at
        FROM audit_logs
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        LIMIT ?
        """,
        params + [lim],
    )
    rows = cursor.fetchall()
    conn.close()
    out = []
    for r in rows:
        detail_raw = r[4] or ""
        try:
            detail_obj = json.loads(detail_raw) if detail_raw else {}
        except (TypeError, json.JSONDecodeError):
            detail_obj = {"raw": str(detail_raw)[:500]}
        out.append(
            {
                "id": r[0],
                "user_id": r[1],
                "username": r[2],
                "action": r[3],
                "details": detail_obj,
                "ip_address": r[5],
                "created_at": r[6],
            }
        )
    return out
