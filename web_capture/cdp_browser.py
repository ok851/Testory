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


def _launch_debug_browser_unlocked(
    *,
    browser: str = "edge",
    port: Optional[int] = None,
    url: str = "",
    user_data_dir: str = "",
) -> Dict[str, Any]:
    snap = _snap()
    kind = (browser or "edge").strip().lower() or "edge"

    # 1) 复用本进程已跟踪且仍存活的实例
    if snap.get("browser_process") and snap.get("debug_port"):
        proc = snap["browser_process"]
        try:
            if proc.poll() is None:
                debug_port = int(snap["debug_port"])
                ws = fetch_cdp_ws(debug_port) or (snap.get("cdp_ws") or "")
                if ws:
                    _set(cdp_ws=ws)
                    return {
                        "success": True,
                        "already_running": True,
                        "debug_port": debug_port,
                        "cdp_ws": ws,
                        "executable": snap.get("executable") or "",
                    }
        except Exception:
            pass

    preferred = int(port or os.environ.get("WEB_CAPTURE_CDP_PORT", "9222") or 9222)

    # 2) 复用本机已在监听的 CDP（避免反复换端口新开浏览器）
    for candidate in (preferred, int(snap.get("debug_port") or 0)):
        if candidate <= 0:
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

    start_url = (url or "").strip() or "about:blank"
    args = [
        exe,
        f"--remote-debugging-port={debug_port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={udir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        start_url,
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
    }


def connect_playwright_over_cdp(debug_port: Optional[int] = None) -> Dict[str, Any]:
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
        page = context.pages[0] if context.pages else context.new_page()
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
    if stop_browser and proc:
        try:
            proc.terminate()
        except Exception:
            pass
    _set(
        browser_process=None if stop_browser else proc,
        playwright=None,
        browser=None,
        context=None,
        page=None,
    )
    return {"success": True}


def navigate(url: str) -> Dict[str, Any]:
    page = get_active_page()
    if not page:
        return {"success": False, "error": "未附加页面"}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        return {"success": True, "page_url": page.url}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
