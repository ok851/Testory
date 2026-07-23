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


def _sync_secret(explicit: str = "") -> str:
    raw = (
        explicit
        or os.environ.get("PLATFORM_SYNC_SECRET")
        or os.environ.get("PLATFORM_ADMIN_SECRET")
        or ""
    ).strip()
    # dotenv 对「KEY=   # 注释」可能把注释当成值；HTTP 头必须是 latin-1
    if not raw or raw.startswith("#"):
        raw = (os.environ.get("PLATFORM_ADMIN_SECRET") or "").strip()
    if not raw or raw.startswith("#"):
        return ""
    try:
        raw.encode("latin-1")
    except UnicodeEncodeError:
        return ""
    return raw


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
    secret = _sync_secret(sync_secret)
    if secret:
        headers["X-Platform-Sync-Secret"] = secret
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    bases = [admin_url]
    if not admin_url.endswith("/admin"):
        bases.append(admin_url + "/admin")
    last: Dict[str, Any] = {"success": False, "error": "unreachable"}
    for base in bases:
        req = urllib.request.Request(
            base + path,
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
                last = json.loads(raw) if raw else {"success": False, "error": str(e)}
            except Exception:
                last = {"success": False, "error": str(e)}
            # 404 时尝试下一候选；其它 HTTP 错误直接返回
            if e.code != 404:
                return last
        except Exception as e:
            last = {"success": False, "error": str(e)}
            continue
    return last
