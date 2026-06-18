# -*- coding: utf-8 -*-
"""HTTP client for website / main platform -> platform admin APIs."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


def _admin_url() -> str:
    return (os.environ.get("PLATFORM_ADMIN_URL") or "").strip().rstrip("/")


def platform_api_json(
    path: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    *,
    sync_secret: str = "",
) -> Dict[str, Any]:
    admin_url = _admin_url()
    if not admin_url:
        return {"success": False, "error": "未配置 PLATFORM_ADMIN_URL"}
    if not path.startswith("/"):
        path = f"/{path}"
    data = None
    headers = {"Accept": "application/json"}
    secret = (
        sync_secret
        or os.environ.get("PLATFORM_SYNC_SECRET")
        or os.environ.get("PLATFORM_ADMIN_SECRET")
        or ""
    ).strip()
    if secret:
        headers["X-Platform-Sync-Secret"] = secret
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        admin_url + path,
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": True}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": False, "error": str(e)}
        except Exception:
            return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}
