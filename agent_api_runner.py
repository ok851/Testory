# -*- coding: utf-8 -*-
"""平台侧薄封装：供 Hermes skill / 内部调用执行临时 HTTP 或接口用例。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


def run_temp_http(
    *,
    method: str = "GET",
    url: str = "",
    headers: Optional[Dict[str, str]] = None,
    body: Any = None,
    timeout_sec: float = 30.0,
) -> Dict[str, Any]:
    method = (method or "GET").strip().upper()
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "url 为空"}
    step = {
        "action": "api_request",
        "method": method,
        "url": url,
        "headers": headers or {},
        "body": body if body is not None else "",
        "timeout": timeout_sec,
    }
    try:
        from playwright_automation import sync_run_api_request_step

        result = sync_run_api_request_step(step)
        if isinstance(result, dict):
            return {"ok": result.get("status") != "error", "result": result}
        return {"ok": True, "result": result}
    except Exception as e:
        # 降级：直接 requests
        try:
            import requests

            kw: Dict[str, Any] = {"headers": headers or {}, "timeout": timeout_sec}
            if body is not None and method in ("POST", "PUT", "PATCH"):
                if isinstance(body, (dict, list)):
                    kw["json"] = body
                else:
                    kw["data"] = body
            resp = requests.request(method, url, **kw)
            text = (resp.text or "")[:4000]
            return {
                "ok": 200 <= resp.status_code < 400,
                "status_code": resp.status_code,
                "body_preview": text,
            }
        except Exception as e2:
            return {"ok": False, "error": f"{e}; fallback: {e2}"}


def run_api_case(case_id: int, db=None) -> Dict[str, Any]:
    try:
        from playwright_automation import sync_run_api_case_for_batch

        if db is None:
            from database import db as _db

            db = _db
        payload = sync_run_api_case_for_batch(int(case_id), db)
        return {"ok": (payload or {}).get("status") == "success", "result": payload}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def summarize_for_agent(result: Dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)[:8000]
