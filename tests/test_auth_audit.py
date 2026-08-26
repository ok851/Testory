# -*- coding: utf-8 -*-
"""登录 / SSO 审计串联。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash

from database import Database
from modules.auth.auth_audit import (
    ACTION_LOGIN_FAILURE,
    ACTION_LOGIN_SUCCESS,
    ACTION_LOGOUT,
    ACTION_SSO_LOGIN_SUCCESS,
    AUDIT_TARGET_TYPE_AUTH,
    list_auth_audit_events,
    record_auth_audit,
)
from ai_modules.execute.customer_audit_pack import build_customer_audit_pack


def test_record_login_success_and_failure():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    Database._schema_initialized = False
    try:
        db = Database(path)
        uid = db.create_user("alice", generate_password_hash("pass1234"), role="tester")
        lid = record_auth_audit(
            action=ACTION_LOGIN_SUCCESS,
            username="alice",
            user_id=uid,
            ip_address="127.0.0.1",
            details={"method": "password"},
            db=db,
        )
        assert lid
        fid = record_auth_audit(
            action=ACTION_LOGIN_FAILURE,
            username="bob",
            user_id=None,
            ip_address="10.0.0.2",
            details={"method": "password", "reason": "bad_credentials"},
            db=db,
        )
        assert fid
        record_auth_audit(
            action=ACTION_SSO_LOGIN_SUCCESS,
            username="alice",
            user_id=uid,
            details={"method": "sso", "provider": "oauth2"},
            db=db,
        )
        record_auth_audit(
            action=ACTION_LOGOUT,
            username="alice",
            user_id=uid,
            db=db,
        )
        events = list_auth_audit_events(db=db, limit=50)
        assert len(events) >= 4
        actions = {e["action"] for e in events}
        assert ACTION_LOGIN_SUCCESS in actions
        assert ACTION_LOGIN_FAILURE in actions
        assert ACTION_SSO_LOGIN_SUCCESS in actions
        rows = db.get_audit_logs(target_type=AUDIT_TARGET_TYPE_AUTH, page_size=50)
        assert len(rows) >= 4
        assert all(r["target_type"] == "auth" for r in rows)
    finally:
        Database._schema_initialized = False
        try:
            os.remove(path)
        except OSError:
            pass


def test_customer_pack_includes_auth_events():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    Database._schema_initialized = False
    try:
        db = Database(path)
        pid = db.create_project("auth-pack", "")
        uid = db.create_user("ops", generate_password_hash("pass1234"), role="admin")
        record_auth_audit(
            action=ACTION_LOGIN_SUCCESS,
            username="ops",
            user_id=uid,
            details={"method": "password"},
            db=db,
        )
        out = Path(tempfile.mkdtemp()) / "p"
        exported = build_customer_audit_pack(
            project_id=pid,
            db=db,
            out_dir=out,
            make_zip=False,
            embed_limit=0,
        )
        auth_path = Path(exported["pack_dir"]) / "auth_events.json"
        assert auth_path.is_file()
        data = json.loads(auth_path.read_text(encoding="utf-8"))
        assert data["count"] >= 1
        assert data["events"][0]["action"] == ACTION_LOGIN_SUCCESS
        assert exported["manifest"]["counts"].get("auth_events", 0) >= 1
    finally:
        Database._schema_initialized = False
        try:
            os.remove(path)
        except OSError:
            pass
