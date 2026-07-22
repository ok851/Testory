# -*- coding: utf-8 -*-
"""兼容层：原 ScreenObserver 强制注入已移除，请使用 screen_tools。"""
from __future__ import annotations

import warnings

from screen_tools import (  # noqa: F401
    capture_primary_monitor_png,
    filter_privacy,
    get_screen_description,
    get_screen_text,
    wait_screen_stable,
)


class ScreenObserver:
    """已废弃的兼容壳：仅保留最小 API，避免旧测试 import 失败。不参与工具循环注入。"""

    def __init__(self, platform_type: str = "web", interval_sec: int = 3):
        warnings.warn(
            "ScreenObserver 已废弃，请改用 screen_tools.get_screen_text / get_screen_description",
            DeprecationWarning,
            stacklevel=2,
        )
        self.platform_type = platform_type
        self.interval_sec = interval_sec
        self.full_desktop = True
        self.prefer_surface = ""
        self._last_analysis = ""

    def set_prefer_surface(self, surface: str) -> None:
        self.prefer_surface = surface or ""

    def set_full_desktop(self, enabled: bool = True) -> None:
        self.full_desktop = bool(enabled)

    def should_capture(self) -> bool:
        return False

    def capture_and_analyze_sync(self, instruction_hint: str = "", *, force: bool = True) -> str:
        r = get_screen_description(instruction_hint or "")
        self._last_analysis = (r.get("description") or "")[:300]
        return self._last_analysis

    def capture_and_analyze_async(self, instruction_hint: str = "", on_result=None) -> None:
        return None

    def pop_pending_result(self):
        return None

    def get_last_analysis(self) -> str:
        return self._last_analysis

    def get_capture_count(self) -> int:
        return 0
