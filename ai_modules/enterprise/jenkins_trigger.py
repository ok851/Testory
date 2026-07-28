# -*- coding: utf-8 -*-
"""从 Testory 反向触发 / 轮询 Jenkins Job（Remote Access API）。

诚实约束：
- 触发成功（受理）≠ Jenkins 流水线已通过 ≠ Testory 用例已绿
- 统一门禁见 ``ci_unified_sync``：两侧终态后再算 ``unified_gate_passed``
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


def jenkins_config_from_env() -> Dict[str, str]:
    return {
        "base_url": (os.environ.get("JENKINS_URL") or "").strip().rstrip("/"),
        "user": (os.environ.get("JENKINS_USER") or "").strip(),
        "token": (
            os.environ.get("JENKINS_API_TOKEN")
            or os.environ.get("JENKINS_TOKEN")
            or ""
        ).strip(),
    }


def jenkins_configured() -> bool:
    c = jenkins_config_from_env()
    return bool(c["base_url"] and c["user"] and c["token"])


def _auth_header(user: str, token: str) -> str:
    raw = f"{user}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _request(
    method: str,
    url: str,
    *,
    user: str,
    token: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 20.0,
) -> Tuple[int, bytes, Dict[str, str]]:
    hdrs = {
        "Authorization": _auth_header(user, token),
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = getattr(resp, "status", None) or resp.getcode()
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return int(status), body, resp_headers
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        resp_headers = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        return int(e.code), body, resp_headers


def fetch_crumb(base_url: str, user: str, token: str) -> Optional[Dict[str, str]]:
    """获取 CSRF crumb；部分 Jenkins 关闭 crumb 时返回 None。"""
    url = base_url.rstrip("/") + "/crumbIssuer/api/json"
    status, body, _ = _request("GET", url, user=user, token=token, timeout=10.0)
    if status != 200:
        return None
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    field = data.get("crumbRequestField") or "Jenkins-Crumb"
    crumb = data.get("crumb")
    if not crumb:
        return None
    return {"field": str(field), "crumb": str(crumb)}


def trigger_jenkins_job(
    *,
    job_name: str,
    parameters: Optional[Dict[str, Any]] = None,
    base_url: str = "",
    user: str = "",
    token: str = "",
) -> Dict[str, Any]:
    """触发 Jenkins job（build 或 buildWithParameters）。"""
    cfg = jenkins_config_from_env()
    base = (base_url or cfg["base_url"]).rstrip("/")
    user = (user or cfg["user"]).strip()
    token = (token or cfg["token"]).strip()
    job = (job_name or "").strip().strip("/")
    if not base or not user or not token:
        return {
            "ok": False,
            "error_code": "JENKINS_NOT_CONFIGURED",
            "error": "请配置 JENKINS_URL / JENKINS_USER / JENKINS_API_TOKEN",
            "case_pass_claimed": False,
            "jenkins_build_claimed_pass": False,
        }
    if not job:
        return {
            "ok": False,
            "error_code": "JOB_NAME_REQUIRED",
            "error": "job_name 不能为空",
            "case_pass_claimed": False,
            "jenkins_build_claimed_pass": False,
        }

    # 支持 folder 路径：folder/job → job/folder/job/jobName
    parts = [urllib.parse.quote(p) for p in job.split("/") if p]
    job_path = "/job/".join(parts)
    params = parameters if isinstance(parameters, dict) else {}
    if params:
        qs = urllib.parse.urlencode({str(k): str(v) for k, v in params.items()})
        url = f"{base}/job/{job_path}/buildWithParameters?{qs}"
    else:
        url = f"{base}/job/{job_path}/build"

    headers: Dict[str, str] = {}
    crumb = fetch_crumb(base, user, token)
    if crumb:
        headers[crumb["field"]] = crumb["crumb"]

    status, body, resp_headers = _request(
        "POST",
        url,
        user=user,
        token=token,
        data=b"",
        headers=headers,
        timeout=30.0,
    )
    # Jenkins 成功触发通常 201，Location 指向 queue
    location = resp_headers.get("location") or resp_headers.get("Location") or ""
    ok = status in (200, 201)
    return {
        "ok": ok,
        "status_code": status,
        "job_name": job,
        "trigger_url": url.split("?")[0],
        "queue_url": location or None,
        "body_preview": body.decode("utf-8", errors="replace")[:300] if body else "",
        "error_code": None if ok else "JENKINS_TRIGGER_FAILED",
        "error": None if ok else (body.decode("utf-8", errors="replace")[:200] or f"http_{status}"),
        "case_pass_claimed": False,
        "jenkins_build_claimed_pass": False,
        "disclaimer": (
            "仅表示 Jenkins 已受理构建请求（队列/触发）；"
            "不等于 Job 已通过，也不等于 Testory 用例已绿。"
            "统一门禁请用 /api/ci/sync。"
        ),
    }


def _api_json(url: str, user: str, token: str) -> Tuple[int, Any]:
    status, body, _ = _request("GET", url, user=user, token=token, timeout=15.0)
    if status != 200:
        return status, None
    try:
        return status, json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return status, None


def resolve_queue_to_build(
    queue_url: str,
    *,
    base_url: str = "",
    user: str = "",
    token: str = "",
) -> Dict[str, Any]:
    """将 queue item 解析为 executable build URL（若尚未分配则 building=False, terminal=False）。"""
    cfg = jenkins_config_from_env()
    user = (user or cfg["user"]).strip()
    token = (token or cfg["token"]).strip()
    q = (queue_url or "").strip()
    if not q:
        return {"ok": False, "error": "empty_queue_url", "terminal": False}
    if not q.rstrip("/").endswith("/api/json"):
        q = q.rstrip("/") + "/api/json"
    status, data = _api_json(q, user, token)
    if status != 200 or not isinstance(data, dict):
        return {
            "ok": False,
            "error": f"queue_http_{status}",
            "terminal": False,
            "status_code": status,
        }
    if data.get("cancelled"):
        return {
            "ok": True,
            "terminal": True,
            "result": "ABORTED",
            "building": False,
            "queue_url": queue_url,
        }
    exe = data.get("executable") if isinstance(data.get("executable"), dict) else None
    if not exe:
        return {
            "ok": True,
            "terminal": False,
            "building": True,
            "result": None,
            "queue_url": queue_url,
            "why": data.get("why"),
        }
    build_url = str(exe.get("url") or "").rstrip("/")
    number = exe.get("number")
    return {
        "ok": True,
        "terminal": False,
        "building": True,
        "result": None,
        "build_url": build_url or None,
        "build_number": number,
        "queue_url": queue_url,
    }


def fetch_build_status(
    build_url: str,
    *,
    user: str = "",
    token: str = "",
) -> Dict[str, Any]:
    """读取 Jenkins build result / building。"""
    cfg = jenkins_config_from_env()
    user = (user or cfg["user"]).strip()
    token = (token or cfg["token"]).strip()
    bu = (build_url or "").strip().rstrip("/")
    if not bu:
        return {"ok": False, "error": "empty_build_url", "terminal": False}
    api = bu + "/api/json?tree=number,result,building,url"
    status, data = _api_json(api, user, token)
    if status != 200 or not isinstance(data, dict):
        return {
            "ok": False,
            "error": f"build_http_{status}",
            "terminal": False,
            "status_code": status,
        }
    building = bool(data.get("building"))
    result = data.get("result")
    terminal = (not building) and (result is not None)
    return {
        "ok": True,
        "build_url": str(data.get("url") or bu).rstrip("/"),
        "build_number": data.get("number"),
        "building": building,
        "result": result,
        "terminal": terminal,
    }


def resolve_jenkins_build_status(
    *,
    queue_url: str = "",
    build_url: str = "",
    user: str = "",
    token: str = "",
) -> Dict[str, Any]:
    """优先用 build_url；否则从 queue 解析再读 build。"""
    bu = (build_url or "").strip()
    if not bu and queue_url:
        q = resolve_queue_to_build(queue_url, user=user, token=token)
        if q.get("terminal"):
            return q
        if q.get("build_url"):
            bu = str(q["build_url"])
        else:
            return q
    if not bu:
        return {"ok": False, "error": "no_build_or_queue", "terminal": False}
    return fetch_build_status(bu, user=user, token=token)


def submit_build_description(
    build_url: str,
    description: str,
    *,
    user: str = "",
    token: str = "",
    base_url: str = "",
) -> bool:
    """向已有 build 写入描述（回写 Testory 同步摘要；不改写 Jenkins result）。"""
    cfg = jenkins_config_from_env()
    base = (base_url or cfg["base_url"]).rstrip("/")
    user = (user or cfg["user"]).strip()
    token = (token or cfg["token"]).strip()
    bu = (build_url or "").strip().rstrip("/")
    text = (description or "").strip()
    if not bu or not text or not user or not token:
        return False
    headers: Dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}
    if base:
        crumb = fetch_crumb(base, user, token)
        if crumb:
            headers[crumb["field"]] = crumb["crumb"]
    body = urllib.parse.urlencode({"description": text}).encode("utf-8")
    status, _, _ = _request(
        "POST",
        bu + "/submitDescription",
        user=user,
        token=token,
        data=body,
        headers=headers,
        timeout=15.0,
    )
    return status in (200, 201, 302, 303)
