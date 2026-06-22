"""Testory MCP 工具注册（Phase 4c）。"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

ToolFn = Callable[..., Any]


def mcp_kit_for_port(port) -> Tuple[str, List[Dict[str, Any]]]:
    """返回 (server_description, tools) 供 web/mobile/desktop MCP 注册。"""
    plat = getattr(port, "platform", "web")
    desc = f"Testory {plat} vision automation (capture/ground/tap/input/assert/run_steps)"
    tools = [
        {
            "name": f"{plat}_screenshot",
            "description": "截取当前画面 PNG（base64）",
            "handler": lambda: _b64(port.capture().png_bytes),
        },
        {
            "name": f"{plat}_tap",
            "description": "按自然语言描述点击元素",
            "parameters": {"locate": "str"},
            "handler": lambda locate: port.tap(locate).__dict__,
        },
        {
            "name": f"{plat}_input",
            "description": "按描述定位输入框并输入文字",
            "parameters": {"locate": "str", "text": "str"},
            "handler": lambda locate, text: port.input_text(locate, text).__dict__,
        },
        {
            "name": f"{plat}_assert",
            "description": "画面自然语言断言",
            "parameters": {"condition": "str"},
            "handler": lambda condition: port.assert_vision(condition).__dict__,
        },
    ]
    if hasattr(port, "query"):
        tools.append(
            {
                "name": f"{plat}_query",
                "description": "从当前画面读取信息（自然语言提问）",
                "parameters": {"prompt": "str"},
                "handler": lambda prompt: _query_result(port, prompt),
            }
        )
    if hasattr(port, "run_steps"):
        tools.append(
            {
                "name": f"{plat}_run_steps",
                "description": "串行执行 JSON 步骤数组",
                "parameters": {"steps": "list"},
                "handler": lambda steps: port.run_steps(steps),
            }
        )
    return desc, tools


def _b64(png: bytes) -> Dict[str, Any]:
    import base64

    return {"format": "png", "data": base64.b64encode(png).decode("ascii")}


def _query_result(port, prompt: str) -> Dict[str, Any]:
    text, err = port.query(prompt or "")
    if err or not text:
        return {"ok": False, "error": err or "未能读取画面信息"}
    return {"ok": True, "data": text}
