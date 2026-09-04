"""
本机有头浏览器桥接层（系统 Edge/Chrome + CDP）。
已取消内嵌画布 Chromium；Hermes 通过 sync_hermes_cdp_endpoint() attach 到同一本机浏览器。
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from modules.core.logger import uat_logger

_bridge_lock = threading.Lock()
_browser = None       # Playwright Browser 实例
_context = None       # BrowserContext
_page = None          # 当前活跃 Page
_cdp_ws = ""          # CDP WebSocket URL
_last_screenshot = b""  # 最新截图缓存
_screen_share_active = False  # 共享屏幕开关
_screen_share_interval = 3    # 共享屏幕截图间隔（秒）

# DOM 快照短 TTL 缓存（避免同一页面在 8 秒内被 hermes_execute 反复重取）
# 每次导航/新标签会自动因 _cache_key 变化而失效
_DOM_CACHE_TTL_SEC = 8.0
_dom_cache: Dict[str, Dict[str, Any]] = {}
import time as _time_mod


def urls_match_for_browser_reuse(page_url: str, target: str) -> bool:
    """同 host 且路径基本一致才视为已在目标页（避免同站旧页被当成已完成）。"""
    if not target:
        return True
    cur = (page_url or "").strip()
    tgt = target.strip()
    if not cur or cur.lower() in ("about:blank", "chrome://newtab/", "edge://newtab/"):
        return False
    try:
        from urllib.parse import urlparse

        a, b = urlparse(cur), urlparse(tgt)
        if (a.netloc or "").lower() != (b.netloc or "").lower():
            return False
        ap = (a.path or "/").rstrip("/") or "/"
        bp = (b.path or "/").rstrip("/") or "/"
        if ap == bp:
            return True
        if bp != "/" and (ap.startswith(bp) or bp.startswith(ap)):
            return True
        return False
    except Exception:
        return tgt.rstrip("/") in cur or cur.rstrip("/") in tgt


def _dom_cache_get(key: str) -> Optional[str]:
    try:
        entry = _dom_cache.get(key)
        if not entry:
            return None
        if _time_mod.monotonic() - float(entry.get("ts", 0)) > _DOM_CACHE_TTL_SEC:
            _dom_cache.pop(key, None)
            return None
        val = entry.get("value")
        return val if isinstance(val, str) else None
    except Exception:
        return None


def _dom_cache_set(key: str, value: str) -> None:
    try:
        _dom_cache[key] = {"ts": _time_mod.monotonic(), "value": value}
        # 简单清理：超过 20 条时丢弃最旧的
        if len(_dom_cache) > 20:
            oldest_k = min(
                _dom_cache.keys(),
                key=lambda k: float((_dom_cache.get(k) or {}).get("ts", 0)),
            )
            _dom_cache.pop(oldest_k, None)
    except Exception:
        pass


def invalidate_dom_cache() -> None:
    """浏览器变更动作后失效 DOM 短缓存，避免 SPA 同 URL 脏读。"""
    try:
        _dom_cache.clear()
    except Exception:
        pass


def _dom_cache_key_for_page(suffix: str = "") -> str:
    try:
        pu = _page.url if _page and not _page.is_closed() else ""
        pt = _page.title() if _page and not _page.is_closed() else ""
        return f"{suffix}|{pu}|{pt}"
    except Exception:
        return suffix


def ensure_browser(*, headless: bool = False, url: str = "", browser: str = "edge", force_new: bool = False) -> bool:
    """
    确保本机有头浏览器已启动并连接 CDP（优先系统 Edge/Chrome）。
    Hermes 通过 sync_hermes_cdp_endpoint() attach 到同一浏览器。

    启动策略：有业务 URL 时命令行直接打开目标页；无 URL 时用 about:blank。
    若已有页面且目标 URL 不同（含同站不同路径），再 page.goto；启动后清理多余空白标签。

    force_new: True 时强制关闭现有浏览器实例并重新启动，确保全新会话。
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

        # 强制新浏览器：先清理现有实例（勿再调 force_cleanup_browser，同锁会死锁）
        if force_new:
            uat_logger.info("[Browser] force_new=True, 正在清理现有浏览器实例...")
            try:
                _cdp_mod.disconnect(stop_browser=True)
            except Exception:
                pass
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
            _browser = None
            _context = None
            _page = None
            _cdp_ws = ""
            try:
                from modules.hermes.hermes_config import clear_hermes_cdp_endpoint

                clear_hermes_cdp_endpoint(restart_gateway=False)
            except Exception:
                pass
            try:
                invalidate_dom_cache()
            except Exception:
                pass

        def _urls_on_same_target(page_url: str, target: str) -> bool:
            return urls_match_for_browser_reuse(page_url, target)

        def _need_goto(page_url: str, target: str) -> bool:
            if not target:
                return False
            return not _urls_on_same_target(page_url, target)

        def _local_page_usable() -> bool:
            if not _page:
                return False
            try:
                if _page.is_closed():
                    return False
                _ = _page.url
                # CDP 端口也必须活着（用户关窗后 page 对象可能短暂仍“未 closed”）
                port = int((_cdp_mod._snap() or {}).get("debug_port") or 0)
                if port > 0 and not _cdp_mod.fetch_cdp_ws(port):
                    return False
                return True
            except Exception:
                return False

        if not force_new and _local_page_usable():
            if nav_url:
                try:
                    cur_u = ""
                    try:
                        cur_u = str(_page.url or "")
                    except Exception:
                        cur_u = ""
                    if _need_goto(cur_u, nav_url):
                        uat_logger.info("[Browser] 同站/跨站路径不同，重新导航到 %s", nav_url[:120])
                        _page.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
                    _cdp_mod._set(pending_start_url="")
                except Exception as exc:
                    uat_logger.warning("[Browser] 复用页导航失败，将重建: %s", exc)
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
                        _cdp_mod.disconnect(stop_browser=False)
                    except Exception:
                        pass
            if _local_page_usable():
                try:
                    port = int((_cdp_mod._snap() or {}).get("debug_port") or 0)
                    _cdp_mod.close_blank_cdp_targets(port, keep_url_substr=nav_url or "")
                except Exception:
                    pass
                uat_logger.info("[Browser] 复用现有浏览器实例")
                return True

        # 本地页不可用：清引用后再启动/附着
        if _page is not None or _browser is not None:
            try:
                _browser = _context = _page = None
                _cdp_ws = ""
            except Exception:
                _browser = _context = _page = None
                _cdp_ws = ""

        try:
            _cdp_state = _cdp_mod._snap()
            _debug_port = int(_cdp_state.get("debug_port") or 0)
            _ws = ""
            if _debug_port:
                _ws = _cdp_mod.fetch_cdp_ws(_debug_port) or ""
                if not _ws:
                    # 残留端口/状态：先 disconnect 再开新进程
                    try:
                        _cdp_mod.disconnect(stop_browser=True)
                    except Exception:
                        pass
                    _debug_port = 0

            if not _ws:
                uat_logger.info("[Browser] 正在启动新的 %s 浏览器实例...", kind)
                launched = _cdp_mod.launch_debug_browser(browser=kind, url=nav_url or "")
                if not launched.get("success"):
                    if kind == "edge":
                        uat_logger.info("[Browser] Edge 启动失败，尝试 Chrome...")
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
                uat_logger.info("[Browser] %s 浏览器启动成功，CDP 端口: %d", kind, _debug_port)

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
                from modules.hermes.hermes_config import sync_hermes_cdp_endpoint

                ok = sync_hermes_cdp_endpoint(_cdp_ws, restart_gateway=False)
                if not ok:
                    uat_logger.warning("CDP 热更新失败，尝试重启 Hermes")
                    sync_hermes_cdp_endpoint(_cdp_ws, restart_gateway=True)
            except Exception as e:
                uat_logger.warning("CDP 同步到 Hermes 失败: %s", e)

            if nav_url and _page and not _page.is_closed():
                try:
                    cur_u = ""
                    try:
                        cur_u = str(_page.url or "")
                    except Exception:
                        cur_u = ""
                    if _need_goto(cur_u, nav_url):
                        _page.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
                    _cdp_mod._set(pending_start_url="")
                except Exception:
                    pass
            try:
                _cdp_mod.close_blank_cdp_targets(_debug_port, keep_url_substr=nav_url or "")
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
                    from modules.hermes.hermes_config import sync_hermes_cdp_endpoint

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
                if _page.is_closed():
                    _local_dead = True
                else:
                    # 用户关窗后：CDP HTTP 不通则视为死亡（避免误判仍存活）
                    try:
                        from web_capture import cdp_browser as _cdp_mod

                        port = int((_cdp_mod._snap() or {}).get("debug_port") or 0)
                        if port > 0 and not _cdp_mod.fetch_cdp_ws(port):
                            raise RuntimeError("CDP endpoint unreachable")
                    except RuntimeError:
                        raise
                    except Exception:
                        pass
                    return True
            except Exception:
                _local_dead = True

        # --- 第二层：先检查 cdp_browser 是否有存活的浏览器（在清理 hermes 之前） ---
        _cdp_alive = False
        _ws = ""
        try:
            from web_capture import cdp_browser as _cdp_mod
            _cdp_state = _cdp_mod._snap()
            _debug_port = int(_cdp_state.get("debug_port") or 0)
            if _debug_port:
                _ws = _cdp_mod.fetch_cdp_ws(_debug_port) or ""
                if _ws:
                    # 还要有至少一个 page，否则是僵尸调试端口
                    pages = _cdp_mod.fetch_cdp_pages(_debug_port) or []
                    if pages:
                        _cdp_ws = _ws
                        _cdp_alive = True
                    else:
                        _ws = ""
            # 跟踪进程已退出：清掉假存活状态
            proc = _cdp_state.get("browser_process")
            if proc is not None:
                try:
                    if proc.poll() is not None and not _cdp_alive:
                        _cdp_mod.disconnect(stop_browser=True)
                except Exception:
                    pass
        except Exception:
            pass

        # 本地实例已死，清理引用（但只在 cdp_browser 也死了的时候才清理 hermes 配置）
        if _local_dead and (_browser is not None or _page is not None):
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
                    from modules.hermes.hermes_config import clear_hermes_cdp_endpoint
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
            from modules.hermes.hermes_config import clear_hermes_cdp_endpoint
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
    + 8 秒 TTL 缓存（同一 URL/标题 不重复 evaluate）。
    """
    if not _page or _page.is_closed():
        return ""
    _ck = _dom_cache_key_for_page("snap")
    _cached = _dom_cache_get(_ck)
    if _cached:
        return _cached
    try:
        from modules.ai.ai_page_probe import _COLLECT_INTERACTIVE_JS_FLAT, _format_summary_lines

        title = _page.title()
        url = _page.url
        try:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT, 200)
        except Exception:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT)

        registry = _build_registry_from_rows(rows or [])
        text = _format_summary_lines(title, url, registry, max_lines=90, max_chars=18000)
        result = text or f"URL: {url}\nTitle: {title}"
        if result:
            _dom_cache_set(_ck, result)
        return result
    except Exception:
        try:
            result = f"URL: {_page.url}\nTitle: {_page.title()}"
            if result:
                _dom_cache_set(_ck, result)
            return result
        except Exception:
            return ""


def resolve_element_from_live_dom(
    *,
    hint: str = "",
    typed_value: str = "",
    prefer_focused: bool = False,
) -> Dict[str, str]:
    """从当前页面 DOM/probe 解析可回放定位（供 ActionRecorder 落库）。

    返回可能含：selector(css)、text、name、placeholder、tag、matched。
    """
    out: Dict[str, str] = {}
    hint = (hint or "").strip()
    typed_value = (typed_value or "").strip()
    try:
        if not _page or _page.is_closed():
            return out
    except Exception:
        return out

    # 1) 输入后优先取焦点元素（browser_type 后 activeElement 最准）
    if prefer_focused or typed_value:
        try:
            focused = _page.evaluate(
                """() => {
                    const el = document.activeElement;
                    if (!el || el === document.body || el === document.documentElement) return null;
                    const tag = (el.tagName || '').toLowerCase();
                    const id = el.id || '';
                    const name = el.getAttribute('name') || '';
                    const ph = el.getAttribute('placeholder') || '';
                    const aria = el.getAttribute('aria-label') || '';
                    const typ = el.getAttribute('type') || '';
                    const text = (el.innerText || el.textContent || '').trim().slice(0, 80);
                    let css = '';
                    if (id && /^[\\w.-]+$/.test(id)) {
                      css = (tag === 'input' || tag === 'button' || tag === 'textarea' || tag === 'select')
                        ? (tag + '#' + id) : ('#' + id);
                    } else if (name) {
                      css = tag + '[name=\"' + name.replace(/"/g, '\\\\\"') + '\"]';
                    } else if (ph) {
                      css = tag + '[placeholder=\"' + ph.replace(/"/g, '\\\\\"').slice(0, 40) + '\"]';
                    }
                    return { tag, id, name, placeholder: ph, ariaLabel: aria, type: typ, text, css,
                             value: (el.value || '').toString().slice(0, 80) };
                }"""
            )
            if isinstance(focused, dict) and (focused.get("css") or focused.get("placeholder") or focused.get("name")):
                if focused.get("css"):
                    out["selector"] = str(focused["css"])[:300]
                label = (
                    focused.get("placeholder")
                    or focused.get("ariaLabel")
                    or focused.get("name")
                    or focused.get("text")
                    or ""
                )
                if label:
                    out["text"] = str(label)[:120]
                    out["matched"] = str(label)[:120]
                if typed_value and str(focused.get("value") or "") == typed_value:
                    out["verified_value"] = typed_value
                return out
        except Exception:
            pass

    # 2) probe 注册表按 hint / typed 文案匹配
    try:
        registry = get_probe_registry() or []
    except Exception:
        registry = []
    hint_l = hint.lower()
    best = None
    best_score = 0
    for entry in registry:
        if not isinstance(entry, dict):
            continue
        blob = " ".join(
            str(entry.get(k) or "")
            for k in ("text", "aria_label", "id", "css", "role", "value", "type")
        ).lower()
        score = 0
        if hint_l and hint_l in blob:
            score += 10 + min(len(hint_l), 20)
        if typed_value and typed_value in str(entry.get("value") or ""):
            score += 8
        if hint and str(entry.get("text") or "").strip() == hint:
            score += 15
        if score > best_score:
            best_score = score
            best = entry
    if best and best_score > 0:
        css = str(best.get("css") or "").strip()
        text = str(best.get("text") or best.get("aria_label") or "").strip()
        if css:
            out["selector"] = css[:300]
        if text:
            out["text"] = text[:120]
            out["matched"] = text[:120]
        elif hint:
            out["text"] = hint[:120]
            out["matched"] = hint[:120]
    return out


def get_probe_registry() -> List[Dict[str, Any]]:
    """获取 probe 注册表（供 ai_locator_resolution 使用）。"""
    if not _page or _page.is_closed():
        return []
    try:
        from modules.ai.ai_page_probe import _COLLECT_INTERACTIVE_JS_FLAT

        try:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT, 200)
        except Exception:
            rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT)

        return _build_registry_from_rows(rows or [])
    except Exception:
        return []


def _wait_for_page_ready(timeout_ms: int = 5000) -> bool:
    """等待页面加载完成。
    
    Returns:
        bool: 页面是否已就绪
    """
    global _page
    if not _page or _page.is_closed():
        return False
    
    import time
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    
    while time.monotonic() < deadline:
        try:
            # 检查页面是否可用
            url = _page.url
            if url and url not in ("", "about:blank", "chrome://newtab/", "edge://newtab/"):
                # 页面有 URL，说明浏览器已连接
                # 尝试等待 body 元素出现
                try:
                    _page.wait_for_selector("body", timeout=1000, state="attached")
                    return True
                except Exception:
                    # body 还没出现，继续等
                    pass
        except Exception:
            pass
        
        time.sleep(0.2)
    
    # 超时后检查一次，即使没有 body 也返回 True（页面可能已就绪但选择器不可用）
    try:
        url = _page.url
        if url:
            return True
    except Exception:
        pass
    
    return False


def get_dom_context_pack(*, skip_wait: bool = False, wait_timeout_ms: int = 3000, force: bool = False) -> str:
    """获取 DOM 上下文包（供 _build_system_prompt 使用）。
    
    改进：增加等待页面加载和重试机制，确保获取到有效的 DOM 信息。
    + 8 秒 TTL 缓存（同一 URL/标题 不重复采集）。
    skip_wait: 外层已 wait 过时传 True，避免双次 3s 等待。
    force: True 时跳过缓存强制重采。
    """
    global _page
    if not _page or _page.is_closed():
        return ""
    
    _ck = _dom_cache_key_for_page("ctx")
    if not force:
        _cached = _dom_cache_get(_ck)
        if _cached:
            return _cached

    # 等待页面就绪（可由 get_rich_page_context 跳过，避免双 wait）
    if not skip_wait:
        _wait_for_page_ready(timeout_ms=int(wait_timeout_ms or 0) or 3000)
    
    strategies = [
        _collect_dom_via_js,
        _collect_dom_via_simplified_js,
    ]
    
    for collect_fn in strategies:
        try:
            result = collect_fn()
            if result and len(result.strip()) > 10:
                _dom_cache_set(_ck, result)
                return result
        except Exception:
            continue
    
    # 所有策略都失败，返回基本页面信息
    try:
        result = _get_basic_page_info()
        if result:
            _dom_cache_set(_ck, result)
        return result
    except Exception:
        return ""


def _collect_dom_via_js() -> str:
    """通过主 JS 采集脚本获取 DOM 信息。"""
    from modules.ai.ai_page_probe import _COLLECT_INTERACTIVE_JS_FLAT

    try:
        rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT, 200)
    except Exception:
        rows = _page.evaluate(_COLLECT_INTERACTIVE_JS_FLAT)

    registry = _build_registry_from_rows(rows or [])
    if not registry:
        return ""
    
    lines = []
    for entry in registry[:60]:
        i = entry.get("i", "")
        tag = entry.get("tag", "")
        css = entry.get("css", "")
        text = (entry.get("text") or "")[:40]
        lines.append(f"[{i}] <{tag}> css={css} text={text}")
    return "\n".join(lines)


def _collect_dom_via_simplified_js() -> str:
    """通过简化版 JS 采集脚本获取 DOM 信息（备用策略）。"""
    simplified_js = """
() => {
  const results = [];
  const selectors = 'input, button, textarea, select, a[href], [role="button"], [role="textbox"], [role="combobox"]';
  const elements = document.querySelectorAll(selectors);
  for (let i = 0; i < elements.length && results.length < 100; i++) {
    const el = elements[i];
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 && rect.height < 2) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    
    const tag = el.tagName.toLowerCase();
    const id = el.id || '';
    const name = el.getAttribute('name') || '';
    const type = el.getAttribute('type') || '';
    const placeholder = el.getAttribute('placeholder') || '';
    const text = (el.innerText || '').trim().substring(0, 60);
    const href = (el.getAttribute('href') || '').substring(0, 100);
    const ariaLabel = el.getAttribute('aria-label') || '';
    const role = el.getAttribute('role') || '';
    
    let cssSelector = '';
    if (id && /^[\\w-]+$/.test(id)) {
      cssSelector = `#${id}`;
    } else if (name) {
      cssSelector = `${tag}[name="${name}"]`;
    } else {
      const classes = el.className || '';
      const primaryClass = classes.split(/\\s+/)[0] || '';
      if (primaryClass) {
        cssSelector = `${tag}.${primaryClass}`;
      }
    }
    
    results.push({
      tag,
      css: cssSelector,
      id,
      name,
      type,
      placeholder,
      text,
      href,
      ariaLabel,
      role,
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    });
  }
  return results;
}
"""
    rows = _page.evaluate(simplified_js)
    if not rows:
        return ""
    
    lines = []
    for i, entry in enumerate(rows[:60]):
        tag = entry.get("tag", "")
        css = entry.get("css", "")
        text = (entry.get("text") or entry.get("placeholder") or "")[:40]
        lines.append(f"[{i}] <{tag}> css={css} text={text}")
    return "\n".join(lines)


def _get_basic_page_info() -> str:
    """获取基本页面信息（DOM 采集失败时使用）。"""
    title = _page.title()
    url = _page.url
    return f"[页面信息] URL={url}\n标题={title}\nDOM 采集失败，请使用 JavaScript 或视觉辅助定位。"


def get_visual_description(hint: str = "") -> str:
    """获取页面视觉描述（用于辅助 DOM 定位）。
    
    当 DOM 信息为空时，使用此函数获取视觉描述来辅助定位。
    """
    if not _page or _page.is_closed():
        return ""
    
    try:
        png = _page.screenshot(type="png")
        if not png:
            return ""
        
        focus = (hint or "").strip() or "页面上的按钮、输入框、链接等可交互元素"
        
        instruction = (
            "你是网页 UI 观察助手。根据截图用中文给出结构化短描述（严格不超过 300 字）。\n"
            f"关注点: {focus}\n"
            "必须包含：\n"
            "1) 页面标题和主要内容\n"
            "2) 所有可见的按钮、输入框、链接（带文字内容）\n"
            "3) 表单结构（如有）\n"
            "4) 异常弹窗或提示（若有）\n"
            "5) 布局特征（左右/上下结构、主要区域）\n"
            "格式示例：\n"
            "页面标题: ...\n"
            "主要元素: [登录按钮]、[用户名输入框]、[密码输入框]\n"
            "表单: 包含用户名和密码字段\n"
            "异常: 无\n"
            "布局: 居中卡片式布局\n"
        )
        
        from modules.ai.ai_vision_local import vision_describe
        result = vision_describe(png, instruction) or ""
        return result
    except Exception:
        return ""


def get_rich_page_context(task_instruction: str = "") -> str:
    """获取丰富的页面上下文（DOM + 视觉 + 建议）。
    
    这是主要的上下文采集函数，用于浏览器任务。
    结合 DOM 信息和视觉截图，为 Agent 提供完整的页面理解。
    
    改进：
    1. 仅一次 page-ready wait（避免与 get_dom_context_pack 双等）
    2. DOM 为空时默认不跑 VLM（仅屏幕观察开启或 AI_DOM_VLM_FALLBACK=1）
    3. 始终提供 JavaScript 定位建议
    """
    global _page, _screen_share_active
    if not _page or _page.is_closed():
        return ""
    
    # 等待页面就绪（仅一次）
    _wait_for_page_ready(timeout_ms=3000)
    
    context_parts = []
    
    # 1. 获取页面基本信息
    try:
        title = _page.title()
        url = _page.url
        context_parts.append(f"【页面状态】URL={url}\n标题={title}")
    except Exception:
        context_parts.append("【页面状态】无法获取")
    
    # 2. 获取 DOM 信息（外层已 wait，跳过二次等待）
    dom_pack = get_dom_context_pack(skip_wait=True)
    has_dom = bool(dom_pack and len(dom_pack.strip()) > 10)
    
    # 3. 添加 DOM 信息；空 DOM 时条件化 VLM
    if has_dom:
        context_parts.append(f"【页面 DOM/可交互控件】\n{dom_pack}")
    else:
        context_parts.append("【页面 DOM 采集结果】未获取到 DOM 信息")
        allow_vlm = bool(_screen_share_active) or (
            (os.environ.get("AI_DOM_VLM_FALLBACK") or "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        if allow_vlm:
            context_parts.append("将使用视觉辅助定位")
            try:
                hint = task_instruction or "页面上的按钮、输入框、链接等可交互元素"
                visual_desc = get_visual_description(hint)
                if visual_desc:
                    context_parts.append(f"【页面视觉描述】\n{visual_desc}")
                else:
                    context_parts.append(
                        "【定位建议】\n"
                        "DOM/视觉均失败：请用 browser_console 执行 JS，或至多一次 browser_snapshot。"
                    )
            except Exception:
                context_parts.append(
                    "【定位建议】\n"
                    "请使用 browser_console 直接操作 DOM，或至多一次 browser_snapshot。"
                )
        else:
            context_parts.append(
                "【定位建议】\n"
                "请优先用 browser_console(expression=...) 查询/点击；"
                "难定位时再调用一次 browser_snapshot（全程最多 2 次）。"
                "开启「允许观察屏幕」后才会自动做视觉描述兜底。"
            )
    
    # 4. 始终添加 JavaScript 定位建议
    js_suggestion = _generate_js_suggestion(task_instruction, has_dom)
    if js_suggestion:
        context_parts.append(f"【JavaScript 定位建议】\n{js_suggestion}")
    else:
        # 即使没有任务指令，也提供通用 JavaScript 建议
        context_parts.append(
            "【通用 JavaScript 操作方法】\n"
            "  1. 定位元素: document.querySelector('selector') 或 document.querySelectorAll('selector')\n"
            "  2. 点击: element.click() 或 element.dispatchEvent(new Event('click', {bubbles: true}))\n"
            "  3. 输入: element.value = 'text'; element.dispatchEvent(new Event('input', {bubbles: true}))\n"
            "  4. React/Vue 注意: 必须用 dispatchEvent 触发事件，不能只设置 value"
        )
    
    # 5. 添加元素定位策略说明
    strategy = _build_element_locating_strategy(has_dom)
    context_parts.append(strategy)
    
    return "\n\n".join(context_parts)


def _generate_js_suggestion(task_instruction: str, has_dom: bool) -> str:
    """根据任务指令生成完整的 JavaScript 操作代码（可直接用于 browser_console）。

    生成的代码是可直接执行的完整 JavaScript 代码块，Agent 可以直接调用：
    browser_console(expression=<代码>)
    """
    if not task_instruction:
        return ""
    
    suggestions = []
    task_lower = task_instruction.lower()
    
    # 生成可直接执行的 JavaScript 代码块
    code_blocks = []
    
    # 检测任务类型并生成相应的 JavaScript 代码
    if any(kw in task_lower for kw in ["登录", "login", "sign", "注册"]):
        code_blocks.append(
            """// 登录表单自动填充
(() => {
  const results = {};
  // 定位用户名输入框
  const usernameInput = document.querySelector('input[name="username"], input[name="account"], input[type="text"], input[type="email"]');
  if (usernameInput) {
    usernameInput.focus();
    usernameInput.value = '';
    usernameInput.dispatchEvent(new Event('input', {bubbles: true}));
    results.username_input = 'found';
  } else {
    results.username_input = 'not_found';
  }
  // 定位密码输入框
  const passwordInput = document.querySelector('input[name="password"], input[type="password"]');
  if (passwordInput) {
    passwordInput.focus();
    results.password_input = 'found';
  } else {
    results.password_input = 'not_found';
  }
  // 列出所有可用的 input 元素
  const allInputs = Array.from(document.querySelectorAll('input, textarea, button, select'));
  results.all_elements = allInputs.map(el => ({
    tag: el.tagName,
    type: el.type || '',
    id: el.id || '',
    name: el.getAttribute('name') || '',
    placeholder: el.getAttribute('placeholder') || '',
    text: (el.innerText || '').trim().substring(0, 50),
    visible: el.offsetWidth > 0 && el.offsetHeight > 0
  }));
  return results;
})()"""
        )
    
    if any(kw in task_lower for kw in ["输入", "填写", "input", "fill", "type", "录入"]):
        code_blocks.append(
            """// 表单输入辅助 - 列出所有可交互元素
(() => {
  const elements = Array.from(document.querySelectorAll('input, textarea, select, button, a[href], [role="textbox"], [role="combobox"], [contenteditable="true"]'));
  return elements.map((el, i) => ({
    index: i,
    tag: el.tagName.toLowerCase(),
    id: el.id || '',
    name: el.getAttribute('name') || '',
    type: el.getAttribute('type') || '',
    placeholder: el.getAttribute('placeholder') || '',
    text: (el.innerText || '').trim().substring(0, 80),
    cssSelector: (() => {
      if (el.id) return '#' + el.id;
      if (el.getAttribute('name')) return el.tagName + '[name="' + el.getAttribute('name') + '"]';
      const classes = (el.className || '').toString().split(/\\s+/).filter(Boolean);
      if (classes.length) return el.tagName + '.' + classes[0];
      return el.tagName.toLowerCase();
    })(),
    visible: el.offsetWidth > 0 && el.offsetHeight > 0
  })).filter(el => el.visible || el.tag === 'button');
})()"""
        )
    
    if any(kw in task_lower for kw in ["点击", "click", "按钮", "button"]):
        code_blocks.append(
            """// 查找并列出所有按钮和可点击元素
(() => {
  const buttons = Array.from(document.querySelectorAll('button, a, [role="button"], [onclick], input[type="submit"], input[type="button"]'));
  return buttons.map((btn, i) => ({
    index: i,
    tag: btn.tagName.toLowerCase(),
    text: (btn.innerText || btn.value || '').trim().substring(0, 80),
    id: btn.id || '',
    href: btn.getAttribute('href') || '',
    cssSelector: (() => {
      if (btn.id) return '#' + btn.id;
      if (btn.getAttribute('href')) return 'a[href="' + btn.getAttribute('href') + '"]';
      const classes = (btn.className || '').toString().split(/\\s+/).filter(Boolean);
      if (classes.length) return btn.tagName + '.' + classes[0];
      return btn.tagName.toLowerCase();
    })(),
    visible: btn.offsetWidth > 0 && btn.offsetHeight > 0
  }));
})()"""
        )
    
    if any(kw in task_lower for kw in ["搜索", "search", "查询", "query"]):
        code_blocks.append(
            """// 查找搜索框和搜索按钮
(() => {
  const results = {};
  // 搜索框
  const searchInput = document.querySelector('input[type="search"], input[placeholder*="搜索"], input[placeholder*="search"], input[name="search"], [role="searchbox"]');
  if (searchInput) {
    results.search_input = {
      found: true,
      selector: (() => {
        if (searchInput.id) return '#' + searchInput.id;
        if (searchInput.getAttribute('name')) return 'input[name="' + searchInput.getAttribute('name') + '"]';
        return 'input[type="search"]';
      })()
    };
  }
  // 搜索按钮
  const searchBtn = document.querySelector('button[aria-label*="搜索"], button[aria-label*="search"], .search-btn, .btn-search');
  if (searchBtn) {
    results.search_button = {
      found: true,
      selector: (() => {
        if (searchBtn.id) return '#' + searchBtn.id;
        const classes = (searchBtn.className || '').toString().split(/\\s+/).filter(Boolean);
        if (classes.length) return 'button.' + classes[0];
        return 'button';
      })(),
      text: (searchBtn.innerText || '').trim().substring(0, 50)
    };
  }
  // 列出所有可见的 input 元素
  results.all_inputs = Array.from(document.querySelectorAll('input')).filter(el => el.offsetWidth > 0).map(el => ({
    type: el.type,
    id: el.id,
    name: el.getAttribute('name'),
    placeholder: el.getAttribute('placeholder'),
    selector: (() => {
      if (el.id) return '#' + el.id;
      if (el.getAttribute('name')) return 'input[name="' + el.getAttribute('name') + '"]';
      return 'input[type="' + el.type + '"]';
    })()
  }));
  return results;
})()"""
        )
    
    # 如果没有特定任务类型，提供通用的 DOM 分析代码
    if not code_blocks:
        code_blocks.append(
            """// 通用 DOM 分析 - 获取页面上所有可交互元素
(() => {
  const selectors = 'input, textarea, select, button, a, [role="button"], [role="textbox"], [role="combobox"], [contenteditable="true"], [onclick]';
  const elements = Array.from(document.querySelectorAll(selectors));
  return elements.map((el, i) => {
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 2 && rect.height > 0 && rect.top >= 0 && rect.top <= window.innerHeight;
    const style = window.getComputedStyle(el);
    if (!visible && el.tagName !== 'BUTTON') return null;
    return {
      index: i,
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      name: el.getAttribute('name') || '',
      type: el.getAttribute('type') || '',
      placeholder: el.getAttribute('placeholder') || '',
      text: (el.innerText || el.value || '').trim().substring(0, 80),
      cssSelector: (() => {
        if (el.id && /^[\\w-]+$/.test(el.id)) return '#' + el.id;
        if (el.getAttribute('name')) return el.tagName + '[name="' + el.getAttribute('name') + '"]';
        const classes = (el.className || '').toString().split(/\\s+/).filter(Boolean);
        if (classes.length) return el.tagName + '.' + classes[0];
        return el.tagName.toLowerCase();
      })(),
      position: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) }
    };
  }).filter(Boolean);
})()"""
        )
    
    suggestions.append("【可直接执行的 JavaScript 代码（用于 browser_console）】")
    suggestions.extend(code_blocks)
    suggestions.append("")
    suggestions.append("【使用方法】")
    suggestions.append("直接调用 browser_console(expression=<上述代码>) 即可获取结果")
    suggestions.append("根据返回的元素列表，选择目标元素的 cssSelector")
    suggestions.append("然后使用 browser_console(expression=...) 执行操作代码")
    suggestions.append("")
    suggestions.append("【操作代码模板】")
    suggestions.append("// 点击元素：browser_console(expression='document.querySelector(SELECTOR).click()')")
    suggestions.append("// 输入文本：browser_console(expression='const el=document.querySelector(SELECTOR);el.value=TEXT;el.dispatchEvent(new Event(\"input\",{bubbles:true}))')")
    
    return "\n".join(suggestions)


def _build_element_locating_strategy(has_dom: bool) -> str:
    """构建元素定位策略说明。"""
    if has_dom:
        return (
            "【元素定位策略 - 必须严格遵守】\n"
            "  ⚠️ 最重要规则：你必须使用 browser_console(expression=...) 执行上方提供的 JavaScript 代码！\n"
            "  ⚠️ browser_snapshot 全程最多 2 次、禁止连续反复调用！\n"
            "\n"
            "  执行步骤：\n"
            "  1. 直接调用 browser_console(expression=上方的通用 DOM 分析代码)\n"
            "  2. 分析返回的元素列表，找到目标元素的 cssSelector\n"
            "  3. 再次调用 browser_console(expression='document.querySelector(cssSelector).click()')\n"
            "  4. 对输入框使用：browser_console(expression='const el=document.querySelector(cssSelector);el.value=text;el.dispatchEvent(new Event(\"input\",{bubbles:true}))')\n"
            "\n"
            "  示例（不要使用这些具体选择器，先执行上方的代码获取实际选择器）：\n"
            "  browser_console(expression=\"const el=document.querySelector('#username');el.value='test';el.dispatchEvent(new Event('input',{bubbles:true}))\")\n"
            "  browser_console(expression=\"document.querySelector('button.login-btn').click()\")\n"
            "\n"
            "  React/Vue 特别注意：\n"
            "  - 必须使用 dispatchEvent 触发事件\n"
            "  - 不能只设置 value 或 innerText\n"
            "  - 对于受控组件，需要模拟完整事件链"
        )
    else:
        return (
            "【元素定位策略 - DOM 不可用】\n"
            "  ⚠️ 最重要规则：你必须使用 browser_console(expression=...) 执行上方提供的 JavaScript 代码！\n"
            "  ⚠️ browser_snapshot 全程最多 2 次、禁止连续反复调用！\n"
            "\n"
            "  执行步骤：\n"
            "  1. 直接调用 browser_console(expression=上方的通用 DOM 分析代码)\n"
            "  2. 根据返回的元素列表和视觉描述，找到目标元素\n"
            "  3. 使用 browser_console(expression=...) 执行操作：\n"
            "     - document.querySelector('input[name=\"xxx\"]').click() 点击\n"
            "     - const el=document.querySelector('input[name=\"xxx\"]');el.value='text';el.dispatchEvent(new Event('input',{bubbles:true})) 输入\n"
            "  4. 仅当 JavaScript 也完全无法定位时，才使用 browser_snapshot（全程最多 2 次）\n"
            "\n"
            "  绝对禁止：\n"
            "  - 禁止在不尝试 browser_console 执行 JavaScript 的情况下直接调用 browser_snapshot\n"
            "  - 禁止连续反复调用 browser_snapshot\n"
            "  - 禁止反复调用 browser_snapshot / browser_get_images 等只读工具空转"
        )


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
            from modules.hermes.hermes_config import clear_hermes_cdp_endpoint
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
