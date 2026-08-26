# -*- coding: utf-8 -*-
"""供 execution worker 调用的本机用例执行（避免 server 模式再次入队）。"""
from __future__ import annotations

import os
from typing import Any, Dict


def run_case_on_local_app(case_id: int, user_id: int, port: int = None) -> Dict[str, Any]:
    """通过本机 Flask 内部 API 同步执行用例（仅 client/standalone）。"""
    import json
    import urllib.error
    import urllib.request

    p = port or int(os.environ.get("FLASK_RUN_PORT", "5000"))
    secret = (os.environ.get("EXECUTION_WORKER_SECRET") or "uat-local-worker").strip()
    url = f"http://127.0.0.1:{p}/api/internal/run-case"
    payload = json.dumps({"case_id": case_id, "user_id": user_id}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Execution-Worker-Secret": secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"success": False, "error": body or str(e), "status": "error"}
