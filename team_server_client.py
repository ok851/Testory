# -*- coding: utf-8 -*-
"""团队服务器 HTTP 客户端（桌面 client 模式）。"""
from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from client_config_store import get_auth_token, get_team_server_url, load_client_config, save_client_config


class TeamServerError(Exception):
    pass


_cookie_jar: Optional[http.cookiejar.CookieJar] = None


def _base_url() -> str:
    url = get_team_server_url()
    if not url:
        raise TeamServerError("未配置团队服务器地址")
    return url.rstrip("/")


def _get_cookie_jar() -> http.cookiejar.CookieJar:
    global _cookie_jar
    if _cookie_jar is None:
        _cookie_jar = http.cookiejar.CookieJar()
        cfg = load_client_config()
        cookie_str = (cfg.get("session_cookie") or "").strip()
        if cookie_str:
            try:
                for part in cookie_str.split(";"):
                    part = part.strip()
                    if "=" in part:
                        name, val = part.split("=", 1)
                        _cookie_jar.set_cookie(
                            http.cookiejar.Cookie(
                                version=0,
                                name=name.strip(),
                                value=val.strip(),
                                port=None,
                                port_specified=False,
                                domain="",
                                domain_specified=False,
                                domain_initial_dot=False,
                                path="/",
                                path_specified=True,
                                secure=False,
                                expires=None,
                                discard=True,
                                comment=None,
                                comment_url=None,
                                rest={},
                                rfc2109=False,
                            )
                        )
            except Exception:
                pass
    return _cookie_jar


def _store_cookies_from_response(resp) -> None:
    jar = _get_cookie_jar()
    jar.extract_cookies(resp, resp.geturl())
    parts = []
    for c in jar:
        parts.append(f"{c.name}={c.value}")
    if parts:
        save_client_config({"session_cookie": "; ".join(parts)})


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    token = get_auth_token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    if extra:
        h.update(extra)
    return h


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_get_cookie_jar()))


def request_json(
    method: str,
    path: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
) -> Tuple[Dict[str, Any], int]:
    if not path.startswith("/"):
        path = "/" + path
    url = _base_url() + path
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method.upper())
    try:
        with _opener().open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.strip() else {}
            return parsed, resp.status
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(err_body) if err_body.strip() else {}
        except Exception:
            parsed = {"error": str(e)}
        raise TeamServerError(parsed.get("error") or parsed.get("message") or str(e)) from e
    except Exception as e:
        raise TeamServerError(str(e)) from e


def login(username: str, password: str) -> Dict[str, Any]:
    url = _base_url() + "/api/auth/login"
    payload = json.dumps({"username": username, "password": password}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with _opener().open(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            _store_cookies_from_response(resp)
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_body)
        except Exception:
            parsed = {"error": str(e)}
        raise TeamServerError(parsed.get("error") or str(e)) from e


def get_me() -> Dict[str, Any]:
    data, _ = request_json("GET", "/api/auth/me")
    return data


def proxy_request(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], int]:
    return request_json(method, path, body=body)


def report_run_result(
    case_id: int,
    status: str,
    duration: float,
    error: str = "",
    extracted_text: str = "",
    expected_text: str = "",
    step_results: Optional[list] = None,
    screenshots: Optional[list] = None,
) -> Dict[str, Any]:
    payload = {
        "case_id": case_id,
        "status": status,
        "duration": duration,
        "error": error,
        "extracted_text": extracted_text,
        "expected_text": expected_text,
        "step_results": step_results or [],
        "screenshots": screenshots or [],
    }
    data, _ = request_json("POST", "/api/execution-jobs/report-run", body=payload)
    return data


def health_check() -> bool:
    try:
        url = _base_url() + "/api/health/ready"
        with _opener().open(url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
