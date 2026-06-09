# -*- coding: utf-8 -*-
"""
FastAPI 网关：每会话独立 Chromium（Playwright），CDP Screencast 推流，
WebSocket 同步点击/滚动/导航/键盘。

环境变量：
  EMBEDDED_BROWSER_GATEWAY_SECRET  与 Flask 共用，必填。
  EMBEDDED_BROWSER_IDLE_SEC        无 WS 活动回收秒数，默认 1800。
  EMBEDDED_BROWSER_GATE_PORT       监听端口，默认 8765。
  EMBEDDED_BROWSER_HEADLESS        画布 Chromium 是否无头，默认 1（仅画布投屏，不另弹窗）；未设时不用 PLAYWRIGHT_HEADLESS。
  PLAYWRIGHT_HEADLESS              主站 Playwright 用；画布优先 EMBEDDED_BROWSER_HEADLESS。
  PLAYWRIGHT_BROWSER               默认 chromium；可选 chrome / edge / firefox / webkit（与主站一致）。
  EMBEDDED_INSPECT_EVAL_RETRIES    inspect 快照遇「导航销毁上下文」时重试次数，默认 6。
  EMBEDDED_INSPECT_DOM_WAIT_MS     每次 evaluate 前 wait_for_load_state(domcontentloaded) 超时毫秒，默认 8000。
  EMBEDDED_INSPECT_MAX_ITEMS       inspect 返回的最大可交互控件条数，默认 200，上限 240（与主站 ai_page_probe 快照一致）。
  EMBEDDED_GOTO_TIMEOUT_MS         会话首跳 / run-steps navigate / WS 导航的 goto 超时毫秒，默认 28000（上限 120000）。
  EMBEDDED_STEP_SETTLE_MS          每步成功后额外等待毫秒，让页面重绘与 CDP 串流跟上（0 关闭），默认 120，上限 3000。
  EMBEDDED_SNAP_AFTER_STEP         每步成功后向已连接画布 WS 追加一帧 Playwright JPEG 截图（1 开 / 0 关），默认 1，与 CDP screencast 并行可显著对齐「操作点」与画面。
  EMBEDDED_BROWSER_PUBLIC_CDP_HOST   可选；若设置，将 POST /internal/session 返回的 cdp_browser_ws 中主机替换为该值（保留端口与 path），便于宿主机连接容器内调试端口。

HTTP：POST /internal/session/{session_id}/run-steps
  请求体 JSON：{"steps":[...]}，steps 与主站 ai_plan_steps_to_playwright_script_steps 输出一致（navigate/click/input/wait/assert/verify 等），
  在远程画布同一会话内串行执行；与 WebSocket 输入共用 run_lock，避免与画布点击交错冲突。
"""

from __future__ import annotations

try:
    from pathlib import Path

    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.is_file():
        load_dotenv(_env_path, encoding="utf-8-sig")
except ImportError:
    pass

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from ai_page_probe import INTERACTIVE_PAGE_SNAPSHOT_EVAL_JS

logger = logging.getLogger(__name__)


def _embedded_goto_timeout_ms() -> int:
    """会话创建、步内 navigate、WS 侧导航共用的 goto 超时（毫秒）。"""
    v = int(os.environ.get("EMBEDDED_GOTO_TIMEOUT_MS", "28000") or 28000)
    return max(5000, min(v, 120000))


def _embedded_step_settle_ms() -> int:
    """每步成功后暂停，便于 CDP screencast 与 SPA 状态提交。"""
    v = int(os.environ.get("EMBEDDED_STEP_SETTLE_MS", "120") or 120)
    return max(0, min(v, 3000))


async def _embedded_settle_after_step(page: Page) -> None:
    ms = _embedded_step_settle_ms()
    if ms <= 0:
        return
    try:
        await page.evaluate(
            "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"
        )
    except Exception:
        pass
    await asyncio.sleep(ms / 1000.0)


def _embedded_snap_after_step_enabled() -> bool:
    v = (os.environ.get("EMBEDDED_SNAP_AFTER_STEP") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _embedded_snap_jpeg_quality() -> int:
    q = int(os.environ.get("EMBEDDED_BROWSER_JPEG_QUALITY", "52") or 52)
    return max(30, min(q, 92))


async def _embedded_push_snap_frame_sync(rec: EmbeddedSession, page: Page) -> None:
    """与 CDP screencast 并行：用同一 Page 的截图在关键步后「钉」一帧，缓解串流滞后。"""
    if not _embedded_snap_after_step_enabled():
        return
    ws = getattr(rec, "viewer_ws", None)
    if ws is None:
        return
    try:
        b = await page.screenshot(
            type="jpeg",
            quality=_embedded_snap_jpeg_quality(),
            full_page=False,
            timeout=15000,
        )
        b64 = base64.b64encode(b).decode("ascii")
        async with rec.viewer_send_lock:
            await ws.send_text(json.dumps({"t": "frame", "format": "jpeg", "data": b64, "sync": 1}))
    except Exception:
        pass


async def _embedded_fill_input_resilient(page: Page, el, text: str) -> None:
    """聚焦 + fill；值不一致时用逐键输入（适配 React / Ant Design / Vue 等受控组件）。"""
    txt = str(text or "")
    await el.scroll_into_view_if_needed(timeout=15000)
    try:
        await el.click(timeout=8000)
    except Exception:
        pass
    try:
        await el.fill("", timeout=4000)
    except Exception:
        pass
    try:
        await el.fill(txt, timeout=20000)
    except Exception:
        await el.click(timeout=5000)
        await page.keyboard.type(txt, delay=20)
        if txt:
            try:
                g0 = await el.input_value(timeout=5000)
            except Exception:
                g0 = ""
            if g0 != txt:
                raise RuntimeError("input 填充失败（异常路径后值仍不匹配）")
        return
    if not txt:
        return
    try:
        got = await el.input_value(timeout=5000)
    except Exception:
        got = ""
    if got == txt:
        return
    try:
        await el.fill("", timeout=4000)
    except Exception:
        pass
    press_seq = getattr(el, "press_sequentially", None)
    if callable(press_seq):
        try:
            await press_seq(txt, delay=22, timeout=35000)
            try:
                g1 = await el.input_value(timeout=5000)
            except Exception:
                g1 = ""
            if g1 == txt or not txt:
                return
        except Exception:
            pass
    await el.click(timeout=5000)
    await page.keyboard.type(txt, delay=22)
    try:
        g2 = await el.input_value(timeout=5000)
    except Exception:
        g2 = ""
    if txt and g2 != txt:
        raise RuntimeError("input 值回读仍不匹配（受控组件、遮挡或选择器命中错误元素）")


def _url_assert_variants_embed(s: str) -> tuple:
    from urllib.parse import unquote

    s = (s or "").strip()
    if not s:
        return ("",)
    u = unquote(s)
    out: List[str] = []
    for x in (s, u):
        if x not in out:
            out.append(x)
    return tuple(out)


def _url_assert_matches_embed(actual: str, expected: str, ctype: str) -> bool:
    ctype = (ctype or "").lower()
    av = _url_assert_variants_embed(actual)
    ev = _url_assert_variants_embed(expected)
    if ctype == "url_equals":
        for a in av:
            for e in ev:
                if a == e:
                    return True
        return False
    if ctype == "url_contains":
        for a in av:
            for e in ev:
                if e and e in a:
                    return True
        return False
    return False


def _embedded_playwright_headless() -> bool:
    """
    画布网关专用：默认无头 + CDP 推流，避免与 UI 画布重复弹出可见 Chromium。
    仅当 EMBEDDED_BROWSER_HEADLESS=0 时才开有界面窗口（调试）。
    """
    raw = (os.environ.get("EMBEDDED_BROWSER_HEADLESS") or "").strip()
    if raw:
        return raw.lower() not in ("0", "false", "no", "off")
    return True


def _normalize_embedded_browser(raw: Optional[str]) -> str:
    """与主站 playwright_automation.normalize_playwright_browser_name 保持一致（避免网关依赖整包）。"""
    key = (raw or os.environ.get("PLAYWRIGHT_BROWSER") or "chromium").strip().lower()
    if key in ("msedge", "edge", "microsoft-edge"):
        return "edge"
    if key in ("google-chrome", "chrome", "chrome-stable"):
        return "chrome"
    if key in ("chromium", "cr"):
        return "chromium"
    if key in ("firefox", "ff"):
        return "firefox"
    if key in ("webkit", "safari"):
        return "webkit"
    return "chromium"


def _pick_free_loopback_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _sync_fetch_chromium_cdp_browser_ws(debug_port: int) -> Optional[str]:
    url = f"http://127.0.0.1:{debug_port}/json/version"
    try:
        with urlopen(url, timeout=3.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        ws = (data.get("webSocketDebuggerUrl") or "").strip()
        return ws or None
    except Exception as e:
        logger.debug("json/version failed port=%s: %s", debug_port, e)
        return None


def _rewrite_cdp_ws_for_public_clients(internal_ws: str) -> str:
    pub_host = (os.environ.get("EMBEDDED_BROWSER_PUBLIC_CDP_HOST") or "").strip()
    if not pub_host or not internal_ws:
        return internal_ws
    try:
        u = urlparse(internal_ws)
        if not u.port:
            return internal_ws
        new_netloc = f"{pub_host}:{u.port}"
        return urlunparse((u.scheme, new_netloc, u.path, u.params, u.query, u.fragment))
    except Exception:
        return internal_ws


async def _resolve_cdp_ws_after_launch(debug_port: int) -> Optional[str]:
    for attempt in range(50):
        ws = await asyncio.to_thread(_sync_fetch_chromium_cdp_browser_ws, debug_port)
        if ws:
            return ws
        await asyncio.sleep(0.05 + 0.02 * min(attempt, 15))
    return None


async def _launch_playwright_browser(
    p: Playwright, headless: bool, engine: str, *, cdp_debug_port: Optional[int] = None
) -> Browser:
    base_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if cdp_debug_port is not None and cdp_debug_port > 0:
        base_args = list(base_args) + [f"--remote-debugging-port={cdp_debug_port}"]
    eng = _normalize_embedded_browser(engine)
    if eng == "firefox":
        return await p.firefox.launch(headless=headless)
    if eng == "webkit":
        return await p.webkit.launch(headless=headless)
    if eng == "chromium":
        return await p.chromium.launch(headless=headless, args=base_args)
    channel = "msedge" if eng == "edge" else "chrome"
    return await p.chromium.launch(headless=headless, args=base_args, channel=channel)

# 可交互快照脚本见 ai_page_probe.INTERACTIVE_PAGE_SNAPSHOT_EVAL_JS（与主站内置浏览器一致）。

PAGE_DIAG_JS = """() => {
    const nav = performance.getEntriesByType('navigation')[0];
    const res = performance.getEntriesByType('resource') || [];
    const mem = performance.memory || null;
    const navS = nav ? {
        domContentLoaded: Math.round((nav.domContentLoadedEventEnd || 0) - (nav.domContentLoadedEventStart || 0)),
        load: Math.round((nav.loadEventEnd || 0) - (nav.loadEventStart || 0)),
        transferSize: nav.transferSize || 0,
    } : {};
    const topRes = res.slice().sort((a, b) => (b.duration || 0) - (a.duration || 0)).slice(0, 12)
        .map(r => ({
            name: String(r.name || '').slice(0, 140),
            type: r.initiatorType || '',
            duration: Math.round(r.duration || 0),
            transferSize: r.transferSize || 0,
        }));
    return {
        url: location.href,
        title: document.title || '',
        userAgent: navigator.userAgent || '',
        viewport: { w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio || 1 },
        domNodeCount: document.getElementsByTagName('*').length,
        scriptCount: document.scripts ? document.scripts.length : 0,
        stylesheetCount: document.styleSheets ? document.styleSheets.length : 0,
        resourceEntries: res.length,
        navigation: navS,
        memory: mem ? {
            usedJSHeapSize: mem.usedJSHeapSize,
            totalJSHeapSize: mem.totalJSHeapSize,
            limit: mem.jsHeapSizeLimit,
        } : null,
        slowestResources: topRes,
        source: 'embedded_gateway',
    };
}"""


def _embedded_locator(page: Page, selector: str, selector_type: str):
    st = (selector_type or "css").lower().strip()
    sel = (selector or "").strip()
    if st != "text" and not sel:
        raise ValueError("selector empty")
    if st == "xpath":
        xs = sel
        if xs.lower().startswith("xpath:"):
            xs = xs[6:].strip()
        return page.locator(f"xpath={xs}")
    if st == "text":
        return page.get_by_text(sel, exact=False)
    return page.locator(sel)


async def _embedded_run_step(page: Page, step: Dict[str, Any]) -> None:
    """执行单步（与 ai_plan_steps_to_playwright_script_steps 输出字段对齐的子集）。"""
    action = (step.get("action") or "").strip().lower()
    if action == "navigate":
        url = (step.get("url") or "").strip()
        if not url:
            raise ValueError("navigate 缺少 url")
        await page.goto(url, wait_until="domcontentloaded", timeout=_embedded_goto_timeout_ms())
        return
    if action == "wait":
        ms = int(step.get("time") or 1000)
        ms = max(0, min(ms, 600_000))
        await asyncio.sleep(ms / 1000.0)
        return
    if action == "extract_text":
        return
    if action == "click":
        sel = (step.get("selector") or "").strip()
        st = step.get("selector_type") or "css"
        loc = _embedded_locator(page, sel, st)
        el = loc.first
        await el.scroll_into_view_if_needed(timeout=15000)
        await el.wait_for(state="visible", timeout=30000)
        await el.click(timeout=20000)
        return
    if action == "input":
        sel = (step.get("selector") or "").strip()
        st = step.get("selector_type") or "css"
        text = str(step.get("text") or step.get("input_value") or "")
        loc = _embedded_locator(page, sel, st)
        el = loc.first
        await el.wait_for(state="visible", timeout=30000)
        await _embedded_fill_input_resilient(page, el, text)
        return
    if action == "verify":
        sel = (step.get("selector") or "").strip()
        st = step.get("selector_type") or "css"
        vt = (step.get("verify_type") or "visible").lower()
        loc = _embedded_locator(page, sel, st)
        el = loc.first
        if vt in ("visible", "auto", "clickable"):
            await el.wait_for(state="visible", timeout=30000)
        elif vt in ("exist",):
            await el.wait_for(state="attached", timeout=30000)
        else:
            await el.wait_for(state="visible", timeout=15000)
        return
    if action == "assert":
        ct = (step.get("compare_type") or "text_equals").strip().lower()
        exp = str(step.get("input_value") or step.get("text") or "").strip()
        if ct in ("url_equals", "url_contains"):
            u = page.url
            if ct == "url_equals" and not _url_assert_matches_embed(u, exp, "url_equals"):
                raise RuntimeError(f"url_equals 期望 {exp!r} 当前 {u!r}")
            if ct == "url_contains" and exp and not _url_assert_matches_embed(u, exp, "url_contains"):
                raise RuntimeError(f"url_contains 期望子串 {exp!r} 不在当前 {u!r}")
            return
        if ct in ("page_text_contains", "page_text_equals", "page_text_regex"):
            handle = await page.query_selector("body")
            body_txt = ""
            try:
                if handle:
                    body_txt = (await handle.inner_text()) or ""
            finally:
                if handle:
                    await handle.dispose()
            body_txt = body_txt.strip()
            if ct == "page_text_equals":
                from auth_batch_helpers import page_text_has_exact_snippet

                if not page_text_has_exact_snippet(body_txt, exp):
                    raise RuntimeError(
                        f"page_text_equals 未找到与预期完全一致的文案 {exp!r}"
                    )
            elif ct == "page_text_regex":
                if not exp or not re.search(exp, body_txt):
                    raise RuntimeError(f"page_text_regex 未匹配 pattern={exp!r}")
            else:
                if exp and exp not in body_txt:
                    raise RuntimeError(f"page_text_contains 未找到 {exp!r}")
            return
        sel = (step.get("selector") or "").strip()
        st = step.get("selector_type") or "css"
        if not sel:
            raise ValueError("assert 需要 selector（url_* / page_text_* 除外）")
        loc = _embedded_locator(page, sel, st)
        el = loc.first
        await el.wait_for(state="visible", timeout=20000)
        txt = (await el.inner_text() or "").strip()
        if ct in ("text_equals", "equals"):
            if exp and txt != exp:
                raise RuntimeError(f"text_equals 期望 {exp!r} 实际 {txt!r}")
        elif ct in ("text_contains", "contains"):
            if exp and exp not in txt:
                raise RuntimeError(f"text_contains 期望包含 {exp!r} 实际 {txt!r}")
        elif ct in ("text_regex", "page_text_regex", "regex"):
            if not exp or not re.search(exp, txt):
                raise RuntimeError(f"text_regex 未匹配 pattern={exp!r} 实际 {txt!r}")
        else:
            if exp and txt != exp:
                raise RuntimeError(f"text_equals 期望 {exp!r} 实际 {txt!r}")
        return
    raise ValueError(f"unsupported action: {action}")


def _gateway_secret() -> str:
    return (os.environ.get("EMBEDDED_BROWSER_GATEWAY_SECRET") or "").strip()


def _require_internal(request: Request) -> None:
    exp = _gateway_secret()
    if not exp:
        raise HTTPException(status_code=503, detail="EMBEDDED_BROWSER_GATEWAY_SECRET not set")
    got = request.headers.get("X-Embedded-Browser-Secret") or ""
    try:
        ok = secrets.compare_digest(got, exp)
    except (TypeError, ValueError):
        ok = False
    if not ok:
        raise HTTPException(status_code=401, detail="invalid gateway secret")


def _parse_user_id(request: Request) -> int:
    raw = request.headers.get("X-Embedded-Browser-User-Id") or "0"
    try:
        return int(raw)
    except ValueError:
        return 0


@dataclass
class EmbeddedSession:
    """单会话运行时：独立 Playwright 实例，避免与平台主 automation worker 互相干扰。"""

    session_id: str
    user_id: int
    ws_token: str
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    viewer_ws: Optional[WebSocket] = None
    viewer_send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


app = FastAPI(title="Embedded Browser Gateway", version="1.0.0")
_sessions: Dict[str, EmbeddedSession] = {}
_sessions_lock = asyncio.Lock()
_reaper_task: Optional[asyncio.Task] = None


async def _destroy_session_unlocked(session_id: str) -> None:
    sess = _sessions.pop(session_id, None)
    if not sess:
        return
    try:
        await sess.page.close()
    except Exception:
        logger.debug("page.close failed", exc_info=True)
    try:
        await sess.context.close()
    except Exception:
        logger.debug("context.close failed", exc_info=True)
    try:
        await sess.browser.close()
    except Exception:
        logger.debug("browser.close failed", exc_info=True)
    try:
        await sess.playwright.stop()
    except Exception:
        logger.debug("playwright.stop failed", exc_info=True)
    logger.info("destroyed embedded session %s", session_id)


async def _touch(session_id: str) -> None:
    async with _sessions_lock:
        s = _sessions.get(session_id)
        if s:
            s.last_seen = time.time()


async def _reaper_loop() -> None:
    idle = max(120, int(os.environ.get("EMBEDDED_BROWSER_IDLE_SEC", "1800")))
    while True:
        await asyncio.sleep(60)
        now = time.time()
        victims: list[str] = []
        async with _sessions_lock:
            for sid, rec in _sessions.items():
                if now - rec.last_seen > idle:
                    victims.append(sid)
        for sid in victims:
            async with _sessions_lock:
                rec = _sessions.get(sid)
                if rec and time.time() - rec.last_seen > idle:
                    logger.info("idle reap session %s", sid)
                    await _destroy_session_unlocked(sid)


@app.on_event("startup")
async def _startup() -> None:
    global _reaper_task
    if _gateway_secret():
        _reaper_task = asyncio.create_task(_reaper_loop())
        logger.info("embedded browser gateway started (reaper on)")
    else:
        logger.warning("EMBEDDED_BROWSER_GATEWAY_SECRET empty — internal APIs will reject requests")


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _reaper_task
    if _reaper_task:
        _reaper_task.cancel()
        try:
            await _reaper_task
        except asyncio.CancelledError:
            pass
    async with _sessions_lock:
        for sid in list(_sessions.keys()):
            await _destroy_session_unlocked(sid)


@app.get("/")
async def root() -> Dict[str, Any]:
    """浏览器直接打开根路径时避免误以为服务未启动（内部 API 仍以 /internal 与 /ws 为准）。"""
    return {
        "service": "embedded_browser_gateway",
        "ok": True,
        "health": "/health",
        "hint": "本进程为 AI 自动化测试平台配套网关；请勿用浏览器完成业务操作。健康检查请访问 /health。",
    }


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "sessions": len(_sessions)}


@app.post("/internal/session")
async def internal_create_session(request: Request) -> Dict[str, Any]:
    """创建会话：返回 session_id、ws_token（WebSocket 查询参数）。"""
    _require_internal(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    user_id = int(body.get("user_id") or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id required")
    initial_url = (body.get("initial_url") or "").strip()
    headless = _embedded_playwright_headless()
    browser_hint = (body.get("browser") or body.get("engine") or "").strip() or None

    sid = uuid.uuid4().hex
    tok = secrets.token_urlsafe(32)

    eng_norm = _normalize_embedded_browser(browser_hint or "")
    cdp_port: Optional[int] = None
    cdp_browser_ws: Optional[str] = None
    if eng_norm in ("chromium", "chrome", "edge"):
        try:
            cdp_port = _pick_free_loopback_port()
        except OSError as e:
            logger.warning("cdp port allocation failed: %s", e)
            cdp_port = None

    p = await async_playwright().start()
    browser = await _launch_playwright_browser(
        p, headless, browser_hint or "", cdp_debug_port=cdp_port
    )
    logger.info(
        "session %s using engine %s (hint=%s) cdp_port=%s",
        sid,
        eng_norm,
        browser_hint,
        cdp_port,
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        ignore_https_errors=True,
    )
    page = await context.new_page()
    if initial_url:
        try:
            await page.goto(initial_url, wait_until="domcontentloaded", timeout=_embedded_goto_timeout_ms())
        except Exception as e:
            logger.warning("initial goto failed: %s", e)

    if cdp_port:
        internal_ws = await _resolve_cdp_ws_after_launch(cdp_port)
        if internal_ws:
            cdp_browser_ws = _rewrite_cdp_ws_for_public_clients(internal_ws)
        else:
            logger.warning("session %s: no webSocketDebuggerUrl on port %s", sid, cdp_port)

    rec = EmbeddedSession(
        session_id=sid,
        user_id=user_id,
        ws_token=tok,
        playwright=p,
        browser=browser,
        context=context,
        page=page,
    )
    async with _sessions_lock:
        _sessions[sid] = rec
    logger.info("created session %s user=%s", sid, user_id)
    out: Dict[str, Any] = {"session_id": sid, "ws_token": tok}
    if cdp_browser_ws:
        out["cdp_browser_ws"] = cdp_browser_ws
    return out


@app.delete("/internal/session/{session_id}")
async def internal_delete_session(session_id: str, request: Request) -> Dict[str, Any]:
    _require_internal(request)
    uid = _parse_user_id(request)
    async with _sessions_lock:
        rec = _sessions.get(session_id)
        if not rec:
            raise HTTPException(status_code=404, detail="session not found")
        if uid and rec.user_id != uid:
            raise HTTPException(status_code=403, detail="forbidden")
        await _destroy_session_unlocked(session_id)
    return {"success": True}


def _inspect_transient_nav_error(msg: str) -> bool:
    """与导航重叠时 evaluate 常失败；可等待后重试。"""
    low = (msg or "").lower()
    return (
        "execution context was destroyed" in low
        or "because of a navigation" in low
        or "cannot find context with specified id" in low
        or "target closed" in low
    )


async def _evaluate_interactive_snapshot_stable(page: Page, n: int) -> Dict[str, Any]:
    """
    在 inspect 中执行快照脚本。与 /api/navigate 或网关内导航并发时，旧 frame 上下文会被销毁；
    先 best-effort 等待 domcontentloaded，再对瞬时错误做有限次重试。
    """
    max_attempts = max(1, min(12, int(os.environ.get("EMBEDDED_INSPECT_EVAL_RETRIES", "6") or 6)))
    dom_wait_ms = max(500, min(60000, int(os.environ.get("EMBEDDED_INSPECT_DOM_WAIT_MS", "8000") or 8000)))
    for attempt in range(max_attempts):
        try:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=dom_wait_ms)
            except Exception:
                pass
            raw = await page.evaluate(INTERACTIVE_PAGE_SNAPSHOT_EVAL_JS, n)
            if isinstance(raw, dict):
                raw = dict(raw)
                raw.setdefault("source", "embedded_gateway")
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            if _inspect_transient_nav_error(str(e)) and attempt + 1 < max_attempts:
                logger.info(
                    "inspect snapshot transient (session retry %s/%s): %s",
                    attempt + 1,
                    max_attempts,
                    e,
                )
                await asyncio.sleep(0.15 + 0.2 * attempt)
                continue
            raise


@app.get("/internal/session/{session_id}/inspect")
async def internal_inspect(session_id: str, request: Request) -> Dict[str, Any]:
    _require_internal(request)
    uid = _parse_user_id(request)
    async with _sessions_lock:
        rec = _sessions.get(session_id)
        if not rec:
            raise HTTPException(status_code=404, detail="session not found")
        if uid and rec.user_id != uid:
            raise HTTPException(status_code=403, detail="forbidden")
        rec.last_seen = time.time()
        page = rec.page
    n = max(20, min(int(os.environ.get("EMBEDDED_INSPECT_MAX_ITEMS", "200") or 200), 240))
    try:
        data = await _evaluate_interactive_snapshot_stable(page, n)
    except Exception as e:
        logger.exception("internal_inspect: page.evaluate failed session=%s", session_id)
        raise HTTPException(
            status_code=502,
            detail=f"获取页面结构失败（请确认远程画布已加载页面且未崩溃）: {e}",
        ) from e
    return {"success": True, "data": data}


@app.get("/internal/session/{session_id}/diagnostics")
async def internal_diagnostics(session_id: str, request: Request) -> Dict[str, Any]:
    _require_internal(request)
    uid = _parse_user_id(request)
    async with _sessions_lock:
        rec = _sessions.get(session_id)
        if not rec:
            raise HTTPException(status_code=404, detail="session not found")
        if uid and rec.user_id != uid:
            raise HTTPException(status_code=403, detail="forbidden")
        rec.last_seen = time.time()
        page = rec.page
    data = await page.evaluate(PAGE_DIAG_JS)
    return {"success": True, "data": data if isinstance(data, dict) else {}}


@app.post("/internal/session/{session_id}/run-steps")
async def internal_run_steps(session_id: str, request: Request) -> Dict[str, Any]:
    """在远程画布会话中串行执行脚本步（body.steps 与主站 ai_plan_steps_to_playwright_script_steps 输出一致）。"""
    _require_internal(request)
    uid = _parse_user_id(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    steps_in = body.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        raise HTTPException(status_code=400, detail="steps required (non-empty list)")
    async with _sessions_lock:
        rec = _sessions.get(session_id)
        if not rec:
            raise HTTPException(status_code=404, detail="session not found")
        if uid and rec.user_id != uid:
            raise HTTPException(status_code=403, detail="forbidden")
        rec.last_seen = time.time()
        lk = rec.run_lock
        page = rec.page
    results: List[Dict[str, Any]] = []
    async with lk:
        for i, raw in enumerate(steps_in):
            if not isinstance(raw, dict):
                results.append({"index": i, "ok": False, "error": "step is not an object"})
                break
            try:
                await _embedded_run_step(page, raw)
                results.append({"index": i, "ok": True})
                await _embedded_settle_after_step(page)
                await _embedded_push_snap_frame_sync(rec, page)
            except Exception as e:
                results.append({"index": i, "ok": False, "error": str(e)})
                break
    return {"success": True, "results": results}


@app.websocket("/ws/{session_id}")
async def websocket_browser(websocket: WebSocket, session_id: str) -> None:
    """浏览器画面与输入：首帧起持续 screencast（JPEG base64）。"""
    token = (websocket.query_params.get("token") or "").strip()
    await websocket.accept()

    async with _sessions_lock:
        rec = _sessions.get(session_id)
        try:
            tok_ok = bool(rec) and secrets.compare_digest(rec.ws_token, token)
        except (TypeError, ValueError):
            tok_ok = False
        if not tok_ok:
            await websocket.close(code=4401)
            return
        rec.last_seen = time.time()
        rec.viewer_ws = websocket
        page = rec.page
        emb_lock = rec.run_lock

    frame_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    stop = asyncio.Event()

    cdp = await page.context.new_cdp_session(page)

    def on_screencast(params: Dict[str, Any]) -> None:
        try:
            frame_queue.put_nowait(params)
        except asyncio.QueueFull:
            try:
                frame_queue.get_nowait()
            except Exception:
                pass
            try:
                frame_queue.put_nowait(params)
            except Exception:
                pass

    cdp.on("Page.screencastFrame", on_screencast)
    try:
        await cdp.send("Page.enable", {})
        await cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": int(os.environ.get("EMBEDDED_BROWSER_JPEG_QUALITY", "52")), "maxWidth": 1280, "maxHeight": 720},
        )
    except Exception as e:
        logger.exception("startScreencast failed: %s", e)
        await websocket.close(code=4500)
        return

    async def pump() -> None:
        while not stop.is_set():
            try:
                params = await asyncio.wait_for(frame_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
                async with rec.viewer_send_lock:
                    await websocket.send_text(
                        json.dumps({"t": "frame", "format": "jpeg", "data": params.get("data")})
                    )
            except Exception:
                break

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            raw = await websocket.receive_text()
            await _touch(session_id)
            msg = json.loads(raw)
            t = msg.get("t")
            async with emb_lock:
                if t == "click":
                    await page.mouse.click(float(msg["x"]), float(msg["y"]))
                elif t == "wheel":
                    await page.mouse.wheel(float(msg.get("dx", 0)), float(msg.get("dy", 0)))
                elif t == "navigate":
                    url = (msg.get("url") or "").strip()
                    if url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=_embedded_goto_timeout_ms())
                        async with rec.viewer_send_lock:
                            await websocket.send_text(json.dumps({"t": "navigated", "url": page.url}))
                elif t == "reload":
                    await page.reload(wait_until="domcontentloaded", timeout=_embedded_goto_timeout_ms())
                    async with rec.viewer_send_lock:
                        await websocket.send_text(json.dumps({"t": "navigated", "url": page.url}))
                elif t == "go_back":
                    await page.go_back()
                    async with rec.viewer_send_lock:
                        await websocket.send_text(json.dumps({"t": "navigated", "url": page.url}))
                elif t == "go_forward":
                    await page.go_forward()
                    async with rec.viewer_send_lock:
                        await websocket.send_text(json.dumps({"t": "navigated", "url": page.url}))
                elif t == "type":
                    await page.keyboard.type(str(msg.get("text") or ""), delay=int(msg.get("delay") or 20))
                elif t == "keydown":
                    await page.keyboard.down(str(msg.get("key") or ""))
                elif t == "keyup":
                    await page.keyboard.up(str(msg.get("key") or ""))
                elif t == "ping":
                    async with rec.viewer_send_lock:
                        await websocket.send_text(json.dumps({"t": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("ws loop end: %s", e)
    finally:
        async with _sessions_lock:
            r2 = _sessions.get(session_id)
            if r2 is not None and getattr(r2, "viewer_ws", None) is websocket:
                r2.viewer_ws = None
        stop.set()
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass
        try:
            await cdp.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            await cdp.detach()
        except Exception:
            pass
