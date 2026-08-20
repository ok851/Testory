# -*- coding: utf-8 -*-
"""Lightweight tool registry for execution lanes and audit metadata.

This is a compatibility-first registry: it does not yet replace the schema builders in
``ai_chat_tool_loop``, but it makes the current tool landscape explicit and auditable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


TOOL_LANES = {
    "desktop": [
        "windows_focus_app",
        "windows_launch_app",
        "windows_click_element",
        "windows_type_text",
        "desktop_type_text",
        "windows_press_key",
        "windows_wait",
    ],
    "observation": [
        "get_screen_text",
        "get_screen_description",
    ],
    "execution_agent": [
        "hermes_execute",
        "api_call",
    ],
    "planning": [
        "refine_test_plan",
    ],
    "cross_end_mobile": [
        "mobile_extract_otp",
    ],
}


REGISTERED_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "mobile_extract_otp",
        "description": (
            "等待手机本机提取短信/通知中的验证码（默认4-8位数字，优先匹配「验证码」附近）。"
            "成功返回 {sms_otp, variables:{sms_otp}}，可直接用于后续 desktop_type_text/windows_type_text 的文本参数。"
            "参数：timeout_sec=120(默认), sender_hint(可选,如「10086」), pattern(可选,自定义正则分组1)"
        ),
        "input_schema": {
            "timeout_sec": {"type": "number", "default": 120},
            "sender_hint": {"type": "string"},
            "pattern": {"type": "string"},
        },
        "tags": ["cross-end", "mobile"],
    },
    {
        "name": "desktop_type_text",
        "description": (
            "向当前已聚焦的桌面窗口输入框键入文本（支持中文 IME）。"
            "可直接引用 mobile_extract_otp 返回的 {{sms_otp}} 变量（如 text='{{sms_otp}}' 会自动替换）。"
            "参数：text(必填), clear=true/false(默认true)"
        ),
        "input_schema": {
            "text": {"type": "string", "required": True},
            "clear": {"type": "boolean", "default": True},
        },
        "tags": ["cross-end", "desktop-input"],
    },
    {
        "name": "windows_click_element",
        "description": (
            "点击桌面可见控件（按钮/链接/菜单/输入框标签）。**使用规则：** "
            "1) 若本轮尚未观察屏幕，必须先调 get_screen_text 获取屏幕文本候选；"
            "2) description 仅写短控件名，如「确定」「下一步」「登录」，禁止整句；"
            "3) 找不到元素时，必须先 get_screen_text 再用 OCR 候选匹配重试，不得直接报元素不存在。"
        ),
        "input_schema": {
            "description": {"type": "string", "required": True},
        },
        "tags": ["desktop", "click"],
    },
    {
        "name": "get_screen_text",
        "description": (
            "OCR 识别当前屏幕所有可见文字，返回文本候选列表及各自屏幕坐标。"
            "桌面操作：首次 windows_click_element / windows_type_text 前必须先调用一次，"
            "便于 Electron / DirectUI / 微信 / QQ 等无 DOM 应用用文本定位控件。"
        ),
        "input_schema": {},
        "tags": ["observation", "ocr"],
    },
]


def get_registered_tool(name: str) -> Optional[Dict[str, Any]]:
    for t in REGISTERED_TOOLS:
        if t.get("name") == name:
            return t
    return None


def known_tool_names() -> List[str]:
    names: List[str] = []
    for group in TOOL_LANES.values():
        for name in group:
            if name not in names:
                names.append(name)
    return names


def lane_for_tool(tool_name: str) -> str:
    for lane, names in TOOL_LANES.items():
        if tool_name in names:
            return lane
    return "unknown"


def describe_registry() -> Dict[str, Any]:
    return {
        "known_tools": known_tool_names(),
        "lanes": TOOL_LANES,
    }
