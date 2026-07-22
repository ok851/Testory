"""
本机有头浏览器桥接层（系统 Edge/Chrome + CDP）。
已取消内嵌画布 Chromium；Hermes 通过 sync_hermes_cdp_endpoint() attach 到同一本机浏览器。
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


def ensure_browser(*, headless: bool = False, url: str = "", browser: str = "edge") -> bool:
    """
    确保本机有头浏览器已启动并连接 CDP（优先系统 Edge/Chrome）。
    Hermes 通过 sync_hermes_cdp_endpoint() attach 到同一浏览器。

    启动策略：进程只开 about:blank 单标签，业务 URL 一律 page.goto；
    禁止把业务 URL 放进浏览器命令行（否则 Edge 会「新建标签页」+ 目标页双开）。
    """
    global _browser, _context, _page, _cdp_ws
    kind = (browser or "edge").strip().lower()
    if kind in ("chromium", "chrome"):
        kind = "chrome"
    else:
        kind = "edge"

    with _bridge_lock:
        from web_capture import cdp_browser as _cdp_mod

        nav_url = (url or "").strip() or str(
            (_cdp_mod._snap() or {}).get("pending_start_url") or ""
        ).strip()

        if _page and not _page.is_closed():
            if nav_url:
                try:
                    _page.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
                    _cdp_mod._set(pending_start_url="")
                except Exception:
                    pass
            try:
                from web_capture.cdp_browser import maximize_debug_browser_window

                maximize_debug_browser_window(page=_page)
            except Exception:
                pass
            return True

        try:
            _cdp_state = _cdp_mod._snap()
            _debug_port = int(_cdp_state.get("debug_port") or 0)
            _ws = ""
            if _debug_port:
                _ws = _cdp_mod.fetch_cdp_ws(_debug_port) or ""

            if not _ws:
                launched = _cdp_mod.launch_debug_browser(browser=kind, url=nav_url or "")
                if not launched.get("success"):
                    if kind == "edge":
                        launched = _cdp_mod.launch_debug_browser(
                            browser="chrome", url=nav_url or ""
                        )
                    if not launched.get("success"):
                        uat_logger.warning("本机浏览器启动失败: %s", launched.get("error"))
                        return False
                _debug_port = int(launched.get("debug_port") or 0)
                _ws = (launched.get("cdp_ws") or "").strip() or (
                    _cdp_mod.fetch_cdp_ws(_debug_port) or ""
                )
                nav_url = nav_url or str(launched.get("pending_start_url") or "").strip()

            if not _ws:
                uat_logger.warning("本机浏览器已启动但未拿到 CDP WebSocket")
                return False

            conn = _cdp_mod.connect_playwright_over_cdp(
                _debug_port, prefer_url=nav_url or ""
            )
            if conn.get("success"):
                _page = _cdp_mod.get_active_page()
                snap2 = _cdp_mod._snap()
                _browser = snap2.get("browser")
                _context = snap2.get("context")
            else:
                from playwright.sync_api import sync_playwright as _sp

                _pw = _sp().start()
                _browser = _pw.chromium.connect_over_cdp(_ws)
                _context = (
                    _browser.contexts[0] if _browser.contexts else _browser.new_context()
                )
                _page = _cdp_mod.pick_best_page(_context, prefer_url=nav_url or "")
                if _page is None:
                    pages = list(getattr(_context, "pages", None) or [])
                    _page = pages[0] if pages else _context.new_page()

            if not _page:
                uat_logger.warning("本机浏览器 CDP 已连接但无可用 Page")
                return False

            _cdp_ws = _ws
            try:
                from hermes_config import sync_hermes_cdp_endpoint

                ok = sync_hermes_cdp_endpoint(_cdp_ws, restart_gateway=False)
                if not ok:
                    uat_logger.warning("CDP 热更新失败，尝试重启 Hermes")
                    sync_hermes_cdp_endpoint(_cdp_ws, restart_gateway=True)
            except Exception as e:
                uat_logger.warning("CDP 同步到 Hermes 失败: %s", e)

            if nav_url and _page and not _page.is_closed():
                try:
                    _page.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
                    _cdp_mod._set(pending_start_url="")
                except Exception:
                    pass
            try:
                from web_capture.cdp_browser import maximize_debug_browser_window

                maximize_debug_browser_window(
                    debug_port=int(_debug_port or 0),
                    page=_page,
                )
            except Exception:
                pass
            return bool(_page and not _page.is_closed())
        except Exception as e:
            uat_logger.warning("本机浏览器桥接失败: %s", e)

        allow_pw = (os.environ.get("AI_ALLOW_PLAYWRIGHT_CHROMIUM_FALLBACK") or "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not allow_pw:
            return False

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
            try:
                resp = _requests.get("http://127.0.0.1:9222/json/version", timeout=5)
                _cdp_ws = resp.json().get("webSocketDebuggerUrl", "")
            except Exception:
                _cdp_ws = ""
            if _cdp_ws:
                try:
                    from hermes_config import sync_hermes_cdp_endpoint

                    success = sync_hermes_cdp_endpoint(_cdp_ws, restart_gateway=False)
                    if not success:
                        sync_hermes_cdp_endpoint(_cdp_ws, restart_gateway=True)
                except Exception as e:
                    uat_logger.warning("CDP 同步到 Hermes 失败: %s", e)
            if nav_url:
                _page.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
            return True
        except Exception as e:
            uat_logger.warning("Playwright Chromium 兜底启动失败: %s", e)
            return False


def get_cdp_ws() -> str:
    return _cdp_ws


def get_page() -> Any:
    return _page


def is_browser_alive() -> bool:
    """验证浏览器实例是否仍然存活。
    先检查本地 Playwright 实例，再检查 cdp_browser 的浏览器。
    当检测到死亡时，同步清理本地引用和 hermes_config 中的 CDP 状态。"""
    global _browser, _context, _page, _cdp_ws
    with _bridge_lock:
        # --- 第一层：检查本地 Playwright 实例 ---
        _local_dead = _browser is None or _page is None
        if not _local_dead:
            try:
                # 多层检测：先检查底层进程是否还在（如果 API 可用）
                if hasattr(_browser, "process") and _browser.process:
                    if hasattr(_browser.process, "poll") and _browser.process.poll() is not None:
                        raise RuntimeError("Browser process exited")
                # 再检查 page 是否可交互
                _ = _page.url
                if not _page.is_closed():
                    return True
                _local_dead = True
            except Exception:
                _local_dead = True

        # --- 第二层：先检查 cdp_browser 是否有存活的浏览器（在清理 hermes 之前） ---
        _cdp_alive = False
        try:
            from web_capture import cdp_browser as _cdp_mod
            _cdp_state = _cdp_mod._snap()
            _debug_port = _cdp_state.get("debug_port") or 0
            if _debug_port:
                _ws = _cdp_mod.fetch_cdp_ws(_debug_port)
                if _ws:
                    _cdp_ws = _ws
                    _cdp_alive = True
        except Exception:
            pass

        # 本地实例已死，清理引用（但只在 cdp_browser 也死了的时候才清理 hermes 配置）
        if _local_dead and _browser is not None:
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
            _cdp_ws = _ws if _cdp_alive else ""
            # 只有当 cdp_browser 也死了，才清理 hermes_config 中的 CDP 状态
            if not _cdp_alive:
                try:
                    from hermes_config import clear_hermes_cdp_endpoint
                    clear_hermes_cdp_endpoint(restart_gateway=False)
                except Exception:
                    pass

        if _cdp_alive:
            return True

        return False


def force_cleanup_browser() -> None:
    """强制关闭浏览器并清理所有相关状态（含 cdp_browser），供外部兜底调用。"""
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
    # 清理 cdp_browser 的状态，防止孤儿浏览器进程
    try:
        from web_capture import cdp_browser as _cdp_mod
        _cdp_mod.disconnect(stop_browser=True)
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
