# -*- coding: utf-8 -*-
"""
FastAPI 网关：每会话独立 Chromium（Playwright），CDP Screencast 推流，
WebSocket 同步点击/滚动/导航/键盘。

环境变量：
  EMBEDDED_BROWSER_GATEWAY_SECRET  与 Flask 共用，必填。
  EMBEDDED_BROWSER_IDLE_SEC        无 WS 活动回收秒数，默认 1800。
  EMBEDDED_BROWSER_GATE_PORT       监听端口，默认 8765。
  PLAYWRIGHT_HEADLESS              默认 1。
  PLAYWRIGHT_BROWSER               默认 chromium；可选 chrome / edge / firefox / webkit（与主站一致）。
  EMBEDDED_INSPECT_EVAL_RETRIES    inspect 快照遇「导航销毁上下文」时重试次数，默认 6。
  EMBEDDED_INSPECT_DOM_WAIT_MS     每次 evaluate 前 wait_for_load_state(domcontentloaded) 超时毫秒，默认 8000。

HTTP：POST /internal/session/{session_id}/run-steps
  请求体 JSON：{"steps":[...]}，steps 与主站 ai_plan_steps_to_playwright_script_steps 输出一致（navigate/click/input/wait/assert/verify 等），
  在远程画布同一会话内串行执行；与 WebSocket 输入共用 run_lock，避免与画布点击交错冲突。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

logger = logging.getLogger(__name__)


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


async def _launch_playwright_browser(p: Playwright, headless: bool, engine: str) -> Browser:
    base_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    eng = _normalize_embedded_browser(engine)
    if eng == "firefox":
        return await p.firefox.launch(headless=headless)
    if eng == "webkit":
        return await p.webkit.launch(headless=headless)
    if eng == "chromium":
        return await p.chromium.launch(headless=headless, args=base_args)
    channel = "msedge" if eng == "edge" else "chrome"
    return await p.chromium.launch(headless=headless, args=base_args, channel=channel)

# 与 playwright_automation.get_interactive_page_snapshot 中 evaluate 保持一致，便于 AI/侧栏对齐。
INTERACTIVE_SNAPSHOT_JS = """(n) => {
    const v = { width: window.innerWidth, height: window.innerHeight };
    const set = 'a, button, input, textarea, select, [role=button], [role=link], [role=tab], [role=searchbox]';
    const nodes = Array.from(document.querySelectorAll(set));
    const out = [];
    for (const el of nodes) {
        if (out.length >= n) break;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') continue;
        const r = el.getBoundingClientRect();
        if (r.width < 1 && r.height < 1) continue;
        if (r.bottom < 0 || r.top > v.height || r.right < 0 || r.left > v.width) continue;
        const tag = (el.tagName || '').toLowerCase();
        const idv = (el.id || '').toString();
        const cn = (el.className && typeof el.className === 'string') ? el.className : '';
        const cls = cn.split(/\\s+/).filter(c => c && c.length < 50).slice(0, 2);
        const dt = (el.getAttribute('data-testid') || el.getAttribute('data-test') || '');
        const nm = (el.getAttribute('name') || '');
        const tx = (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
        const ph = (el.getAttribute('placeholder') || '') || '';
        const al = (el.getAttribute('aria-label') || '') || '';
        let suggest = '';
        if (idv) suggest = tag + '#' + idv;
        else if (dt) suggest = tag + '[data-testid="' + String(dt).replace(/"/g, '\\\\"') + '"]';
        else if (nm) suggest = tag + '[name="' + String(nm).replace(/"/g, '\\\\"') + '"]';
        else if (cls.length) suggest = tag + '.' + cls.join('.');
        else if (al) suggest = tag + '[aria-label="' + al.slice(0, 40).replace(/"/g, '\\\\"') + '"]';
        else if (ph) suggest = tag + '[placeholder="' + ph.slice(0, 32).replace(/"/g, '\\\\"') + '"]';
        else suggest = tag;
        out.push({
            n: out.length + 1,
            tag,
            id: idv || null,
            class: cls.join(' ') || null,
            name: nm || null,
            type: (el.getAttribute('type') || '') || null,
            href: (el.getAttribute('href') || '') || null,
            role: (el.getAttribute('role') || '') || null,
            text: tx || null,
            placeholder: ph || null,
            ariaLabel: al || null,
            dataTestid: dt || null,
            box: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
            suggestedSelector: suggest
        });
    }
    return {
        url: window.location.href,
        title: (document.title || '') || '',
        viewport: v,
        items: out,
        source: 'embedded_gateway'
    };
}"""

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
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
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
        try:
            await el.fill(text, timeout=15000)
        except Exception:
            await el.click(timeout=5000)
            await page.keyboard.type(text, delay=15)
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
        ct = (step.get("compare_type") or "text_contains").strip().lower()
        exp = str(step.get("input_value") or step.get("text") or "").strip()
        if ct in ("url_equals", "url_contains"):
            u = page.url
            if ct == "url_equals" and not _url_assert_matches_embed(u, exp, "url_equals"):
                raise RuntimeError(f"url_equals 期望 {exp!r} 当前 {u!r}")
            if ct == "url_contains" and exp and not _url_assert_matches_embed(u, exp, "url_contains"):
                raise RuntimeError(f"url_contains 期望子串 {exp!r} 不在当前 {u!r}")
            return
        sel = (step.get("selector") or "").strip()
        st = step.get("selector_type") or "css"
        if not sel:
            raise ValueError("assert 需要 selector（url_* 除外）")
        loc = _embedded_locator(page, sel, st)
        el = loc.first
        await el.wait_for(state="visible", timeout=20000)
        txt = (await el.inner_text() or "").strip()
        if ct in ("text_equals", "equals"):
            if exp and txt != exp:
                raise RuntimeError(f"text_equals 期望 {exp!r} 实际 {txt!r}")
        elif ct in ("text_contains", "contains", ""):
            if exp and exp not in txt:
                raise RuntimeError(f"text_contains 期望包含 {exp!r} 实际 {txt!r}")
        else:
            if exp and exp not in txt:
                raise RuntimeError(f"assert({ct}) 未匹配: 期望涉及 {exp!r} 文案 {txt!r}")
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
        "hint": "本进程为 UI 平台配套网关；请勿用浏览器完成业务操作。健康检查请访问 /health。",
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
    headless = os.environ.get("PLAYWRIGHT_HEADLESS", "1").strip().lower() not in ("0", "false", "no")
    browser_hint = (body.get("browser") or body.get("engine") or "").strip() or None

    sid = uuid.uuid4().hex
    tok = secrets.token_urlsafe(32)

    p = await async_playwright().start()
    browser = await _launch_playwright_browser(p, headless, browser_hint or "")
    logger.info(
        "session %s using engine %s (hint=%s)",
        sid,
        _normalize_embedded_browser(browser_hint or ""),
        browser_hint,
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        ignore_https_errors=True,
    )
    page = await context.new_page()
    if initial_url:
        try:
            await page.goto(initial_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            logger.warning("initial goto failed: %s", e)

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
    return {"session_id": sid, "ws_token": tok}


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
            raw = await page.evaluate(INTERACTIVE_SNAPSHOT_JS, n)
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
    n = max(20, min(150, 200))
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
                        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        await websocket.send_text(json.dumps({"t": "navigated", "url": page.url}))
                elif t == "reload":
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                    await websocket.send_text(json.dumps({"t": "navigated", "url": page.url}))
                elif t == "go_back":
                    await page.go_back()
                    await websocket.send_text(json.dumps({"t": "navigated", "url": page.url}))
                elif t == "go_forward":
                    await page.go_forward()
                    await websocket.send_text(json.dumps({"t": "navigated", "url": page.url}))
                elif t == "type":
                    await page.keyboard.type(str(msg.get("text") or ""), delay=int(msg.get("delay") or 20))
                elif t == "keydown":
                    await page.keyboard.down(str(msg.get("key") or ""))
                elif t == "keyup":
                    await page.keyboard.up(str(msg.get("key") or ""))
                elif t == "ping":
                    await websocket.send_text(json.dumps({"t": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("ws loop end: %s", e)
    finally:
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
