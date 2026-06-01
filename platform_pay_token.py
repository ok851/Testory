# -*- coding: utf-8 -*-
"""软件 → 官网支付跳转：短时 pay_token 签发与校验。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional


def _secret() -> bytes:
    raw = (
        os.environ.get("PLATFORM_PAY_TOKEN_SECRET")
        or os.environ.get("PLATFORM_ADMIN_SECRET")
        or "uat-platform-pay-token-dev"
    )
    return raw.strip().encode("utf-8")


def create_pay_token(
    user_id: int,
    username: str,
    email: str = "",
    team_server_url: str = "",
    expires_in: int = 3600,
) -> str:
    payload = {
        "uid": int(user_id),
        "username": (username or "").strip(),
        "email": (email or "").strip(),
        "team_server_url": (team_server_url or "").strip(),
        "exp": int(time.time()) + max(60, int(expires_in)),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verify_pay_token(token: str) -> Optional[Dict[str, Any]]:
    token = (token or "").strip()
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        return None
    pad = "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + pad).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    if not payload.get("uid") or not payload.get("username"):
        return None
    return payload
