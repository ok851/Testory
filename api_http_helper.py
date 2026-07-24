"""
共享 HTTP 请求与 JSON 路径解析，供 api_request 步骤与 assertion_engine 使用。
"""
import base64
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests


# 不参与实际 HTTP 请求的 api_spec 扩展字段（Postman 风格：前置链、脚本、提取等）
API_SPEC_META_KEYS = frozenset(
    {
        "prerequest_chain",
        "prescript",
        "postscript",
        "pre_request_script",
        "post_request_script",
        "extract",
        "extract_variables",
        "persist_extracts_to_case",
    }
)


def get_json_path_value(data: Any, json_path: str) -> Any:
    """点分路径，如 data.user.name 或 list.0.id；空路径表示根对象。

    兼容 JSONPath 常见前缀 ``$.id`` / ``$``。
    """
    if json_path is None or not str(json_path).strip():
        return data
    path = str(json_path).strip()
    if path == "$":
        return data
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:].lstrip(".")
    actual: Any = data
    for key in path.split("."):
        if not key:
            continue
        if isinstance(actual, dict):
            actual = actual.get(key)
        elif isinstance(actual, list) and key.isdigit():
            actual = actual[int(key)]
        else:
            return None
    return actual


def playwright_cookies_to_requests_cookiejar(
    cookies: List[Dict[str, Any]], url: str
) -> requests.cookies.RequestsCookieJar:
    jar = requests.cookies.RequestsCookieJar()
    for c in cookies or []:
        name = c.get("name")
        value = c.get("value")
        if not name:
            continue
        domain = c.get("domain") or ""
        path = c.get("path") or "/"
        jar.set(name, value or "", domain=domain, path=path)
    return jar


def _apply_spec_auth_to_headers(
    spec: Dict[str, Any], headers: Dict[str, str], rt: Callable[[str], str]
) -> None:
    """认证配置写入 Authorization（覆盖 Headers 中的同名项，与 Postman 行为一致）。"""
    auth = (spec.get("auth_type") or "none").lower().strip()
    if auth == "bearer":
        tok = rt(str(spec.get("bearer_token") or "").strip())
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
    elif auth == "basic":
        user = rt(str(spec.get("basic_username") or ""))
        password = rt(str(spec.get("basic_password") or ""))
        pair = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {pair}"


def perform_http_request(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
    json_body: Any = None,
    data: Any = None,
    raw_body: Optional[str] = None,
    timeout: float = 30,
    verify: bool = True,
    cookies: Optional[requests.cookies.RequestsCookieJar] = None,
    allow_redirects: bool = True,
) -> Tuple[requests.Response, Optional[Any]]:
    """
    发送 HTTP 请求。
    - json_body: dict/list 时作为 JSON 发送
    - raw_body: 原始字符串（Content-Type 需在 headers 中指定）
    - data: application/x-www-form-urlencoded 等可传给 requests data=
    """
    headers = headers or {}
    kwargs: Dict[str, Any] = {
        "method": method.upper(),
        "url": url,
        "headers": headers,
        "timeout": timeout,
        "verify": verify,
        "allow_redirects": allow_redirects,
    }
    if params:
        kwargs["params"] = params
    if cookies is not None:
        kwargs["cookies"] = cookies

    if raw_body is not None:
        kwargs["data"] = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
    elif json_body is not None:
        kwargs["json"] = json_body
    elif data is not None:
        kwargs["data"] = data

    resp = requests.request(**kwargs)
    parsed_json = None
    try:
        parsed_json = resp.json()
    except (ValueError, json.JSONDecodeError):
        pass
    return resp, parsed_json


def execute_api_spec_sync(
    spec: Dict[str, Any],
    resolve_text: Optional[Callable[[str], str]] = None,
    browser_cookie_jar: Optional[requests.cookies.RequestsCookieJar] = None,
) -> Dict[str, Any]:
    """
    执行 api_request 步骤规格（已解析为 dict）。
    resolve_text: 可选，对字符串字段做 {{变量}} 替换。
    返回: status_code, response_text, response_json, ok_assert, assert_message, error
    """
    rt = resolve_text or (lambda x: x)

    spec = dict(spec) if isinstance(spec, dict) else {}
    # 内部标记：前置链子请求等场景下接受任意 2xx，避免误拷主请求的 200 断言导致链失败、主请求从未发出
    accept_2xx_for_status = bool(spec.pop("accept_2xx_for_status", False))
    if API_SPEC_META_KEYS.intersection(spec):
        spec = {k: v for k, v in spec.items() if k not in API_SPEC_META_KEYS}

    method = (spec.get("method") or "GET").upper()
    url = rt((spec.get("url") or "").strip())
    if not url:
        return {
            "status_code": None,
            "response_text": "",
            "response_json": None,
            "response_headers": {},
            "elapsed_ms": 0,
            "ok_assert": False,
            "assert_message": "URL 为空",
            "error": "URL 为空",
        }

    headers_in = spec.get("headers") or {}
    if isinstance(headers_in, str):
        try:
            headers_in = json.loads(headers_in)
        except json.JSONDecodeError:
            headers_in = {}
    headers: Dict[str, str] = {}
    for k, v in (headers_in if isinstance(headers_in, dict) else {}).items():
        headers[str(k)] = rt(str(v))

    _apply_spec_auth_to_headers(spec, headers, rt)

    params_in = spec.get("params") or spec.get("query") or {}
    if isinstance(params_in, str):
        try:
            params_in = json.loads(params_in)
        except json.JSONDecodeError:
            params_in = {}
    params: Dict[str, str] = {}
    for k, v in (params_in if isinstance(params_in, dict) else {}).items():
        params[str(k)] = rt(str(v))

    _bt = spec.get("body_type")
    if _bt is None or (isinstance(_bt, str) and not str(_bt).strip()):
        body_type = "json"
    else:
        body_type = str(_bt).lower()
    timeout = float(spec.get("timeout") or 30)
    verify = bool(spec.get("verify_ssl", True))

    json_body = None
    data = None
    raw_body = None
    if body_type == "none" or body_type == "":
        pass
    elif body_type == "json":
        jb = spec.get("body_json")
        if isinstance(jb, str):
            jb = rt(jb)
            try:
                json_body = json.loads(jb) if jb.strip() else None
            except json.JSONDecodeError as e:
                return {
                    "status_code": None,
                    "response_text": "",
                    "response_json": None,
                    "response_headers": {},
                    "elapsed_ms": 0,
                    "ok_assert": False,
                    "assert_message": f"body JSON 无效: {e}",
                    "error": str(e),
                }
        elif isinstance(jb, (dict, list)):
            json_body = jb
        else:
            json_body = None
    elif body_type == "form":
        fd = spec.get("body_form") or {}
        if isinstance(fd, str):
            try:
                fd = json.loads(fd)
            except json.JSONDecodeError:
                fd = {}
        data = {str(k): rt(str(v)) for k, v in (fd if isinstance(fd, dict) else {}).items()}
    elif body_type == "raw":
        raw_body = rt(spec.get("body_raw") or "")
        rct = (spec.get("raw_content_type") or "").strip()
        if rct:
            headers.setdefault("Content-Type", rt(rct))

    cookies = browser_cookie_jar
    allow_redirects = spec.get("follow_redirects")
    if allow_redirects is None:
        allow_redirects = True
    else:
        allow_redirects = bool(allow_redirects)

    pdm = spec.get("pre_delay_ms")
    if pdm is not None:
        try:
            ms = float(pdm)
            if ms > 0:
                time.sleep(min(ms / 1000.0, 60.0))
        except (TypeError, ValueError):
            pass

    t0 = time.perf_counter()
    try:
        resp, parsed_json = perform_http_request(
            method,
            url,
            headers=headers,
            params=params or None,
            json_body=json_body,
            data=data,
            raw_body=raw_body,
            timeout=timeout,
            verify=verify,
            cookies=cookies,
            allow_redirects=allow_redirects,
        )
    except requests.RequestException as e:
        return {
            "status_code": None,
            "response_text": "",
            "response_json": None,
            "response_headers": {},
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "ok_assert": False,
            "assert_message": f"请求失败: {e}",
            "error": str(e),
        }

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    resp_headers = {str(k): str(v) for k, v in (resp.headers or {}).items()}
    text_preview = (resp.text or "")[:8000]
    if accept_2xx_for_status:
        ok = 200 <= int(resp.status_code) < 300
        msg = f"HTTP {resp.status_code}（期望 2xx）"
    else:
        expected_status = spec.get("expected_status")
        if expected_status is None:
            expected_status = 200
        try:
            expected_status = int(expected_status)
        except (TypeError, ValueError):
            expected_status = 200

        ok = resp.status_code == expected_status
        msg = f"HTTP {resp.status_code}（期望 {expected_status}）"

    if "expected_json_value" in spec:
        expected_json_value = spec.get("expected_json_value")
        json_path = (spec.get("json_path") or "").strip()
        if parsed_json is None:
            ok = False
            msg = "响应不是合法 JSON，无法进行 JSON 断言"
        else:
            actual_j = get_json_path_value(parsed_json, json_path)
            ok = ok and (actual_j == expected_json_value)
            lbl = json_path if json_path else "(根)"
            msg = f"{msg}；JSON {lbl} 实际={actual_j!r} 期望={expected_json_value!r}"

    err = None if ok else msg
    return {
        "status_code": resp.status_code,
        "response_text": text_preview,
        "response_json": parsed_json,
        "response_headers": resp_headers,
        "elapsed_ms": elapsed_ms,
        "ok_assert": ok,
        "assert_message": msg,
        "error": err,
    }


def substitute_env_placeholders(text: str) -> str:
    """将 {{ENV:NAME}} 替换为 os.environ.get('NAME','')。"""
    if not text or "{{ENV:" not in text:
        return text
    import os
    import re

    def repl(m):
        return os.environ.get(m.group(1).strip(), "")

    return re.sub(r"\{\{\s*ENV:([^}]+)\}\}", repl, text)
