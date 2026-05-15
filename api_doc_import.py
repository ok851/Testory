"""
Parse OpenAPI 2/3 and Postman Collection v2.1 into platform api_request step payloads.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def _join_url(base: str, path: str) -> str:
    b = (base or "").strip().rstrip("/")
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    if not b:
        return p
    return b + p


def _openapi_base_url_v3(spec: Dict[str, Any], override: str = "") -> str:
    if (override or "").strip():
        return override.strip().rstrip("/")
    servers = spec.get("servers")
    if isinstance(servers, list) and servers:
        u = (servers[0] or {}).get("url") if isinstance(servers[0], dict) else None
        if isinstance(u, str) and u.strip():
            return u.strip().rstrip("/")
    return ""


def _openapi_base_url_v2(spec: Dict[str, Any], override: str = "") -> str:
    if (override or "").strip():
        return override.strip().rstrip("/")
    schemes = spec.get("schemes")
    sch = "https"
    if isinstance(schemes, list) and schemes:
        sch = str(schemes[0]).lower().replace("://", "").split(":")[0] or "https"
    host = (spec.get("host") or "").strip()
    bp = (spec.get("basePath") or "").strip()
    if not host:
        return (bp or "").rstrip("/")
    return f"{sch}://{host}".rstrip("/") + (bp if bp.startswith("/") else "/" + bp if bp else "")


def _example_to_json_body(content: Any) -> Optional[Any]:
    """OpenAPI 3 media content object -> example or schema example."""
    if not isinstance(content, dict):
        return None
    for key in ("example", "examples"):
        ex = content.get(key)
        if key == "examples" and isinstance(ex, dict) and ex:
            first = next(iter(ex.values()), None)
            if isinstance(first, dict) and "value" in first:
                return first.get("value")
        if ex is not None and key == "example":
            return ex
    schema = content.get("schema")
    if isinstance(schema, dict) and "example" in schema:
        return schema.get("example")
    return None


def _operation_to_spec_v3(
    method: str,
    path: str,
    op: Dict[str, Any],
    base_url: str,
) -> Tuple[str, Dict[str, Any]]:
    summary = (op.get("summary") or op.get("operationId") or f"{method.upper()} {path}").strip()
    desc = (op.get("description") or "").strip()
    full_url = _join_url(base_url, path)
    spec: Dict[str, Any] = {
        "method": method.upper(),
        "url": full_url,
        "expected_status": 200,
        "timeout": 30,
        "verify_ssl": True,
        "follow_redirects": True,
        "body_type": "none",
        "headers": {"Accept": "application/json"},
    }
    rb = op.get("requestBody")
    if isinstance(rb, dict) and rb.get("content"):
        ct = rb["content"]
        if isinstance(ct, dict):
            for mt in ("application/json", "application/*+json"):
                if mt in ct:
                    body = _example_to_json_body(ct[mt])
                    if body is not None:
                        spec["body_type"] = "json"
                        spec["body_json"] = body
                    break
    return summary, spec


def _operation_to_spec_v2(
    method: str,
    path: str,
    op: Dict[str, Any],
    base_url: str,
) -> Tuple[str, Dict[str, Any]]:
    summary = (op.get("summary") or op.get("operationId") or f"{method.upper()} {path}").strip()
    full_url = _join_url(base_url, path)
    spec: Dict[str, Any] = {
        "method": method.upper(),
        "url": full_url,
        "expected_status": 200,
        "timeout": 30,
        "verify_ssl": True,
        "follow_redirects": True,
        "body_type": "none",
        "headers": {"Accept": "application/json"},
    }
    params = op.get("parameters")
    body_obj = None
    if isinstance(params, list):
        for p in params:
            if not isinstance(p, dict):
                continue
            if p.get("in") == "body" and p.get("schema"):
                body_obj = p.get("schema")
                break
    if isinstance(body_obj, dict) and "example" in body_obj:
        spec["body_type"] = "json"
        spec["body_json"] = body_obj.get("example")
    return summary, spec


def parse_openapi_dict(
    spec: Dict[str, Any],
    *,
    base_url_override: str = "",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Returns list of { 'name', 'description', 'method', 'path', 'api_spec': dict }, warnings.
    """
    warns: List[str] = []
    items: List[Dict[str, Any]] = []
    if not isinstance(spec, dict):
        return [], ["文档不是 JSON 对象"]

    ver = str(spec.get("openapi") or spec.get("swagger") or "").strip()
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return [], ["未找到 paths 节点"]

    http_methods = frozenset(
        {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    )

    if ver.startswith("3") or spec.get("openapi", "").startswith("3"):
        base = _openapi_base_url_v3(spec, base_url_override)
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for mth, op in path_item.items():
                if str(mth).lower() not in http_methods or not isinstance(op, dict):
                    continue
                summary, sp = _operation_to_spec_v3(str(mth).lower(), str(path), op, base)
                items.append(
                    {
                        "name": summary[:180],
                        "description": (op.get("description") or summary)[:2000],
                        "method": sp["method"],
                        "path": str(path),
                        "api_spec": sp,
                    }
                )
    else:
        base = _openapi_base_url_v2(spec, base_url_override)
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for mth, op in path_item.items():
                if str(mth).lower() not in http_methods or not isinstance(op, dict):
                    continue
                summary, sp = _operation_to_spec_v2(str(mth).lower(), str(path), op, base)
                items.append(
                    {
                        "name": summary[:180],
                        "description": (op.get("description") or summary)[:2000],
                        "method": sp["method"],
                        "path": str(path),
                        "api_spec": sp,
                    }
                )

    if not items:
        warns.append("未解析出任何 HTTP 操作（请确认 paths 下含 get/post 等）")
    if not base_url_override and not any(
        (it.get("api_spec") or {}).get("url", "").startswith("http") for it in items
    ):
        warns.append("未配置完整服务地址：请在导入时填写 base_url，或于 OpenAPI 中补充 servers/host")

    return items, warns


def _postman_url_to_string(u: Any) -> str:
    if u is None:
        return ""
    if isinstance(u, str):
        return u.strip()
    if not isinstance(u, dict):
        return ""
    raw = (u.get("raw") or "").strip()
    if raw:
        return raw
    proto = str(u.get("protocol") or "https").split(":")[0]
    host_parts = u.get("host")
    if isinstance(host_parts, list):
        host = ".".join(str(x) for x in host_parts)
    else:
        host = str(host_parts or "")
    port = u.get("port")
    if port:
        host = f"{host}:{port}"
    path_parts = u.get("path")
    if isinstance(path_parts, list):
        path = "/" + "/".join(str(x) for x in path_parts if x is not None)
    else:
        path = str(path_parts or "")
    if not host:
        return path or ""
    query = u.get("query")
    qs = ""
    if isinstance(query, list):
        parts = []
        for q in query:
            if isinstance(q, dict) and q.get("key") is not None:
                parts.append(f"{q.get('key')}={q.get('value', '')}")
        if parts:
            qs = "?" + "&".join(parts)
    return f"{proto}://{host}{path}{qs}"


def _flatten_postman_items(
    node: Any,
    out: List[Dict[str, Any]],
    warns: List[str],
    folder: str = "",
) -> None:
    if not isinstance(node, list):
        return
    for it in node:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "request").strip()
        sub = it.get("item")
        if isinstance(sub, list):
            prefix = f"{folder}/{name}" if folder else name
            _flatten_postman_items(sub, out, warns, prefix)
            continue
        req = it.get("request")
        if not isinstance(req, dict):
            continue
        method = str(req.get("method") or "GET").upper()
        url_s = _postman_url_to_string(req.get("url"))
        headers_in = req.get("header")
        headers: Dict[str, str] = {}
        if isinstance(headers_in, list):
            for h in headers_in:
                if isinstance(h, dict) and not h.get("disabled"):
                    k = (h.get("key") or "").strip()
                    if k:
                        headers[k] = str(h.get("value") or "")
        body = req.get("body")
        spec: Dict[str, Any] = {
            "method": method,
            "url": url_s,
            "expected_status": 200,
            "timeout": 30,
            "verify_ssl": True,
            "follow_redirects": True,
            "body_type": "none",
            "headers": headers or {"Accept": "application/json"},
        }
        if isinstance(body, dict):
            mode = str(body.get("mode") or "").lower()
            if mode == "raw":
                spec["body_type"] = "raw"
                spec["body_raw"] = str(body.get("raw") or "")
                spec["raw_content_type"] = str(
                    (body.get("options") or {}).get("raw", {}).get("language") or "text/plain"
                )
            elif mode == "urlencoded":
                spec["body_type"] = "form"
                pairs = body.get("urlencoded")
                bf: Dict[str, str] = {}
                if isinstance(pairs, list):
                    for p in pairs:
                        if isinstance(p, dict) and p.get("key"):
                            bf[str(p["key"])] = str(p.get("value") or "")
                spec["body_form"] = bf
            elif mode == "formdata":
                warns.append(f"请求「{name}」为 form-data，已跳过 body（请在平台内手动配置）")
        label = f"{folder}/{name}" if folder else name
        out.append(
            {
                "name": label[:200],
                "description": (it.get("description") or name)[:2000],
                "method": method,
                "path": url_s[:500],
                "api_spec": spec,
            }
        )


def parse_postman_collection_dict(
    data: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    warns: List[str] = []
    items: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return [], ["集合不是 JSON 对象"]
    if "collection" in data and isinstance(data["collection"], dict):
        data = data["collection"]
    root_items = data.get("item")
    if not isinstance(root_items, list):
        return [], ["未找到 item 数组（非 Postman Collection v2.1？）"]
    _flatten_postman_items(root_items, items, warns, "")
    if not items:
        warns.append("集合中未找到可导入的请求项")
    return items, warns


def detect_and_parse_api_doc(
    text: str,
    *,
    base_url_override: str = "",
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """
    Returns (kind, items, warnings) where kind is 'openapi' | 'postman' | 'unknown'.
    """
    raw = (text or "").strip()
    if not raw:
        return "unknown", [], ["内容为空"]
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        return "unknown", [], [f"JSON 解析失败: {e}"]

    if isinstance(obj, dict):
        if "info" in obj and isinstance(obj["info"], dict):
            if "item" in obj and isinstance(obj["item"], list):
                items, w = parse_postman_collection_dict(obj)
                return "postman", items, w
        if "openapi" in obj or "swagger" in obj or (
            isinstance(obj.get("paths"), dict) and ("definitions" in obj or "components" in obj)
        ):
            items, w = parse_openapi_dict(obj, base_url_override=base_url_override)
            return "openapi", items, w
        if isinstance(obj.get("paths"), dict):
            items, w = parse_openapi_dict(obj, base_url_override=base_url_override)
            return "openapi", items, w

    return "unknown", [], ["无法识别为 OpenAPI 或 Postman Collection（需顶层含 paths 或 item）"]
