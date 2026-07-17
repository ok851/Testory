"""
外部有头浏览器桥接层。
替代嵌入式画布，提供 CDP 连接、页面快照、DOM 探测、截图能力。
Hermes 通过 sync_hermes_cdp_endpoint() attach 到同一浏览器。
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from logger import uat_logger

_bridge_lock = threading.Lock()
_browser = None       # Playwright Browser 实例
_context = None       # BrowserContext
_page = None          # 当前活跃 Page
_cdp_ws = ""          # CDP WebSocket URL
_last_screenshot = b""  # 最新截图缓存
_screen_share_active = False  # 共享屏幕开关
_screen_share_interval = 3    # 共享屏幕截图间隔（秒）


def ensure_browser(*, headless: bool = False, url: str = "") -> bool:
    """
    确保有头浏览器已启动并连接 CDP。
    若已启动则复用；否则启动新实例。
    返回是否就绪。
    """
    global _browser, _context, _page, _cdp_ws
    with _bridge_lock:
        if _page and not _page.is_closed():
            if url:
                try:
                    _page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
            return True
        try:
            from playwright.sync_api import sync_playwright
            import requests as _requests

            _pw = sync_playwright().start()
            _browser = _pw.chromium.launch(
                headless=headless,
                args=[
                    "--remote-debugging-port=9222",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            _context = _browser.new_context(viewport={"width": 1280, "height": 800})
            _page = _context.new_page()

            # 通过 DevTools HTTP API 获取 CDP WebSocket URL（不用私有属性）
            try:
                resp = _requests.get("http://127.0.0.1:9222/json/version", timeout=5)
                _cdp_ws = resp.json().get("webSocketDebuggerUrl", "")
            except Exception:
                _cdp_ws = ""

            # 同步到 Hermes（先尝试热更新，失败则重启）
            if _cdp_ws:
                try:
                    from hermes_config import sync_hermes_cdp_endpoint
                    # 先尝试热更新，失败则重启确保 CDP 配置生效
                    success = sync_hermes_cdp_endpoint(_cdp_ws, restart_gateway=False)
                    if not success:
                        uat_logger.warning("CDP 热更新失败，尝试重启 Hermes")
                        sync_hermes_cdp_endpoint(_cdp_ws, restart_gateway=True)
                except Exception as e:
                    uat_logger.warning("CDP 同步到 Hermes 失败: %s", e)

            if url:
                _page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return True
        except Exception as e:
            uat_logger.warning("ExternalBrowserBridge 启动失败: %s", e)
            return False


def get_cdp_ws() -> str:
    return _cdp_ws


def get_page() -> Any:
    return _page


def is_browser_alive() -> bool:
    """验证 Playwright 浏览器实例是否仍然存活（进程未退出、Page 未关闭）。
    当检测到死亡时，同步清理本地引用和 hermes_config 中的 CDP 状态。"""
    global _browser, _context, _page, _cdp_ws
    with _bridge_lock:
        if _browser is None or _page is None:
            return False
        try:
            # 多层检测：先检查底层进程是否还在（如果 API 可用）
            if hasattr(_browser, "process") and _browser.process:
                if hasattr(_browser.process, "poll") and _browser.process.poll() is not None:
                    raise RuntimeError("Browser process exited")
            # 再检查 page 是否可交互
            _ = _page.url
            return not _page.is_closed()
        except Exception:
            # 浏览器进程已死或连接断开，清理所有引用
            try:
                if _context:
                    _context.close()
            except Exception:
                pass
            try:
                if _browser:
                    _browser.close()
            except Exception:
                pass
            _browser = _context = _page = None
            _cdp_ws = ""
            # 同步清理 hermes_config 中的 CDP 状态，防止状态残留
            try:
                from hermes_config import clear_hermes_cdp_endpoint
                clear_hermes_cdp_endpoint(restart_gateway=False)
            except Exception:
                pass
            return False


def force_cleanup_browser() -> None:
    """强制关闭浏览器并清理所有相关状态，供外部兜底调用。"""
    global _browser, _context, _page, _cdp_ws
    with _bridge_lock:
        try:
            if _context:
                _context.close()
        except Exception:
            pass
        try:
            if _browser:
                _browser.close()
        except Exception:
            pass
        _browser = _context = _page = None
        _cdp_ws = ""
        try:
            from hermes_config import clear_hermes_cdp_endpoint
            clear_hermes_cdp_endpoint(restart_gateway=False)
        except Exception:
            pass


def get_page_snapshot() -> str:
    """
    获取当前页面快照文本（供 _build_system_prompt 使用）。
    复用 ai_page_probe 的 JS 探测逻辑，但操作已启动的 Page 对象。
    """
    if not _page or _page.is_closed():
        return ""
    try:
        from ai_page_probe import _COLLECT_INTERACTIVE_JS_FLAT, _format_summary_lines

        title = _page.title()
        url = _page.url
        try:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT, 200)
        except Exception:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT)

        registry = _build_registry_from_rows(rows or [])
        text = _format_summary_lines(title, url, registry, max_lines=90, max_chars=18000)
        return text or f"URL: {url}\nTitle: {title}"
    except Exception:
        try:
            return f"URL: {_page.url}\nTitle: {_page.title()}"
        except Exception:
            return ""


def get_probe_registry() -> List[Dict[str, Any]]:
    """获取 probe 注册表（供 ai_locator_resolution 使用）。"""
    if not _page or _page.is_closed():
        return []
    try:
        from ai_page_probe import _COLLECT_INTERACTIVE_JS_FLAT

        try:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT, 200)
        except Exception:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT)

        return _build_registry_from_rows(rows or [])
    except Exception:
        return []


def get_dom_context_pack() -> str:
    """获取 DOM 上下文包（供 _build_system_prompt 使用）。"""
    if not _page or _page.is_closed():
        return ""
    try:
        from ai_page_probe import _COLLECT_INTERACTIVE_JS_FLAT

        try:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT, 200)
        except Exception:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT)

        registry = _build_registry_from_rows(rows or [])
        lines = []
        for entry in registry[:60]:
            i = entry.get("i", "")
            tag = entry.get("tag", "")
            css = entry.get("css", "")
            text = (entry.get("text") or "")[:40]
            lines.append(f"[{i}] <{tag}> css={css} text={text}")
        return "\n".join(lines)
    except Exception:
        return ""


def capture_screenshot() -> bytes:
    """截取当前页面截图，缓存并返回 PNG bytes。"""
    global _last_screenshot
    if not _page or _page.is_closed():
        return _last_screenshot
    try:
        png = _page.screenshot(type="png")
        _last_screenshot = png
        return png
    except Exception:
        return _last_screenshot


def cleanup():
    """关闭浏览器，清除 CDP 配置。"""
    global _browser, _context, _page, _cdp_ws
    with _bridge_lock:
        try:
            if _context:
                _context.close()
            if _browser:
                _browser.close()
        except Exception:
            pass
        _browser = _context = _page = None
        _cdp_ws = ""
        try:
            from hermes_config import clear_hermes_cdp_endpoint
            clear_hermes_cdp_endpoint(restart_gateway=False)
        except Exception:
            pass


def _build_registry_from_rows(rows: List[Any]) -> List[Dict[str, Any]]:
    """从 JS evaluate 返回的行构建 probe 注册表。"""
    import re as _re

    registry: List[Dict[str, Any]] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        raw_norm = dict(raw)
        if not raw_norm.get("css"):
            sug = (raw_norm.get("suggestedSelector") or "").strip()
            if sug:
                raw_norm["css"] = sug
            elif raw_norm.get("id"):
                tid = str(raw_norm.get("id") or "").strip()
                ttag = (raw_norm.get("tag") or "input").strip().lower()
                if tid and _re.match(r"^[\w.-]+$", tid):
                    if ttag in ("input", "button", "a", "select", "textarea"):
                        raw_norm["css"] = f"{ttag}#{tid}"
                    else:
                        raw_norm["css"] = f"#{tid}"
        entry = {
            "i": i,
            "frame": "main",
            "frame_index": 0,
            "tag": raw.get("tag") or "",
            "id": raw.get("id") or "",
            "css": raw_norm.get("css") or "",
            "text": (raw.get("text") or "")[:80],
            "role": raw.get("role") or "",
            "aria_label": raw.get("ariaLabel") or "",
            "href": raw.get("href") or "",
            "value": (raw.get("value") or "")[:40],
            "type": raw.get("type") or "",
            "box": raw.get("box") or {},
        }
        registry.append(entry)
    return registry
