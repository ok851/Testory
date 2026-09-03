# -*- coding: utf-8 -*-
"""CDP 浏览器检测、启动与 Playwright 连接。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import urlopen

_lock = threading.RLock()
_state: Dict[str, Any] = {
    "browser_process": None,
    "debug_port": 0,
    "browser_kind": "",
    "executable": "",
    "playwright": None,
    "browser": None,
    "context": None,
    "page": None,
    "cdp_ws": "",
    "user_data_dir": "",
    "pending_start_url": "",
}


def _set(**kwargs: Any) -> None:
    with _lock:
        _state.update(kwargs)


def _snap() -> Dict[str, Any]:
    with _lock:
        return dict(_state)


def pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _tcp_port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def fetch_cdp_ws(debug_port: int) -> Optional[str]:
    url = f"http://127.0.0.1:{debug_port}/json/version"
    try:
        with urlopen(url, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return (data.get("webSocketDebuggerUrl") or "").strip() or None
    except Exception:
        return None


def _wait_cdp_ws(debug_port: int, *, timeout_sec: float = 15.0) -> Optional[str]:
    deadline = time.monotonic() + max(3.0, float(timeout_sec))
    while time.monotonic() < deadline:
        ws = fetch_cdp_ws(debug_port)
        if ws:
            return ws
        time.sleep(0.15)
    return None


def _adopt_existing_cdp(debug_port: int, *, kind: str = "") -> Optional[Dict[str, Any]]:
    ws = fetch_cdp_ws(debug_port)
    if not ws:
        return None
    _set(
        debug_port=int(debug_port),
        cdp_ws=ws,
        browser_kind=kind or _snap().get("browser_kind") or "",
        # 外部已有调试浏览器：不持有 Popen，仅记录端口
        browser_process=_snap().get("browser_process"),
    )
    return {
        "success": True,
        "already_running": True,
        "debug_port": int(debug_port),
        "cdp_ws": ws,
        "executable": _snap().get("executable") or "",
    }


def fetch_cdp_pages(debug_port: int) -> List[Dict[str, Any]]:
    url = f"http://127.0.0.1:{debug_port}/json/list"
    try:
        with urlopen(url, timeout=3.0) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        if not isinstance(raw, list):
            return []
        out = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            if item.get("type") not in ("page", "webview"):
                continue
            out.append(
                {
                    "index": len(out),
                    "id": item.get("id") or "",
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "webSocketDebuggerUrl": item.get("webSocketDebuggerUrl") or "",
                }
            )
        return out
    except Exception:
        return []


def _is_blank_page_url(url: str) -> bool:
    """狭义空白页（about:blank / chrome|edge newtab scheme）。"""
    u = (url or "").strip().lower()
    if not u:
        return True
    return u in (
        "about:blank",
        "chrome://newtab/",
        "chrome://new-tab-page/",
        "edge://newtab/",
        "edge://new-tab-page/",
        "about:newtab",
    ) or u.startswith("chrome://newtab") or u.startswith("edge://newtab")


def _is_idle_startup_page(url: str) -> bool:
    """启动时多开的「看起来正常、但未做业务导航」的页（含 Edge NTP 首页）。

    用户反馈：多余标签不是 about:blank，而是正常浏览器页、地址栏无业务导航。
    Edge 常见为 https://ntp.msn.com/... ，Chrome 为 chrome://new-tab-page。
    """
    if _is_blank_page_url(url):
        return True
    u = (url or "").strip().lower()
    if not u:
        return True
    # Edge / MSN 新标签页（外观完全像正常网页）
    if "ntp.msn." in u or "/edge/ntp" in u or "ntp.msn.cn" in u:
        return True
    if "chrome://new-tab" in u or "edge://new-tab" in u:
        return True
    # Chrome WebUI 起始页
    if u.startswith("chrome://") or u.startswith("edge://") or u.startswith("devtools://"):
        return True
    return False


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return (urlparse(url or "").netloc or "").strip().lower()
    except Exception:
        return ""


def close_blank_cdp_targets(debug_port: int, *, keep_url_substr: str = "") -> int:
    """关闭空白/NTP/非目标页；有目标 URL 时只保留目标站标签。"""
    port = int(debug_port or 0)
    if port <= 0:
        return 0
    return close_idle_or_non_target_tabs(port, target_url=keep_url_substr)


def close_idle_or_non_target_tabs(debug_port: int, *, target_url: str = "") -> int:
    """经 CDP HTTP 关掉启动多余页（NTP 等）以及非目标站标签。"""
    port = int(debug_port or 0)
    if port <= 0:
        return 0
    pages = fetch_cdp_pages(port)
    if len(pages) <= 1:
        # 仍可能是唯一一页 NTP；无目标时不关，有目标且唯一页是 idle 则留给 goto
        return 0

    target = (target_url or "").strip()
    target_host = _host_of(target)
    keep_ids: set[str] = set()

    if target_host:
        for p in pages:
            tid = str(p.get("id") or "").strip()
            u = str(p.get("url") or "")
            if tid and _host_of(u) == target_host and not _is_idle_startup_page(u):
                keep_ids.add(tid)
        # 同 host 但尚无精确匹配时：保留含 target 子串的页
        if not keep_ids and target:
            tlow = target.lower()
            for p in pages:
                tid = str(p.get("id") or "").strip()
                u = str(p.get("url") or "").lower()
                if tid and tlow.rstrip("/") in u.rstrip("/") and not _is_idle_startup_page(u):
                    keep_ids.add(tid)

    if not keep_ids:
        # 无明确目标页：至少保留一个非 idle；其余 idle 全关
        for p in pages:
            tid = str(p.get("id") or "").strip()
            if tid and not _is_idle_startup_page(str(p.get("url") or "")):
                keep_ids.add(tid)
                break
        if not keep_ids and pages:
            # 全是 idle：保留第一个，关掉其余
            keep_ids.add(str(pages[0].get("id") or ""))

    closed = 0
    for p in pages:
        tid = str(p.get("id") or "").strip()
        if not tid or tid in keep_ids:
            continue
        url = str(p.get("url") or "")
        # 有目标保留集：关掉其它一切（含「正常外观」的 NTP）
        # 无目标：只关 idle
        should_close = bool(keep_ids) or _is_idle_startup_page(url)
        if not should_close:
            continue
        if len(pages) - closed <= 1:
            break
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/close/{tid}", timeout=2.0) as resp:
                resp.read()
            closed += 1
        except Exception:
            continue
    return closed


def pick_best_page(context, *, prefer_url: str = "") -> Any:
    """优先选择业务页；跳过 Edge NTP 等「看起来正常但未导航」的启动页。"""
    pages = list(getattr(context, "pages", None) or [])
    if not pages:
        return None
    prefer_host = _host_of(prefer_url)

    def _score(p: Any) -> int:
        try:
            u = str(p.url or "")
        except Exception:
            return -10
        if _is_idle_startup_page(u):
            return -5
        host = _host_of(u)
        if prefer_host and host == prefer_host:
            return 100
        if u.lower().startswith(("http://", "https://")):
            return 50
        if not _is_blank_page_url(u):
            return 10
        return 0

    ranked = sorted(pages, key=_score, reverse=True)
    return ranked[0] if ranked else pages[0]


def close_extra_blank_tabs(context, keep_page: Any = None, *, target_url: str = "") -> int:
    """关闭 NTP/空白/非目标标签，保留 keep_page 与目标站。"""
    closed = 0
    keep_u = (target_url or "").strip()
    if keep_page is not None and not keep_u:
        try:
            keep_u = str(keep_page.url or "")
        except Exception:
            keep_u = ""
    keep_host = _host_of(keep_u)

    try:
        pages = list(getattr(context, "pages", None) or [])
    except Exception:
        pages = []
    if len(pages) > 1:
        for p in pages:
            try:
                if keep_page is not None and p is keep_page:
                    continue
                u = str(p.url or "")
                if _is_idle_startup_page(u):
                    p.close()
                    closed += 1
                    continue
                if keep_host and _host_of(u) and _host_of(u) != keep_host:
                    p.close()
                    closed += 1
            except Exception:
                continue
    try:
        port = int(_snap().get("debug_port") or 0)
        closed += close_idle_or_non_target_tabs(port, target_url=keep_u)
    except Exception:
        pass
    return closed


def maximize_debug_browser_window(
    *,
    debug_port: int = 0,
    process_pid: int = 0,
    page: Any = None,
) -> bool:
    """通过 CDP 协议最大化浏览器窗口（不抢焦点）。

    仅使用 CDP Browser.setWindowBounds，不调用 Win32 ShowWindow/SetForegroundWindow，
    避免浏览器窗口抢占用户鼠标和前台焦点。
    """
    ok = False
    if page is not None:
        try:
            cdp = page.context.new_cdp_session(page)
            wid = None
            try:
                info = cdp.send("Browser.getWindowForTarget")
                wid = (info or {}).get("windowId")
            except Exception:
                wid = None
            if wid is None:
                port = int(debug_port or _snap().get("debug_port") or 0)
                for item in fetch_cdp_pages(port) if port else []:
                    tid = str(item.get("id") or "")
                    if not tid:
                        continue
                    try:
                        info = cdp.send("Browser.getWindowForTarget", {"targetId": tid})
                        wid = (info or {}).get("windowId")
                        if wid is not None:
                            break
                    except Exception:
                        continue
            if wid is not None:
                cdp.send(
                    "Browser.setWindowBounds",
                    {"windowId": wid, "bounds": {"windowState": "maximized"}},
                )
                ok = True
        except Exception:
            pass
    return ok


def _registry_browser_path(kind: str) -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        if kind == "edge":
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
            )
        else:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
            )
        path, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        return path if path and os.path.isfile(path) else None
    except Exception:
        return None


def detect_browser_executable(kind: str = "edge") -> Optional[str]:
    k = (kind or "edge").strip().lower()
    reg = _registry_browser_path("edge" if k == "edge" else "chrome")
    if reg:
        return reg
    candidates = []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    if k == "edge":
        candidates.extend(
            [
                os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(pfx86, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe"),
            ]
        )
    else:
        candidates.extend(
            [
                os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(pfx86, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
            ]
        )
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def launch_debug_browser(
    *,
    browser: str = "edge",
    port: Optional[int] = None,
    url: str = "",
    user_data_dir: str = "",
) -> Dict[str, Any]:
    """启动带 remote-debugging-port 的 Chrome/Edge（固定优先 9222，可复用已有 CDP）。"""
    with _lock:
        return _launch_debug_browser_unlocked(
            browser=browser, port=port, url=url, user_data_dir=user_data_dir
        )


def _tracked_browser_process_alive() -> bool:
    snap = _snap()
    proc = snap.get("browser_process")
    if proc is None:
        return False
    try:
        return proc.poll() is None
    except Exception:
        return False


def _launch_debug_browser_unlocked(
    *,
    browser: str = "edge",
    port: Optional[int] = None,
    url: str = "",
    user_data_dir: str = "",
) -> Dict[str, Any]:
    snap = _snap()
    kind = (browser or "edge").strip().lower() or "edge"

    # 1) 复用本进程已跟踪且仍存活的实例（进程 + CDP 双检；用户关窗后 poll()!=None 或 CDP 不通）
    if snap.get("browser_process") and snap.get("debug_port"):
        proc = snap["browser_process"]
        try:
            alive = proc.poll() is None
            debug_port = int(snap["debug_port"])
            ws = fetch_cdp_ws(debug_port) if alive else None
            if alive and ws:
                _set(cdp_ws=ws)
                return {
                    "success": True,
                    "already_running": True,
                    "debug_port": debug_port,
                    "cdp_ws": ws,
                    "executable": snap.get("executable") or "",
                }
            # 进程已退 / CDP 不通：清掉残留状态，避免一直 already_running
            _set(
                browser_process=None,
                cdp_ws="",
                debug_port=0 if not ws else debug_port,
                page=None,
                context=None,
                browser=None,
            )
        except Exception:
            _set(browser_process=None, cdp_ws="", page=None, context=None, browser=None)

    preferred = int(port or os.environ.get("WEB_CAPTURE_CDP_PORT", "9222") or 9222)

    # 2) 复用本机已在监听的 CDP（必须真有可交互 page；纯端口开着但无页则不 adopt）
    for candidate in (preferred, int(_snap().get("debug_port") or 0)):
        if candidate <= 0:
            continue
        ws = fetch_cdp_ws(candidate)
        if not ws:
            continue
        pages = fetch_cdp_pages(candidate)
        if not pages:
            continue
        adopted = _adopt_existing_cdp(candidate, kind=kind)
        if adopted:
            return adopted

    exe = detect_browser_executable(kind)
    if not exe:
        return {"success": False, "error": f"未找到 {kind} 可执行文件"}

    # 3) 选择端口：优先 preferred；若被非 CDP 占用则另选空闲端口（只选一次）
    debug_port = preferred
    if _tcp_port_open(debug_port) and not fetch_cdp_ws(debug_port):
        debug_port = pick_free_port()

    # 稳定 profile，避免每次换 pid 目录导致连点狂开新实例
    udir = user_data_dir or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP", "."),
        "Testory",
        "cdp-browser-profile",
    )
    os.makedirs(udir, exist_ok=True)

    start_url = (url or "").strip()
    # 有业务 URL 时命令行直接打开目标页，避免先 about:blank 再二次导航。
    # 若 Edge 额外弹出新标签页，后续 close_blank_cdp_targets / pick_best_page 会收敛。
    initial_page = start_url if start_url.lower().startswith(("http://", "https://")) else "about:blank"
    args = [
        exe,
        f"--remote-debugging-port={debug_port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={udir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--window-size=1920,1080",
        "--window-position=0,0",
        "--disable-features=TranslateUI,InfiniteSessionRestore",
        "--noerrdialogs",
        f"--homepage={initial_page}",
        initial_page,
    ]

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc) or "启动浏览器失败"}

    ws = _wait_cdp_ws(debug_port, timeout_sec=15.0)
    if not ws:
        try:
            if sys.platform == "win32" and proc.pid:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=8,
                )
            else:
                proc.kill()
        except Exception:
            pass
        return {
            "success": False,
            "error": (
                f"浏览器已启动但无法连接 CDP 端口 {debug_port}。"
                "请关闭多余的 Edge/Chrome 调试窗口后重试，或确认未被安全软件拦截。"
            ),
            "debug_port": debug_port,
        }

    _set(
        browser_process=proc,
        debug_port=debug_port,
        browser_kind=kind,
        executable=exe,
        cdp_ws=ws,
        user_data_dir=udir,
        pending_start_url=start_url,
        playwright=None,
        browser=None,
        context=None,
        page=None,
    )
    return {
        "success": True,
        "debug_port": debug_port,
        "cdp_ws": ws,
        "executable": exe,
        "user_data_dir": udir,
        "maximized": True,
        "pending_start_url": start_url,
    }


def connect_playwright_over_cdp(
    debug_port: Optional[int] = None, *, prefer_url: str = ""
) -> Dict[str, Any]:
    snap = _snap()
    port = int(debug_port or snap.get("debug_port") or 0)
    if not port:
        return {"success": False, "error": "未指定 CDP 端口"}
    ws = fetch_cdp_ws(port)
    if not ws:
        return {"success": False, "error": f"无法连接 localhost:{port}"}

    try:
        from playwright.sync_api import sync_playwright

        if snap.get("playwright"):
            try:
                snap["playwright"].stop()
            except Exception:
                pass
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(ws)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = pick_best_page(context, prefer_url=prefer_url)
        if page is None:
            pages = list(getattr(context, "pages", None) or [])
            page = pages[0] if pages else context.new_page()
        # 不再以「关标签」为主策略；单标签启动 + goto 才是正解
        _set(
            playwright=pw,
            browser=browser,
            context=context,
            page=page,
            cdp_ws=ws,
            debug_port=port,
        )
        return {
            "success": True,
            "debug_port": port,
            "page_url": page.url,
            "pages_count": len(context.pages),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc) or "Playwright CDP 连接失败"}


def list_pages() -> Dict[str, Any]:
    snap = _snap()
    port = int(snap.get("debug_port") or 0)
    pages = fetch_cdp_pages(port) if port else []
    ctx = snap.get("context")
    if ctx:
        try:
            for i, p in enumerate(ctx.pages):
                if i < len(pages):
                    pages[i]["playwright_index"] = i
                else:
                    pages.append(
                        {
                            "index": len(pages),
                            "playwright_index": i,
                            "title": "",
                            "url": p.url,
                            "id": "",
                        }
                    )
        except Exception:
            pass
    return {"success": True, "pages": pages, "debug_port": port}


def attach_page(*, page_index: int = 0, target_id: str = "") -> Dict[str, Any]:
    snap = _snap()
    ctx = snap.get("context")
    browser = snap.get("browser")
    if not browser:
        conn = connect_playwright_over_cdp()
        if not conn.get("success"):
            return conn
        snap = _snap()
        ctx = snap.get("context")
        browser = snap.get("browser")
    if not ctx:
        return {"success": False, "error": "无浏览器上下文"}

    try:
        if target_id:
            port = int(snap.get("debug_port") or 0)
            for item in fetch_cdp_pages(port):
                if item.get("id") == target_id:
                    page_index = int(item.get("playwright_index", item.get("index", 0)))
                    break
        pages = ctx.pages
        if not pages:
            page = ctx.new_page()
        elif page_index < 0 or page_index >= len(pages):
            page = pages[0]
        else:
            page = pages[page_index]
        _set(page=page)
        return {"success": True, "page_url": page.url, "page_index": page_index}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_active_page():
    snap = _snap()
    return snap.get("page")


def disconnect(*, stop_browser: bool = False) -> Dict[str, Any]:
    snap = _snap()
    try:
        if snap.get("browser"):
            snap["browser"].close()
    except Exception:
        pass
    try:
        if snap.get("playwright"):
            snap["playwright"].stop()
    except Exception:
        pass
    proc = snap.get("browser_process")
    if stop_browser and proc is not None:
        try:
            pid = int(getattr(proc, "pid", 0) or 0)
        except Exception:
            pid = 0
        try:
            if sys.platform == "win32" and pid > 0:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        except Exception:
            pass
    # 用户手动关窗后进程可能已死，但 debug_port / cdp_ws 仍残留 → 后续误判「还在跑」
    if stop_browser:
        _set(
            browser_process=None,
            playwright=None,
            browser=None,
            context=None,
            page=None,
            cdp_ws="",
            debug_port=0,
            pending_start_url="",
        )
    else:
        _set(
            browser_process=proc,
            playwright=None,
            browser=None,
            context=None,
            page=None,
        )
    return {"success": True}


def cdp_endpoint_reachable(debug_port: int = 0) -> bool:
    """CDP HTTP /json/version 是否可达（窗口关闭后通常为 False）。"""
    port = int(debug_port or (_snap().get("debug_port") or 0) or 0)
    if port <= 0:
        return False
    return bool(fetch_cdp_ws(port))


def navigate(url: str) -> Dict[str, Any]:
    page = get_active_page()
    if not page:
        return {"success": False, "error": "未附加页面"}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        return {"success": True, "page_url": page.url}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
