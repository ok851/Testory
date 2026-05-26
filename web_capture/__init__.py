# -*- coding: utf-8 -*-
"""网页元素捕获与自动化（与 desktop_* / web_dom_picker 实现隔离）。"""

from web_capture.session import (
    close_session,
    get_session_status,
    report_pick,
    start_session,
    stop_session,
    validate_session_id,
)

__all__ = [
    "start_session",
    "stop_session",
    "get_session_status",
    "report_pick",
    "close_session",
    "validate_session_id",
]
