# -*- coding: utf-8 -*-
"""
网页 DOM 捕获器（薄适配层 → web_capture.session）。

legacy_inject 模式保留 /api/web-dom-picker/* 兼容。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from web_capture.locator_generator import format_dom_pick_payload
from web_capture.session import (
    close_session,
    get_session_status,
    report_pick,
    start_session,
    stop_session,
    validate_session_id,
)

# 向后兼容：供旧代码 from web_dom_picker import format_dom_pick_payload
from web_capture.locator_generator import (  # noqa: F401
    format_dom_pick_payload,
    looks_dynamic_dom_id as _looks_dynamic_dom_id,
)


def web_dom_picker_available() -> bool:
    return True


def start_web_dom_picker(
    *,
    record_mode: bool = False,
    case_id: Optional[int] = None,
    platform_origin: str = "",
    web_capture_mode: str = "cdp",
    browser: str = "edge",
    start_url: str = "",
) -> Dict[str, Any]:
    mode = (web_capture_mode or "cdp").strip().lower()
    if mode not in ("cdp", "extension", "legacy_inject"):
        mode = "cdp"
    return start_session(
        mode=mode,
        record_mode=record_mode,
        case_id=case_id,
        platform_origin=platform_origin,
        browser=browser,
        start_url=start_url,
    )


def report_web_dom_pick(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return report_pick(session_id, payload)


def close_web_dom_picker_session(session_id: str) -> Dict[str, Any]:
    return close_session(session_id)


def stop_web_dom_picker(*, fast: bool = False) -> Dict[str, Any]:
    return stop_session(fast=fast)


def get_web_dom_picker_status(*, consume_last_pick: bool = False) -> Dict[str, Any]:
    return get_session_status(consume_last_pick=consume_last_pick)


def sync_start_web_dom_picker(**kwargs: Any) -> Dict[str, Any]:
    return start_web_dom_picker(**kwargs)


def sync_stop_web_dom_picker(**kwargs: Any) -> Dict[str, Any]:
    return stop_web_dom_picker(**kwargs)


def sync_get_web_dom_picker_status(**kwargs: Any) -> Dict[str, Any]:
    return get_web_dom_picker_status(**kwargs)


def sync_report_web_dom_pick(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return report_web_dom_pick(session_id, payload)


__all__ = [
    "format_dom_pick_payload",
    "validate_session_id",
    "start_web_dom_picker",
    "report_web_dom_pick",
    "get_web_dom_picker_status",
    "stop_web_dom_picker",
    "web_dom_picker_available",
]
