# -*- coding: utf-8 -*-
"""桌面用例逐步执行时的跨步骤上下文（launch → attach → verify 贯通）。"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


_LAUNCH_WINDOW_HINTS: Dict[str, List[str]] = {
    "control": ["控制面板", "Control Panel", "All Control Panel Items"],
    "control.exe": ["控制面板", "Control Panel"],
    "notepad": ["记事本", "Notepad", "无标题 - 记事本"],
    "notepad.exe": ["记事本", "Notepad"],
    "calc": ["计算器", "Calculator"],
    "calc.exe": ["计算器", "Calculator"],
    "mspaint": ["画图", "Paint"],
    "mspaint.exe": ["画图", "Paint"],
    "explorer": ["文件资源管理器", "Explorer"],
    "explorer.exe": ["文件资源管理器", "Explorer"],
}


@dataclass
class DesktopRunContext:
    attached_hwnd: int = 0
    last_launch_value: str = ""
    last_window_title_hint: str = ""
    last_action: str = ""

    def remember_launch(self, launch_value: str, hwnd: int = 0, title_hint: str = "") -> None:
        self.last_launch_value = (launch_value or "").strip()
        self.last_action = "launch_app"
        hints = window_hints_for_launch(self.last_launch_value)
        self.last_window_title_hint = (title_hint or (hints[0] if hints else "")).strip()
        if hwnd:
            self.attached_hwnd = int(hwnd)

    def remember_attach(self, hwnd: int, title: str = "") -> None:
        self.last_action = "attach_window"
        if hwnd:
            self.attached_hwnd = int(hwnd)
        if title:
            self.last_window_title_hint = title.strip()


_local = threading.local()


def get_desktop_run_context() -> DesktopRunContext:
    ctx = getattr(_local, "ctx", None)
    if ctx is None:
        ctx = DesktopRunContext()
        _local.ctx = ctx
    return ctx


def reset_desktop_run_context() -> None:
    _local.ctx = DesktopRunContext()


def window_hints_for_launch(launch_value: str) -> List[str]:
    key = (launch_value or "").strip().lower()
    if not key:
        return []
    base = key.replace("\\", "/").split("/")[-1]
    for candidate in (key, base):
        if candidate in _LAUNCH_WINDOW_HINTS:
            return list(_LAUNCH_WINDOW_HINTS[candidate])
    return []


def spec_has_window_target(spec: Optional[Dict[str, Any]]) -> bool:
    s = spec or {}
    return bool(
        s.get("hwnd")
        or s.get("window_title")
        or s.get("window_title_re")
        or s.get("title_contains")
        or s.get("title")
        or s.get("process")
        or s.get("path")
    )


def guess_window_title_from_description(desc: str) -> str:
    d = (desc or "").strip()
    if not d:
        return ""
    if "控制面板" in d:
        return "控制面板"
    if "记事本" in d:
        return "记事本"
    if "计算器" in d:
        return "计算器"
    if "资源管理器" in d or "文件管理" in d:
        return "文件资源管理器"
    return ""


def enrich_desktop_step_with_run_context(
    step: Dict[str, Any],
    ctx: Optional[DesktopRunContext] = None,
) -> Dict[str, Any]:
    """执行前：用上下文与前序 launch 补全 attach/verify 的窗口定位。"""
    import copy
    import json

    s = copy.deepcopy(step)
    action = (s.get("action") or "").strip().lower()
    if action not in ("attach_window", "verify", "assert", "wait"):
        return s

    raw = s.get("desktop_spec")
    if isinstance(raw, str) and raw.strip():
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError:
            spec = {}
    elif isinstance(raw, dict):
        spec = dict(raw)
    else:
        spec = {}

    c = ctx or get_desktop_run_context()
    title = (
        (s.get("selector_value") or "").strip()
        or guess_window_title_from_description(s.get("description") or "")
        or (spec.get("title_contains") or spec.get("title") or "").strip()
        or c.last_window_title_hint
    )
    if title and title.lower() in ("exist", "visible", "clickable", "auto"):
        title = c.last_window_title_hint or guess_window_title_from_description(s.get("description") or "")

    if not spec_has_window_target(spec) and title:
        spec["title_contains"] = title
        spec["window_title_re"] = f".*{re.escape(title)}.*"

    if action == "attach_window" and not spec_has_window_target(spec) and c.attached_hwnd:
        spec["hwnd"] = int(c.attached_hwnd)

    if spec:
        s["desktop_spec"] = spec
    if action in ("verify", "assert") and title and not (s.get("selector_value") or "").strip():
        s["selector_type"] = (s.get("selector_type") or "window").strip() or "window"
        s["selector_value"] = title
    return s


def update_context_from_step_result(
    step: Dict[str, Any],
    result: Dict[str, Any],
    ctx: Optional[DesktopRunContext] = None,
) -> None:
    if not isinstance(result, dict):
        return
    c = ctx or get_desktop_run_context()
    action = (step.get("action") or "").strip().lower()
    hwnd = int(result.get("hwnd") or 0)
    title = (result.get("window_title") or result.get("message") or "").strip()
    if action == "launch_app":
        launch_val = step.get("input_value") or ""
        if not title and launch_val:
            hints = window_hints_for_launch(launch_val)
            if hints:
                title = hints[0]
        c.remember_launch(launch_val, hwnd=hwnd, title_hint=title)
    elif action == "attach_window" and hwnd:
        c.remember_attach(hwnd, title=title)
    elif hwnd:
        c.attached_hwnd = hwnd
