"""
Parse OpenAPI 2/3 and Postman Collection v2.1 into platform api_request step payloads.

变量处理策略（与 Postman 行为一致）：
- URL 中的 {{baseUrl}} 在导入时解析为实际地址（否则 URL 无效）
- 其余所有 {{var}} 占位符原样保留，运行时由 db.resolve_variables() 替换
- Postman 集合中的变量自动提取，由调用方创建为用例级变量
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_VAR_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def _resolve_baseurl(text: str, base_url: str) -> str:
    """Only resolve {{baseUrl}} (case-insensitive) in text; preserve all other {{var}}."""
    if not text or not base_url:
        return text

    def repl(m: re.Match) -> str:
        key = m.group(1).strip()
        if key.lower() == "baseurl":
            return base_url.rstrip("/")
        return m.group(0)

    return _VAR_RE.sub(repl, text)


def _extract_postman_variables(data: Dict[str, Any]) -> Dict[str, str]:
    """Extract Postman collection-level variables from the 'variable' array."""
    result: Dict[str, str] = {}
    variables = data.get("variable")
    if isinstance(variables, list):
        for v in variables:
            if isinstance(v, dict):
                key = (v.get("key") or v.get("name") or "").strip()
                if key:
                    result[key] = str(v.get("value") or "")
    return result


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

    params: Dict[str, str] = {}
    parameters = op.get("parameters")
    if isinstance(parameters, list):
        for p in parameters:
            if not isinstance(p, dict):
                continue
            if p.get("in") == "query":
                pname = str(p.get("name") or "").strip()
                if pname:
                    schema = p.get("schema") or {}
                    val = p.get("example")
                    if val is None and isinstance(schema, dict):
                        val = schema.get("example")
                    params[pname] = str(val) if val is not None else ""
            elif p.get("in") == "header":
                pname = str(p.get("name") or "").strip()
                if pname:
                    schema = p.get("schema") or {}
                    val = p.get("example")
                    if val is None and isinstance(schema, dict):
                        val = schema.get("example")
                    spec.setdefault("headers", {})[pname] = str(val) if val is not None else ""
            elif p.get("in") == "body" and p.get("schema"):
                pass  # handled below via requestBody
        if params:
            spec["params"] = params

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
    params: Dict[str, str] = {}
    parameters = op.get("parameters")
    body_obj = None
    if isinstance(parameters, list):
        for p in parameters:
            if not isinstance(p, dict):
                continue
            if p.get("in") == "query":
                pname = str(p.get("name") or "").strip()
                if pname:
                    val = p.get("default")
                    if val is None:
                        val = p.get("example")
                    params[pname] = str(val) if val is not None else ""
            elif p.get("in") == "header":
                pname = str(p.get("name") or "").strip()
                if pname:
                    val = p.get("default")
                    if val is None:
                        val = p.get("example")
                    spec.setdefault("headers", {})[pname] = str(val) if val is not None else ""
            elif p.get("in") == "body" and p.get("schema"):
                body_obj = p.get("schema")
    if params:
        spec["params"] = params
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


def _postman_url_parse(u: Any, base_url: str = "") -> Tuple[str, Dict[str, str]]:
    """Parse a Postman URL object into (url_without_query, params_dict).

    Only {{baseUrl}} is resolved (to form a valid URL); all other {{var}} are preserved.
    Query params are separated into a dict, with {{var}} placeholders intact.
    """
    if u is None:
        return "", {}
    if isinstance(u, str):
        raw = _resolve_baseurl(u.strip(), base_url)
        url_part, params = _split_url_query(raw)
        return url_part, params
    if not isinstance(u, dict):
        return "", {}

    # Prefer the "raw" field (most reliable in Postman v2.1)
    raw = _resolve_baseurl((u.get("raw") or "").strip(), base_url)
    if raw:
        url_part, params = _split_url_query(raw)
        return url_part, params

    # Fall back to building from parts
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

    query = u.get("query")
    params: Dict[str, str] = {}
    if isinstance(query, list):
        for q in query:
            if isinstance(q, dict) and q.get("key") is not None and not q.get("disabled"):
                k = str(q.get("key") or "")
                if k:
                    params[k] = str(q.get("value") or "")

    if not host:
        return path or "", params
    return f"{proto}://{host}{path}", params


def _split_url_query(url: str) -> Tuple[str, Dict[str, str]]:
    """Split a URL into (url_without_query, params_dict)."""
    if not url:
        return "", {}
    qidx = url.find("?")
    if qidx < 0:
        return url, {}
    url_part = url[:qidx]
    query_str = url[qidx + 1:]
    params: Dict[str, str] = {}
    if query_str:
        for pair in query_str.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k] = v
            else:
                params[pair] = ""
    return url_part, params


def _flatten_postman_items(
    node: Any,
    out: List[Dict[str, Any]],
    warns: List[str],
    base_url: str = "",
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
            _flatten_postman_items(sub, out, warns, base_url, prefix)
            continue
        req = it.get("request")
        if not isinstance(req, dict):
            continue
        method = str(req.get("method") or "GET").upper()
        url_obj = req.get("url")
        url_s, parsed_params = _postman_url_parse(url_obj, base_url)

        # If URL still starts with {{ (no baseUrl resolved), try base_url_override
        if base_url and url_s and not url_s.startswith("http"):
            url_s = base_url.rstrip("/") + ("/" + url_s.lstrip("/") if url_s else "")

        # Headers — preserve {{var}} as-is for runtime resolution
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
        if parsed_params:
            spec["params"] = parsed_params

        # Body — preserve {{var}} as-is for runtime resolution
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
    *,
    base_url_override: str = "",
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, str]]:
    """Returns (items, warnings, variables) where variables is the Postman collection variables dict."""
    warns: List[str] = []
    items: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return [], ["集合不是 JSON 对象"], {}
    if "collection" in data and isinstance(data["collection"], dict):
        data = data["collection"]

    variables = _extract_postman_variables(data)

    # Determine effective base_url: user override > Postman baseUrl variable
    base_url = base_url_override.strip() if base_url_override else variables.get("baseUrl", "")

    root_items = data.get("item")
    if not isinstance(root_items, list):
        return [], ["未找到 item 数组（非 Postman Collection v2.1？）"], {}
    _flatten_postman_items(root_items, items, warns, base_url, "")
    if not items:
        warns.append("集合中未找到可导入的请求项")

    if not base_url and not any(
        (it.get("api_spec") or {}).get("url", "").startswith("http") for it in items
    ):
        warns.append("未检测到有效服务地址，请确认 Postman 变量 baseUrl 已定义或导入时填写 base_url")

    return items, warns, variables


def detect_and_parse_api_doc(
    text: str,
    *,
    base_url_override: str = "",
) -> Tuple[str, List[Dict[str, Any]], List[str], Dict[str, str]]:
    """
    Returns (kind, items, warnings, variables).
    variables is non-empty only for Postman collections (collection-level variables).
    """
    raw = (text or "").strip()
    if not raw:
        return "unknown", [], ["内容为空"], {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        return "unknown", [], [f"JSON 解析失败: {e}"], {}

    if isinstance(obj, dict):
        if "info" in obj and isinstance(obj["info"], dict):
            if "item" in obj and isinstance(obj["item"], list):
                items, w, variables = parse_postman_collection_dict(obj, base_url_override=base_url_override)
                return "postman", items, w, variables
        if "openapi" in obj or "swagger" in obj or (
            isinstance(obj.get("paths"), dict) and ("definitions" in obj or "components" in obj)
        ):
            items, w = parse_openapi_dict(obj, base_url_override=base_url_override)
            return "openapi", items, w, {}
        if isinstance(obj.get("paths"), dict):
            items, w = parse_openapi_dict(obj, base_url_override=base_url_override)
            return "openapi", items, w, {}

    return "unknown", [], ["无法识别为 OpenAPI 或 Postman Collection（需顶层含 paths 或 item）"], {}
