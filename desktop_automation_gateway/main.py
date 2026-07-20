# -*- coding: utf-8 -*-
"""
Windows 桌面自动化远程 Agent（FastAPI）。

环境变量：
  DESKTOP_AGENT_GATEWAY_SECRET  与 Flask 平台共用，必填
  DESKTOP_AGENT_GATE_PORT       默认 8766

HTTP:
  POST /internal/session                 -> { session_id }
  POST /internal/session/{id}/run-steps  -> { steps: [...] }
  POST /internal/session/{id}/inspect    -> UIA 树片段
"""

from __future__ import annotations

import os
import secrets
import uuid
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="Desktop Automation Gateway")
_sessions: Dict[str, Any] = {}


def _secret_ok(request: Request) -> bool:
    expected = (os.environ.get("DESKTOP_AGENT_GATEWAY_SECRET") or "").strip()
    if not expected:
        return False
    got = (request.headers.get("X-Desktop-Agent-Secret") or "").strip()
    if got and secrets.compare_digest(got, expected):
        return True
    # Hermes / curl 常误用 Authorization: Bearer <secret>，一并接受以免反复 401
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token and secrets.compare_digest(token, expected):
            return True
    return False


@app.get("/health")
async def health():
    """无鉴权；供平台能力探测与 Hermes 探活。"""
    return {
        "ok": True,
        "service": "desktop_automation_gateway",
        "auth": "X-Desktop-Agent-Secret or Authorization: Bearer",
    }


@app.post("/internal/session")
async def create_session(request: Request):
    if not _secret_ok(request):
        raise HTTPException(401, "unauthorized")
    sid = str(uuid.uuid4())
    _sessions[sid] = {"id": sid}
    return {"success": True, "session_id": sid}


@app.post("/internal/session/{session_id}/run-steps")
async def run_steps(session_id: str, request: Request):
    if not _secret_ok(request):
        raise HTTPException(401, "unauthorized")
    body = await request.json()
    steps = body.get("steps") or []
    if session_id not in _sessions:
        _sessions[session_id] = {"id": session_id}
    from desktop_automation import _sync_desktop_execute_inprocess
    from desktop_env_config import prepare_desktop_step

    results: List[Dict[str, Any]] = []
    for step in steps:
        try:
            results.append(_sync_desktop_execute_inprocess(prepare_desktop_step(step)))
        except Exception as e:
            results.append({"status": "error", "error": str(e), "step": step.get("action")})
            return {"success": False, "results": results, "error": str(e)}
    return {"success": True, "results": results}


@app.post("/internal/session/{session_id}/inspect")
async def inspect(session_id: str, request: Request):
    if not _secret_ok(request):
        raise HTTPException(401, "unauthorized")
    body = await request.json()
    spec = body.get("desktop_spec") or {}
    if spec:
        from desktop_automation import sync_desktop_attach_from_spec

        sync_desktop_attach_from_spec(spec)
    from desktop_automation import sync_desktop_inspect

    nodes = sync_desktop_inspect(
        max_depth=int(body.get("max_depth") or 4),
        max_nodes=int(body.get("max_nodes") or 120),
    )
    return {"success": True, "nodes": nodes}
