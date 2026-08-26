# -*- coding: utf-8 -*-
"""
桌面嵌入式 Chromium（CEF / Electron / WebView2）CDP 元素捕获。

当 UIA 只能命中 Chrome_RenderWidgetHostHWND 等渲染容器时，尝试发现同进程
remote-debugging 端口，经 CDP 用 elementFromPoint / DOM.getNodeForLocation
提取应用内部 DOM 节点，生成可回放的结构化 selector。
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import urlopen

_cache_lock = threading.Lock()
_port_cache: Dict[int, Tuple[float, List[int]]] = {}  # pid -> (ts, ports)
_CACHE_TTL_SEC = 8.0


def _get_pid_from_hwnd(hwnd: int) -> int:
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return int(pid.value or 0)


def _window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    try:
        from modules.desktop.desktop_win32_snapshot import get_window_rect

        return get_window_rect(int(hwnd))
    except Exception:
        return None


def _is_embed_host_class(class_name: str) -> bool:
    low = (class_name or "").lower()
    keys = (
        "chrome_renderwidgethosthwnd",
        "chrome_widgetwin",
        "cefbrowserwindow",
        "cef",
        "electron",
        "webview",
        "intermediate d3d window",
    )
    return any(k in low for k in keys)


def discover_listening_ports_for_pid(pid: int) -> List[int]:
    """通过 netstat 找该进程监听的 TCP 端口。"""
    if pid <= 0:
        return []
    now = time.monotonic()
    with _cache_lock:
        hit = _port_cache.get(pid)
        if hit and now - hit[0] < _CACHE_TTL_SEC:
            return list(hit[1])

    ports: List[int] = []
    try:
        # -ano: 含 PID；在中文 Windows 上表头可能是「本地地址」
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        out = proc.stdout or ""
    except Exception:
        return []

    pid_s = str(pid)
    for line in out.splitlines():
        if "LISTENING" not in line.upper() and "侦听" not in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        if parts[-1] != pid_s:
            continue
        local = parts[1] if len(parts) > 1 else ""
        # 0.0.0.0:9222  / [::]:9222  / 127.0.0.1:9222
        m = re.search(r":(\d+)$", local)
        if not m:
            continue
        port = int(m.group(1))
        if 1 < port < 65535 and port not in ports:
            ports.append(port)

    ports.sort()
    with _cache_lock:
        _port_cache[pid] = (now, list(ports))
    return ports


def _http_json(url: str, timeout: float = 0.8) -> Optional[Any]:
    try:
        with urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else None
    except Exception:
        return None


def probe_cdp_port(port: int) -> Optional[Dict[str, Any]]:
    """探测端口是否为 Chrome DevTools；返回 version + targets。"""
    if not _tcp_open(port):
        return None
    version = _http_json(f"http://127.0.0.1:{int(port)}/json/version")
    if not isinstance(version, dict):
        return None
    if not (version.get("webSocketDebuggerUrl") or version.get("Browser")):
        # 有些嵌入式只暴露 /json/list
        targets = _http_json(f"http://127.0.0.1:{int(port)}/json/list")
        if not isinstance(targets, list) or not targets:
            return None
        return {"port": int(port), "version": version or {}, "targets": targets}
    targets = _http_json(f"http://127.0.0.1:{int(port)}/json/list") or []
    if not isinstance(targets, list):
        targets = []
    return {"port": int(port), "version": version, "targets": targets}


def _tcp_open(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _pick_page_ws(targets: List[Dict[str, Any]]) -> Optional[str]:
    preferred = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        typ = (t.get("type") or "").lower()
        ws = (t.get("webSocketDebuggerUrl") or "").strip()
        if not ws:
            continue
        if typ in ("page", "webview", "iframe", "other"):
            preferred.append((0 if typ in ("page", "webview") else 1, ws))
    if not preferred:
        return None
    preferred.sort(key=lambda x: x[0])
    return preferred[0][1]


def _cdp_call(ws_url: str, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 2.5) -> Optional[Dict[str, Any]]:
    try:
        import websocket
    except ImportError:
        return None

    payload = {
        "id": int(time.time() * 1000) % 1_000_000_000,
        "method": method,
        "params": params or {},
    }
    try:
        ws = websocket.create_connection(ws_url, timeout=timeout)
    except Exception:
        return None
    try:
        ws.send(json.dumps(payload))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = ws.recv()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("id") != payload["id"]:
                continue
            if msg.get("error"):
                return None
            result = msg.get("result")
            return result if isinstance(result, dict) else {"value": result}
    except Exception:
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return None


def _evaluate_element_at(ws_url: str, client_x: int, client_y: int) -> Optional[Dict[str, Any]]:
    """在页面坐标系用 elementFromPoint 取节点属性。"""
    expr = f"""(() => {{
  const x = {int(client_x)}, y = {int(client_y)};
  const el = document.elementFromPoint(x, y);
  if (!el) return null;
  let cur = el;
  // 向上找更有语义的可点节点
  for (let i = 0; i < 6 && cur; i++) {{
    const tag = (cur.tagName || '').toLowerCase();
    const role = (cur.getAttribute('role') || '').toLowerCase();
    const clickable = tag === 'button' || tag === 'a' || tag === 'input' || tag === 'textarea'
      || tag === 'select' || role === 'button' || role === 'link' || role === 'textbox'
      || cur.onclick != null || (cur.getAttribute('tabindex') != null);
    if (clickable || (cur.innerText || '').trim().length > 0) break;
    cur = cur.parentElement;
  }}
  if (!cur) cur = el;
  const r = cur.getBoundingClientRect();
  const text = ((cur.innerText || cur.value || cur.getAttribute('aria-label')
    || cur.getAttribute('title') || cur.getAttribute('placeholder') || '') + '').trim().slice(0, 120);
  return {{
    tag: (cur.tagName || '').toLowerCase(),
    id: cur.id || '',
    name: cur.getAttribute('name') || '',
    className: (typeof cur.className === 'string' ? cur.className : '') || '',
    text: text,
    ariaLabel: cur.getAttribute('aria-label') || '',
    role: cur.getAttribute('role') || '',
    type: cur.getAttribute('type') || '',
    href: cur.getAttribute('href') || '',
    testId: cur.getAttribute('data-testid') || cur.getAttribute('data-test') || '',
    rect: [r.left, r.top, r.right, r.bottom],
    outerHTML: (cur.outerHTML || '').slice(0, 200)
  }};
}})()"""
    # Runtime.evaluate
    result = _cdp_call(
        ws_url,
        "Runtime.evaluate",
        {"expression": expr, "returnByValue": True, "awaitPromise": False},
        timeout=2.5,
    )
    if not result:
        return None
    val = result.get("result", {}).get("value") if isinstance(result.get("result"), dict) else result.get("value")
    return val if isinstance(val, dict) else None


def _build_selector_from_dom(dom: Dict[str, Any]) -> Dict[str, Any]:
    key_candidates: List[Dict[str, Any]] = []
    test_id = (dom.get("testId") or "").strip()
    el_id = (dom.get("id") or "").strip()
    name = (dom.get("name") or "").strip()
    text = (dom.get("text") or "").strip()
    aria = (dom.get("ariaLabel") or "").strip()
    tag = (dom.get("tag") or "").strip()
    role = (dom.get("role") or "").strip()
    cls = (dom.get("className") or "").strip()

    if test_id:
        key_candidates.append({"property": "css", "value": f"[data-testid='{test_id}']", "match": "equals"})
    if el_id:
        key_candidates.append({"property": "css", "value": f"#{el_id}", "match": "equals"})
        key_candidates.append({"property": "dom-id", "value": el_id, "match": "equals"})
    if name:
        key_candidates.append({"property": "css", "value": f"[name='{name}']", "match": "equals"})
    if aria:
        key_candidates.append({"property": "aria-label", "value": aria, "match": "equals"})
    if text and len(text) <= 40:
        key_candidates.append({"property": "dom-text", "value": text, "match": "equals"})
    if role and tag:
        key_candidates.append({"property": "css", "value": f"{tag}[role='{role}']", "match": "equals"})
    if tag and cls:
        first_cls = cls.split()[0]
        if first_cls and len(first_cls) < 40:
            key_candidates.append({"property": "css", "value": f"{tag}.{first_cls}", "match": "equals"})
    if tag:
        key_candidates.append({"property": "dom-tag", "value": tag, "match": "equals"})

    return {
        "anchor_props": tag or "Element",
        "key_candidates": key_candidates,
        "parent_chain": [],
        "resolved_via": "embed_cdp",
        "dom": {
            "tag": tag,
            "id": el_id,
            "name": name,
            "text": text,
            "aria_label": aria,
            "role": role,
            "class_name": cls[:120],
            "test_id": test_id,
        },
    }


def capture_embed_element_at_point(screen_x: int, screen_y: int) -> Optional[Dict[str, Any]]:
    """
    尝试从嵌入式浏览器捕获点选元素。
    成功返回:
      {
        ok, element_label, control_type, bounding_rect, screen_center,
        element_snapshot: {selector, class_name}, resolved_via: embed_cdp,
        cdp_port, process_pid
      }
    """
    try:
        from modules.desktop.desktop_win32_snapshot import (
            get_process_name_from_hwnd,
            get_window_class_name,
            window_from_point,
            get_top_level_window,
            get_window_text,
        )
    except Exception:
        return None

    hwnd = window_from_point(int(screen_x), int(screen_y))
    if not hwnd:
        return None

    cls = ""
    try:
        cls = get_window_class_name(hwnd) or ""
    except Exception:
        cls = ""

    pid = _get_pid_from_hwnd(hwnd)
    if pid <= 0:
        return None

    ports = discover_listening_ports_for_pid(pid)
    # Testory 启动挂钩分配的端口优先
    try:
        from modules.desktop.desktop_embed_launch import get_active_embed_cdp_port

        hooked = int(get_active_embed_cdp_port() or 0)
        if hooked > 0 and hooked not in ports:
            ports = [hooked] + ports
        elif hooked > 0:
            ports = [hooked] + [p for p in ports if p != hooked]
    except Exception:
        pass
    preferred = [p for p in (9222, 9229, 9223, 9333) if p in ports]
    ordered = preferred + [p for p in ports if p not in preferred]

    host_rect = _window_rect(hwnd)
    try:
        top = get_top_level_window(hwnd)
        top_rect = _window_rect(top)
        # 渲染子控件矩形更接近视口；若无效则用顶层窗
        if not host_rect or (host_rect[2] - host_rect[0]) < 8:
            host_rect = top_rect
    except Exception:
        pass
    if not host_rect:
        return None

    client_x = max(0, int(screen_x) - int(host_rect[0]))
    client_y = max(0, int(screen_y) - int(host_rect[1]))

    proc_name = ""
    try:
        proc_name = get_process_name_from_hwnd(hwnd) or ""
    except Exception:
        pass

    proc_low = proc_name.lower()
    looks_embed = _is_embed_host_class(cls) or any(
        k in proc_low for k in ("electron", "msedgewebview", "cef", "chrome", "msedge", "webview")
    )
    if not looks_embed and not ordered:
        return None

    for port in ordered[:12]:
        info = probe_cdp_port(port)
        if not info:
            continue
        ws = _pick_page_ws(info.get("targets") or [])
        if not ws:
            ws = (info.get("version") or {}).get("webSocketDebuggerUrl") or ""
        if not ws:
            continue
        dom = _evaluate_element_at(ws, client_x, client_y)
        if not dom:
            own = _window_rect(hwnd)
            if own and own != host_rect:
                cx2 = max(0, int(screen_x) - int(own[0]))
                cy2 = max(0, int(screen_y) - int(own[1]))
                dom = _evaluate_element_at(ws, cx2, cy2)
        if not dom:
            continue

        selector = _build_selector_from_dom(dom)
        label = (
            (dom.get("text") or "").strip()
            or (dom.get("ariaLabel") or "").strip()
            or (dom.get("id") or "").strip()
            or (dom.get("tag") or "element")
        )
        rect_c = dom.get("rect") or [client_x - 20, client_y - 12, client_x + 20, client_y + 12]
        try:
            l = int(host_rect[0] + rect_c[0])
            t = int(host_rect[1] + rect_c[1])
            r = int(host_rect[0] + rect_c[2])
            b = int(host_rect[1] + rect_c[3])
        except Exception:
            l, t, r, b = int(screen_x) - 24, int(screen_y) - 16, int(screen_x) + 24, int(screen_y) + 16

        if r <= l or b <= t:
            l, t, r, b = int(screen_x) - 24, int(screen_y) - 16, int(screen_x) + 24, int(screen_y) + 16

        title = ""
        try:
            title = get_window_text(get_top_level_window(hwnd)) or ""
        except Exception:
            pass

        return {
            "ok": True,
            "element_label": label,
            "control_type": (dom.get("tag") or "Element"),
            "bounding_rect": (l, t, r, b),
            "screen_center": ((l + r) // 2, (t + b) // 2),
            "element_snapshot": {
                "selector": selector,
                "class_name": (dom.get("className") or "")[:120],
            },
            "window_title": title,
            "process_name": proc_name,
            "resolved_via": "embed_cdp",
            "cdp_port": int(port),
            "process_pid": int(pid),
            "message": f"嵌入式 DOM via CDP :{port}",
        }

    return None


def _first_css_candidate(selector: Dict[str, Any]) -> str:
    for cand in selector.get("key_candidates") or []:
        prop = (cand.get("property") or "").lower()
        val = (cand.get("value") or "").strip()
        if not val:
            continue
        if prop in ("css", "dom-id"):
            if prop == "dom-id" and not val.startswith("#"):
                return f"#{val}"
            return val
        if prop == "aria-label":
            return f"[aria-label='{val}']"
    return ""


def resolve_embed_click_point(element_snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """按录制时的 embed_cdp selector 在仍开启调试端口的应用内重定位。"""
    if not isinstance(element_snapshot, dict):
        return None
    sel = element_snapshot.get("selector") or element_snapshot
    if not isinstance(sel, dict):
        return None
    if (sel.get("resolved_via") or "") != "embed_cdp":
        return None

    css = _first_css_candidate(sel)
    dom_meta = sel.get("dom") or {}
    text = (dom_meta.get("text") or "").strip()
    if not css and not text:
        for cand in sel.get("key_candidates") or []:
            if (cand.get("property") or "").lower() == "dom-text":
                text = (cand.get("value") or "").strip()
                break
    if not css and not text:
        return None

    ports_try = [9222, 9229, 9223, 9333]
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if hwnd:
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
            if pid.value:
                ports_try = list(
                    dict.fromkeys(ports_try + discover_listening_ports_for_pid(int(pid.value)))
                )
    except Exception:
        pass

    js_css = json.dumps(css)
    js_text = json.dumps(text)
    expr = f"""(() => {{
  let el = null;
  const css = {js_css};
  const text = {js_text};
  if (css) {{
    try {{ el = document.querySelector(css); }} catch (e) {{ el = null; }}
  }}
  if (!el && text) {{
    const all = Array.from(document.querySelectorAll(
      'button,a,input,textarea,[role="button"],[aria-label],span,div,li'));
    el = all.find(n => ((n.innerText||n.value||n.getAttribute('aria-label')||'')+'')
      .includes(text)) || null;
  }}
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {{ rect: [r.left, r.top, r.right, r.bottom] }};
}})()"""

    for port in ports_try[:16]:
        info = probe_cdp_port(port)
        if not info:
            continue
        ws = _pick_page_ws(info.get("targets") or [])
        if not ws:
            ws = (info.get("version") or {}).get("webSocketDebuggerUrl") or ""
        if not ws:
            continue
        result = _cdp_call(
            ws,
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True},
            timeout=2.5,
        )
        if not result:
            continue
        val = (
            result.get("result", {}).get("value")
            if isinstance(result.get("result"), dict)
            else result.get("value")
        )
        if not isinstance(val, dict) or not val.get("rect"):
            continue
        rect_c = val["rect"]
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            host = _window_rect(int(hwnd)) if hwnd else None
        except Exception:
            host = None
        if not host:
            continue
        l = int(host[0] + rect_c[0])
        t = int(host[1] + rect_c[1])
        r = int(host[0] + rect_c[2])
        b = int(host[1] + rect_c[3])
        if r <= l or b <= t:
            continue
        return {
            "ok": True,
            "x": (l + r) // 2,
            "y": (t + b) // 2,
            "bounding_rect": (l, t, r, b),
            "resolved_via": "embed_cdp",
            "cdp_port": int(port),
        }
    return None
