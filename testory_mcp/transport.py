# -*- coding: utf-8 -*-
"""
Testory MCP — JSON-RPC 2.0 Streamable HTTP Transport (minimal, no external SDK).

实现 MCP（Model Context Protocol）的 Streamable HTTP 传输层：
- POST /mcp   → JSON-RPC 2.0 请求/响应（支持批处理）
- GET  /mcp   → SSE 流，用于服务端主动推送通知（可选）

协议方法：
  initialize       → 返回服务器信息与能力
  tools/list       → 返回注册工具列表
  tools/call       → 调用指定工具
  ping             → 心跳

不依赖 mcp SDK，仅使用标准库 + Flask。

用法 1 — 独立启动（stdio → HTTP 代理模式）：
    python -m testory_mcp.transport

用法 2 — 作为 Flask Blueprint 集成到主应用：
    from testory_mcp.transport import mcp_bp, init_mcp_server
    init_mcp_server(port)           # port = VisionActionPort 实例
    app.register_blueprint(mcp_bp)
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, Response, jsonify, request, stream_with_context

# ──────────────────────────────────────────────
# JSON-RPC 2.0 协议常量
# ──────────────────────────────────────────────

JSONRPC_VERSION = "2.0"

# 标准 JSON-RPC 2.0 错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# MCP 自定义错误码（-32000 to -32099）
TOOL_NOT_FOUND = -32000
TOOL_EXECUTION_ERROR = -32001

# MCP 协议版本
MCP_PROTOCOL_VERSION = "2024-11-05"

# ──────────────────────────────────────────────
# 工具注册表（运行时初始化）
# ──────────────────────────────────────────────

_server_info: Dict[str, str] = {}
_tool_registry: Dict[str, Dict[str, Any]] = {}
_capabilities: Dict[str, Any] = {}
_sse_clients: List[Any] = []  # SSE 客户端列表
_sse_lock = threading.Lock()


def init_mcp_server(port=None, *, name: str = "testory-mcp", version: str = "1.0.0"):
    """初始化 MCP 服务器：从 kit.py 注册工具。

    Args:
        port: VisionActionPort 实例（web/desktop/mobile），为 None 时仅注册空工具表。
        name: 服务器名称
        version: 服务器版本
    """
    global _server_info, _tool_registry, _capabilities

    _server_info = {
        "name": name,
        "version": version,
    }

    _capabilities = {
        "tools": {},  # 将在下方填充
    }

    _tool_registry.clear()

    if port is not None:
        from testory_mcp.kit import mcp_kit_for_port

        desc, tools = mcp_kit_for_port(port)
        _server_info["description"] = desc
        for t in tools:
            tool_name = t.get("name") or ""
            if not tool_name:
                continue
            # 将 kit.py 的工具定义转换为 MCP tools/list 格式
            mcp_tool = _kit_tool_to_mcp_schema(t)
            _tool_registry[tool_name] = {
                "schema": mcp_tool,
                "handler": t.get("handler"),
            }


def _kit_tool_to_mcp_schema(t: Dict[str, Any]) -> Dict[str, Any]:
    """将 kit.py 的工具定义转换为 MCP tools/list 中的 JSON Schema 格式。"""
    name = t.get("name") or ""
    description = t.get("description") or ""
    raw_params = t.get("parameters") or {}

    # 构建 JSON Schema properties
    properties = {}
    required = []
    for pname, ptype in raw_params.items():
        if ptype == "str":
            properties[pname] = {"type": "string", "description": pname}
            required.append(pname)
        elif ptype == "list":
            properties[pname] = {"type": "array", "description": pname}
            required.append(pname)
        elif ptype == "int":
            properties[pname] = {"type": "integer", "description": pname}
        elif ptype == "float":
            properties[pname] = {"type": "number", "description": pname}
        elif ptype == "bool":
            properties[pname] = {"type": "boolean", "description": pname}
        else:
            properties[pname] = {"type": "string", "description": pname}

    input_schema = {
        "type": "object",
        "properties": properties,
    }
    if required:
        input_schema["required"] = required

    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
    }


# ──────────────────────────────────────────────
# JSON-RPC 2.0 请求/响应构建
# ──────────────────────────────────────────────

def _jsonrpc_result(request_id: Any, result: Any) -> Dict[str, Any]:
    """构建 JSON-RPC 2.0 成功响应。"""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "result": result,
    }


def _jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    """构建 JSON-RPC 2.0 错误响应。"""
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": err,
    }


def _jsonrpc_notification(method: str, params: Any = None) -> Dict[str, Any]:
    """构建 JSON-RPC 2.0 通知（无 id）。"""
    n = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        n["params"] = params
    return n


# ──────────────────────────────────────────────
# MCP 方法处理器
# ──────────────────────────────────────────────

def _handle_initialize(request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """处理 initialize 请求。"""
    return _jsonrpc_result(request_id, {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": _capabilities,
        "serverInfo": _server_info,
    })


def _handle_tools_list(request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """处理 tools/list 请求。"""
    tools = [_tool_registry[name]["schema"] for name in sorted(_tool_registry.keys())]
    return _jsonrpc_result(request_id, {"tools": tools})


def _handle_tools_call(request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """处理 tools/call 请求。"""
    tool_name = (params.get("name") or "").strip()
    if not tool_name:
        return _jsonrpc_error(request_id, INVALID_PARAMS, "缺少工具名称参数 'name'")

    entry = _tool_registry.get(tool_name)
    if not entry:
        return _jsonrpc_error(request_id, TOOL_NOT_FOUND, f"未知工具: {tool_name}")

    handler = entry.get("handler")
    if not handler or not callable(handler):
        return _jsonrpc_error(request_id, INTERNAL_ERROR, f"工具 {tool_name} 无可用处理器")

    arguments = params.get("arguments") or {}
    try:
        result = _invoke_handler(tool_name, handler, arguments)
        return _jsonrpc_result(request_id, {
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
            ],
        })
    except Exception as e:
        return _jsonrpc_result(request_id, {
            "content": [
                {"type": "text", "text": json.dumps({"error": str(e)}, ensure_ascii=False)}
            ],
            "isError": True,
        })


def _invoke_handler(tool_name: str, handler: Callable, arguments: Dict[str, Any]) -> Any:
    """根据工具名调用处理器，传入正确参数。"""
    if tool_name.endswith("_tap"):
        return handler(arguments.get("locate") or arguments.get("description") or "")
    elif tool_name.endswith("_input"):
        return handler(arguments.get("locate") or "", arguments.get("text") or "")
    elif tool_name.endswith("_assert"):
        return handler(arguments.get("condition") or arguments.get("description") or "")
    elif tool_name.endswith("_query"):
        return handler(arguments.get("prompt") or arguments.get("question") or "")
    elif tool_name.endswith("_run_steps"):
        return handler(arguments.get("steps") or [])
    elif tool_name.endswith("_screenshot"):
        return handler()
    else:
        # 通用：尝试传入整个 arguments
        return handler(**arguments) if arguments else handler()


def _handle_ping(request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """处理 ping 请求。"""
    return _jsonrpc_result(request_id, {})


# 方法路由表
_METHOD_HANDLERS: Dict[str, Callable] = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
    "ping": _handle_ping,
}


# ──────────────────────────────────────────────
# 单条请求处理
# ──────────────────────────────────────────────

def _process_single(request_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理单个 JSON-RPC 2.0 请求。通知（无 id）返回 None。"""
    # 校验 jsonrpc 版本
    if request_obj.get("jsonrpc") != JSONRPC_VERSION:
        rid = request_obj.get("id")
        return _jsonrpc_error(rid, INVALID_REQUEST, "jsonrpc 字段必须为 '2.0'")

    method = (request_obj.get("method") or "").strip()
    request_id = request_obj.get("id")  # 通知时无 id
    params = request_obj.get("params") or {}

    if not method:
        return _jsonrpc_error(request_id, INVALID_REQUEST, "缺少 method 字段")

    # 如果是通知（无 id），不返回响应
    is_notification = request_id is None

    handler = _METHOD_HANDLERS.get(method)
    if not handler:
        if is_notification:
            return None
        return _jsonrpc_error(request_id, METHOD_NOT_FOUND, f"未知方法: {method}")

    response = handler(request_id, params)

    # 广播 SSE 通知给所有连接的客户端
    _broadcast_sse({
        "jsonrpc": JSONRPC_VERSION,
        "method": "notifications/message",
        "params": {
            "level": "info",
            "logger": "testory-mcp",
            "data": {"method": method, "timestamp": time.time()},
        },
    })

    if is_notification:
        return None
    return response


# ──────────────────────────────────────────────
# SSE 广播
# ──────────────────────────────────────────────

def _broadcast_sse(data: Dict[str, Any]):
    """向所有 SSE 客户端广播消息。"""
    payload = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    dead = []
    with _sse_lock:
        for client in _sse_clients:
            try:
                client.put(payload)
            except Exception:
                dead.append(client)
        for d in dead:
            try:
                _sse_clients.remove(d)
            except ValueError:
                pass


# ──────────────────────────────────────────────
# Flask Blueprint — Streamable HTTP
# ──────────────────────────────────────────────

mcp_bp = Blueprint("mcp", __name__, url_prefix="/mcp")


@mcp_bp.route("", methods=["POST"])
def mcp_post():
    """
    POST /mcp — JSON-RPC 2.0 请求入口。

    Content-Type: application/json
    Accept: application/json, text/event-stream

    支持单条请求和数组批处理（JSON-RPC 2.0 Batch）。
    当 Accept 包含 text/event-stream 时，响应以 SSE 格式返回。
    """
    raw = request.get_data(as_text=True)
    if not raw:
        return jsonify(_jsonrpc_error(None, INVALID_REQUEST, "请求体为空")), 400

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        return jsonify(_jsonrpc_error(None, PARSE_ERROR, f"JSON 解析失败: {e}")), 200

    # 批处理：payload 为数组
    if isinstance(payload, list):
        if not payload:
            return jsonify(_jsonrpc_error(None, INVALID_REQUEST, "批处理数组不能为空")), 200
        results = []
        for item in payload:
            if not isinstance(item, dict):
                results.append(_jsonrpc_error(None, INVALID_REQUEST, "批处理元素必须为对象"))
                continue
            r = _process_single(item)
            if r is not None:
                results.append(r)
        if not results:
            return Response("", status=202, content_type="application/json")
        return jsonify(results), 200

    # 单条请求
    if not isinstance(payload, dict):
        return jsonify(_jsonrpc_error(None, INVALID_REQUEST, "请求必须为对象或数组")), 200

    result = _process_single(payload)
    if result is None:
        # 通知 → 202 Accepted 无响应体
        return Response("", status=202, content_type="application/json")

    # 如果客户端 Accept 中包含 text/event-stream，以 SSE 格式返回
    accept = request.headers.get("Accept", "")
    if "text/event-stream" in accept:
        def generate():
            yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return jsonify(result), 200


@mcp_bp.route("", methods=["GET"])
def mcp_get_sse():
    """
    GET /mcp — SSE 流，用于服务端推送通知。

    MCP Streamable HTTP 规范允许客户端通过 GET 打开一个 SSE 通道，
    服务端可在此通道上推送 notifications/message 等通知。
    """
    def generate():
        # 每个客户端维护自己的消息队列
        import queue

        q: queue.Queue = queue.Queue()
        with _sse_lock:
            _sse_clients.append(q)
        try:
            # 发送初始连接确认
            yield f"data: {json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    # 心跳保活
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                try:
                    _sse_clients.remove(q)
                except ValueError:
                    pass

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@mcp_bp.route("", methods=["DELETE"])
def mcp_delete():
    """
    DELETE /mcp — 客户端请求关闭会话。
    当前为最小实现，直接返回 204。
    """
    return Response("", status=204)


# ──────────────────────────────────────────────
# 独立运行（开发/调试用）
# ──────────────────────────────────────────────

def main():
    """独立启动 MCP HTTP 服务器（用于开发/调试）。

    环境变量：
      TESTORY_MCP_HTTP_PORT   监听端口（默认 9820）
      TESTORY_MCP_PLATFORM    平台类型：web/desktop/mobile（默认 web）
      TESTORY_MCP_SESSION_ID  web 平台的 session_id
    """
    import os

    port = int(os.environ.get("TESTORY_MCP_HTTP_PORT", "9820") or 9820)
    platform = (os.environ.get("TESTORY_MCP_PLATFORM") or "web").strip().lower()

    vision_port = None
    try:
        if platform == "web":
            from modules.ai.vision_action_port import WebVisionActionPort

            sid = (os.environ.get("TESTORY_MCP_SESSION_ID") or "").strip()
            if sid:
                vision_port = WebVisionActionPort(sid)
        elif platform == "desktop":
            from modules.ai.vision_action_port import DesktopVisionActionPort

            vision_port = DesktopVisionActionPort()
        elif platform == "android":
            from modules.ai.vision_action_port import MobileVisionActionPort

            udid = (os.environ.get("TESTORY_MCP_UDID") or "").strip()
            if udid:
                vision_port = MobileVisionActionPort(udid)
    except ImportError:
        sys.stderr.write("[testory-mcp] vision_action_port 不可用，仅注册空工具表\n")
    except Exception as e:
        sys.stderr.write(f"[testory-mcp] 初始化 {platform} port 失败: {e}\n")

    init_mcp_server(vision_port, name=f"testory-{platform}-mcp", version="1.0.0")

    # 使用 Flask 内置服务器（开发模式）
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(mcp_bp)

    sys.stderr.write(
        f"[testory-mcp] JSON-RPC 2.0 Streamable HTTP 服务启动\n"
        f"  端点: http://127.0.0.1:{port}/mcp\n"
        f"  平台: {platform}\n"
        f"  工具数: {len(_tool_registry)}\n"
        f"  方法: POST (JSON-RPC), GET (SSE), DELETE (关闭)\n"
    )

    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
