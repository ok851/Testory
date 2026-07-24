# -*- coding: utf-8 -*-
"""MCP 工具描述符（无 handler），供契约文档与离线样例使用。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def strip_handlers(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """去掉不可序列化的 handler，保留 name/description/parameters。"""
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        row = {
            "name": t.get("name"),
            "description": t.get("description") or "",
            "parameters": t.get("parameters") or {},
        }
        if t.get("risk_level"):
            row["risk_level"] = t.get("risk_level")
        out.append(row)
    return out


def desktop_tool_descriptors(*, include_vision_stubs: bool = True) -> List[Dict[str, Any]]:
    """桌面 MCP 工具 Schema 列表（不连接真机）。"""
    from testory_mcp.kit import mcp_windows_desktop_tools

    tools = list(mcp_windows_desktop_tools())
    if include_vision_stubs:
        # 与 mcp_kit_for_port(desktop) 对齐的视觉工具名（无 handler）
        plat = "desktop"
        tools.extend(
            [
                {
                    "name": f"{plat}_screenshot",
                    "description": "截取当前画面 PNG（base64）",
                    "parameters": {},
                    "risk_level": "L0",
                },
                {
                    "name": f"{plat}_tap",
                    "description": "按自然语言描述点击元素",
                    "parameters": {"locate": "str"},
                    "risk_level": "L1",
                },
                {
                    "name": f"{plat}_input",
                    "description": "按描述定位输入框并输入文字",
                    "parameters": {"locate": "str", "text": "str"},
                    "risk_level": "L1",
                },
                {
                    "name": f"{plat}_assert",
                    "description": "画面自然语言断言",
                    "parameters": {"condition": "str"},
                    "risk_level": "L1",
                },
            ]
        )
    # 只读观察类标 L0
    for t in tools:
        name = str(t.get("name") or "")
        if name.startswith("get_screen_") or name.endswith("_screenshot"):
            t.setdefault("risk_level", "L0")
        else:
            t.setdefault("risk_level", "L1")
    return strip_handlers(tools)


def stdio_request(tool: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """stdio 行协议请求信封（testory_mcp.desktop / web）。"""
    return {"tool": tool, "params": params or {}}


def stdio_response_ok(result: Any) -> Dict[str, Any]:
    return {"result": result}


def stdio_response_err(error: str) -> Dict[str, Any]:
    return {"error": str(error)}


def jsonrpc_tools_list_result(tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    """MCP tools/list 结果形状（精简）。"""
    return {
        "tools": [
            {
                "name": t["name"],
                "description": t.get("description") or "",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        k: {"type": "string" if v == "str" else "array" if v == "list" else "string"}
                        for k, v in (t.get("parameters") or {}).items()
                    },
                },
            }
            for t in tools
            if t.get("name")
        ]
    }
