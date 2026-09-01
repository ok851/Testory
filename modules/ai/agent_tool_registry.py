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
        "windows_get_ui_tree",
    ],
    "observation": [
        "get_screen_text",
        "get_screen_description",
        "windows_get_ui_tree",
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
        "mobile_scrcpy_screenshot",
        "mobile_scrcpy_extract_otp",
        "mobile_run_steps",
        "mobile_run_case",
        "mobile_tap",
        "mobile_swipe",
        "mobile_input",
        "mobile_back",
        "mobile_home",
        "mobile_get_ui_tree",
        "mobile_get_screen_text",
        "mobile_await_notification",
    ],
}


REGISTERED_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "mobile_extract_otp",
        "description": (
            "等待手机本机提取短信/通知中的验证码（默认4-8位数字，优先匹配「验证码」附近）。"
            "优先走 scrcpy 视觉（截图+OCR，2-5秒），失败后走 APK 通知监听兜底。"
            "成功返回 {sms_otp, variables:{sms_otp}}，可直接用于后续 desktop_type_text/windows_type_text 的文本参数。"
            "参数：timeout_sec=120(默认), sender_hint(可选,如「10086」), pattern(可选,自定义正则分组1)"
        ),
        "input_schema": {
            "timeout_sec": {"type": "number", "default": 120},
            "sender_hint": {"type": "string"},
            "pattern": {"type": "string"},
        },
        "tags": ["cross-end", "mobile", "scrcpy-vision"],
    },
    {
        "name": "mobile_scrcpy_screenshot",
        "description": (
            "快速截取已配对手机屏幕并 OCR 识别文字（scrcpy 视觉路径，亚秒级）。"
            "用于多端联动中快速获取手机屏幕内容，无需 APK 轮询。"
            "返回 texts（OCR 文本列表）和 text_joined（合并文本）。"
        ),
        "input_schema": {
            "serial": {"type": "string"},
        },
        "tags": ["cross-end", "mobile", "scrcpy-vision", "observation"],
    },
    {
        "name": "mobile_scrcpy_extract_otp",
        "description": (
            "通过通知栏/屏幕 OCR 快速提取验证码（优先 dumpsys notification，"
            "再下拉通知栏 OCR；不盲目打开短信外其它应用）。"
            "速度远快于 mobile_extract_otp 的 APK 轮询路径。"
        ),
        "input_schema": {
            "sender_hint": {"type": "string"},
            "pattern": {"type": "string"},
            "timeout_sec": {"type": "number", "default": 30},
        },
        "tags": ["cross-end", "mobile", "scrcpy-vision"],
    },
    {
        "name": "mobile_tap",
        "description": (
            "在已配对/ADB 连接手机上点击。可按文案定位或直接给 x/y。"
            "通道：scrcpy 注入 → ADB；不依赖 APK poller。"
        ),
        "input_schema": {
            "description": {"type": "string"},
            "text": {"type": "string"},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "serial": {"type": "string"},
        },
        "tags": ["cross-end", "mobile", "action"],
    },
    {
        "name": "mobile_swipe",
        "description": "在手机上滑动（x1,y1→x2,y2）。通道：scrcpy 注入 → ADB。",
        "input_schema": {
            "x1": {"type": "integer", "required": True},
            "y1": {"type": "integer", "required": True},
            "x2": {"type": "integer", "required": True},
            "y2": {"type": "integer", "required": True},
            "duration_ms": {"type": "integer"},
        },
        "tags": ["cross-end", "mobile", "action"],
    },
    {
        "name": "mobile_input",
        "description": "向手机输入文本；ASCII 可走 ADB，中文走 APK 本机输入。",
        "input_schema": {
            "text": {"type": "string", "required": True},
            "description": {"type": "string"},
        },
        "tags": ["cross-end", "mobile", "action"],
    },
    {
        "name": "mobile_get_ui_tree",
        "description": "获取手机当前 UI 层级树（紧凑文本），供 mobile_tap(description=) 定位。",
        "input_schema": {"serial": {"type": "string"}, "max_nodes": {"type": "integer"}},
        "tags": ["cross-end", "mobile", "observation"],
    },
    {
        "name": "mobile_get_screen_text",
        "description": "截图手机屏幕并 OCR 可见文字（快速感知）。",
        "input_schema": {"serial": {"type": "string"}},
        "tags": ["cross-end", "mobile", "observation", "ocr"],
    },
    {
        "name": "mobile_run_steps",
        "description": "把 Android 步骤 enqueue 给已配对手机本机回放（需 APK poller 心跳）。",
        "input_schema": {"steps": {"type": "array", "required": True}, "timeout_sec": {"type": "number"}},
        "tags": ["cross-end", "mobile", "apk-job"],
    },
    {
        "name": "mobile_run_case",
        "description": "按 PC 用例库 case_id 在手机本机执行（需 APK poller）。",
        "input_schema": {"case_id": {"type": "integer", "required": True}},
        "tags": ["cross-end", "mobile", "apk-job"],
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
