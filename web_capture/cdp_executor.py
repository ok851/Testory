# -*- coding: utf-8 -*-
"""CDP 模式运行时步骤执行。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

from web_capture import cdp_browser
from web_capture.playwright_locator import convert_selector
from web_capture.validator import _resolve_locator, verify_element_async

_log = logging.getLogger("uat.web_capture.executor")


def cdp_exec_enabled(step: Optional[Dict[str, Any]] = None) -> bool:
    mode = (os.environ.get("WEB_CAPTURE_EXEC_MODE") or "").strip().lower()
    if mode == "cdp":
        return True
    if step:
        cm = (step.get("capture_mode") or step.get("record_meta", {}).get("capture_mode") or "")
        if str(cm).lower() == "cdp":
            return True
    return False


async def _ensure_page(url: str = ""):
    page = cdp_browser.get_active_page()
    if page:
        return page
    port = int(os.environ.get("WEB_CAPTURE_CDP_PORT", "9222") or 9222)
    conn = cdp_browser.connect_playwright_over_cdp(port)
    if not conn.get("success"):
        raise RuntimeError(conn.get("error") or "CDP 未连接")
    page = cdp_browser.get_active_page()
    if url and page:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    return page


async def click_async(
    selector_type: str,
    selector_value: str,
    *,
    double: bool = False,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    page = await _ensure_page()
    loc = await _resolve_locator(page, selector_type, selector_value)
    await loc.first.wait_for(state="visible", timeout=timeout_ms)
    if double:
        await loc.first.dblclick(timeout=timeout_ms)
    else:
        await loc.first.click(timeout=timeout_ms)
    ms = int((time.perf_counter() - t0) * 1000)
    _log.info("cdp click ok %s=%s %sms", selector_type, selector_value[:60], ms)
    return {"success": True, "ms": ms}


async def fill_async(
    selector_type: str,
    selector_value: str,
    text: str,
    *,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    page = await _ensure_page()
    loc = await _resolve_locator(page, selector_type, selector_value)
    await loc.first.wait_for(state="visible", timeout=timeout_ms)
    await loc.first.fill(str(text or ""), timeout=timeout_ms)
    ms = int((time.perf_counter() - t0) * 1000)
    _log.info("cdp fill ok %sms", ms)
    return {"success": True, "ms": ms}


async def get_text_async(
    selector_type: str,
    selector_value: str,
    *,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    page = await _ensure_page()
    loc = await _resolve_locator(page, selector_type, selector_value)
    await loc.first.wait_for(state="attached", timeout=timeout_ms)
    text = await loc.first.inner_text(timeout=timeout_ms)
    return {"success": True, "text": text}


async def verify_async(selector_type: str, selector_value: str) -> Dict[str, Any]:
    page = await _ensure_page()
    return await verify_element_async(page, selector_type, selector_value)


def sync_click(selector_type: str, selector_value: str, **kwargs) -> Dict[str, Any]:
    return asyncio.run(click_async(selector_type, selector_value, **kwargs))


def sync_fill(selector_type: str, selector_value: str, text: str, **kwargs) -> Dict[str, Any]:
    return asyncio.run(fill_async(selector_type, selector_value, text, **kwargs))
