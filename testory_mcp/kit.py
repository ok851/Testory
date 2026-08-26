"""Testory MCP 工具注册（Phase 4c）。"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

ToolFn = Callable[..., Any]


def mcp_windows_desktop_tools() -> List[Dict[str, Any]]:
    """语义化 Windows 桌面 + 屏幕观察工具（通用原语，供 Hermes MCP 使用）。"""
    from modules.desktop.windows_desktop_tools import dispatch_windows_or_screen_tool

    def _h(name: str):
        def _inner(**kwargs):
            return dispatch_windows_or_screen_tool(name, kwargs or {})

        return _inner

    return [
        {
            "name": "windows_focus_app",
            "description": "将指定窗口激活到前台；未运行时自动尝试启动",
            "parameters": {"app_name": "str"},
            "handler": lambda app_name="": _h("windows_focus_app")(app_name=app_name),
        },
        {
            "name": "windows_launch_app",
            "description": "启动本机应用（未运行也可），等同用例 launch_app",
            "parameters": {"app_name": "str"},
            "handler": lambda app_name="": _h("windows_launch_app")(app_name=app_name),
        },
        {
            "name": "windows_click_element",
            "description": "按自然语言描述点击屏幕元素（UIA→OCR→视觉）",
            "parameters": {"description": "str"},
            "handler": lambda description="": _h("windows_click_element")(description=description),
        },
        {
            "name": "windows_type_text",
            "description": "向当前桌面目标窗口输入文本（UIA/粘贴优先；含 capture_after）",
            "parameters": {"text": "str", "clear": "str"},
            "handler": lambda text="", clear="false": _h("windows_type_text")(
                text=text, clear=str(clear).lower() in ("1", "true", "yes", "on")
            ),
        },
        {
            "name": "windows_press_key",
            "description": "向当前桌面目标窗口按键或组合键",
            "parameters": {"key": "str"},
            "handler": lambda key="": _h("windows_press_key")(key=key),
        },
        {
            "name": "windows_wait",
            "description": "等待毫秒或等待界面稳定 condition=stable",
            "parameters": {"duration_ms": "str", "condition": "str"},
            "handler": lambda duration_ms="", condition="": _h("windows_wait")(
                duration_ms=int(duration_ms) if str(duration_ms).isdigit() else None,
                condition=condition or None,
            ),
        },
        {
            "name": "get_screen_text",
            "description": "OCR 获取屏幕可见文字及位置",
            "parameters": {"region": "str"},
            "handler": lambda region="": _h("get_screen_text")(region=region or None),
        },
        {
            "name": "get_screen_description",
            "description": "视觉模型描述当前屏幕（≤300字）",
            "parameters": {"hint": "str"},
            "handler": lambda hint="": _h("get_screen_description")(hint=hint),
        },
    ]


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
    if plat == "desktop":
        tools.extend(mcp_windows_desktop_tools())
        desc = (
            "Testory desktop automation: windows_* semantic tools + "
            "get_screen_* observation + legacy desktop_* vision tools"
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
