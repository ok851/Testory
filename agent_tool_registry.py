# -*- coding: utf-8 -*-
"""Lightweight tool registry for execution lanes and audit metadata.

This is a compatibility-first registry: it does not yet replace the schema builders in
``ai_chat_tool_loop``, but it makes the current tool landscape explicit and auditable.
"""

from __future__ import annotations

from typing import Any, Dict, List


TOOL_LANES = {
    "desktop": [
        "windows_focus_app",
        "windows_launch_app",
        "windows_click_element",
        "windows_type_text",
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
}


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
