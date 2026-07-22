# -*- coding: utf-8 -*-
"""Probe which HWND receives WM_CHAR for WeChat search. No messaging contacts."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import time
from ctypes import wintypes
from typing import Any, Dict, List, Optional

TOKEN = "QWER99"
OUT: Dict[str, Any] = {"token": TOKEN, "phases": {}}

CWP_SKIPINVISIBLE = 0x0001
CWP_SKIPDISABLED = 0x0002
CWP_SKIPTRANSPARENT = 0x0004


def jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def user32():
    return ctypes.windll.user32


def hwnd_info(hwnd: int) -> Dict[str, Any]:
    u = user32()
    hwnd = int(hwnd or 0)
    if not hwnd:
        return {"hwnd": 0}
    buf = ctypes.create_unicode_buffer(512)
    u.GetClassNameW(hwnd, buf, 512)
    cls = buf.value
    tbuf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, tbuf, 512)
    title = tbuf.value
    parent = int(u.GetParent(hwnd) or 0)
    root = int(u.GetAncestor(hwnd, 2) or 0)  # GA_ROOT = 2
    owner = int(u.GetWindow(hwnd, 4) or 0)  # GW_OWNER = 4
    rect = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    return {
        "hwnd": hwnd,
        "hwnd_hex": hex(hwnd),
        "class": cls,
        "title": title,
        "parent": parent,
        "parent_hex": hex(parent) if parent else "0x0",
        "root": root,
        "root_hex": hex(root) if root else "0x0",
        "owner": owner,
        "rect": {"l": rect.left, "t": rect.top, "r": rect.right, "b": rect.bottom},
    }


def chain_from(hwnd: int) -> List[Dict[str, Any]]:
    chain: List[Dict[str, Any]] = []
    seen = set()
    cur = int(hwnd or 0)
    while cur and cur not in seen:
        seen.add(cur)
        info = hwnd_info(cur)
        chain.append(info)
        cur = int(info.get("parent") or 0)
    return chain


def token_in_text(text: str) -> bool:
    t = (text or "").upper().replace(" ", "").replace("\n", "")
    needle = TOKEN.upper()
    if needle in t:
        return True
    compact = "".join(ch for ch in t if ch.isalnum())
    # OCR may misread; accept near-misses with all 6 chars in order loosely
    if needle in compact:
        return True
    # also check Q W E R 9 9 scattered
    idx = 0
    for ch in needle:
        pos = compact.find(ch, idx)
        if pos < 0:
            return False
        idx = pos + 1
    return True


def ocr_after(hwnd: int, label: str) -> Dict[str, Any]:
    from desktop_ocr import extract_text_from_bytes
    from screen_tools import capture_hwnd_png, get_screen_text

    result: Dict[str, Any] = {"label": label}
    png, meta = capture_hwnd_png(hwnd)
    result["capture_meta"] = meta
    result["png_bytes"] = len(png or b"")
    path = os.path.join(tempfile.gettempdir(), f"wechat_wmchar_{label}.png")
    if png:
        with open(path, "wb") as f:
            f.write(png)
        result["png_path"] = path
    text = ""
    try:
        text = extract_text_from_bytes(png) if png else ""
        result["extract_text"] = text
        result["token_in_extract"] = token_in_text(text)
    except Exception as e:
        result["extract_error"] = f"{type(e).__name__}: {e}"
        result["token_in_extract"] = False
    try:
        gst = get_screen_text()
        joined = ""
        if isinstance(gst, dict):
            parts = []
            for k in ("text", "full_text", "ocr_text"):
                if gst.get(k):
                    parts.append(str(gst.get(k)))
            for b in gst.get("blocks") or gst.get("texts") or gst.get("items") or []:
                if isinstance(b, dict):
                    parts.append(str(b.get("text") or b.get("content") or ""))
                else:
                    parts.append(str(b))
            joined = "\n".join(parts)
        result["get_screen_text_joined"] = (joined or "")[:3000]
        result["token_in_gst"] = token_in_text(joined) or token_in_text(jdump(gst))
    except Exception as e:
        result["gst_error"] = f"{type(e).__name__}: {e}"
        result["token_in_gst"] = False
    result["token_seen"] = bool(result.get("token_in_extract") or result.get("token_in_gst"))
    print(f"\n### OCR [{label}] token_seen={result['token_seen']} ###")
    print("extract preview:", (result.get("extract_text") or "")[:600])
    return result


def press_esc():
    from windows_desktop_tools import windows_press_key

    windows_press_key("Esc", require_change=False)
    time.sleep(0.15)
    windows_press_key("Escape", require_change=False)
    time.sleep(0.2)


def send_wm_char(hwnd: int, text: str) -> Dict[str, Any]:
    from desktop_input import postmessage_type_text_to_hwnd

    return postmessage_type_text_to_hwnd(int(hwnd), text)


def main() -> int:
    from windows_desktop_tools import (
        windows_focus_app,
        windows_press_key,
        get_desktop_target,
        _geometry_wechat_search_target,
    )
    from desktop_input import screen_click

    u = user32()
    u.WindowFromPoint.restype = wintypes.HWND
    u.RealChildWindowFromPoint.restype = wintypes.HWND
    u.ChildWindowFromPointEx.restype = wintypes.HWND
    u.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    u.GetAncestor.restype = wintypes.HWND

    print("=" * 72)
    print("WeChat WM_CHAR HWND probe — token", TOKEN, "— NO messaging")
    print("=" * 72)

    # 1) Focus + Ctrl+F + click search
    focus = windows_focus_app("微信")
    OUT["phases"]["1_focus"] = focus
    print("\n### PHASE 1 focus ###")
    print(jdump(focus))
    top_hwnd = int((focus.get("hwnd") or get_desktop_target().get("hwnd") or 0))
    if not top_hwnd:
        top_hwnd = int(get_desktop_target().get("hwnd") or 0)
    OUT["top_hwnd"] = top_hwnd
    time.sleep(0.35)

    # Ctrl+F to open search
    try:
        cf = windows_press_key("Ctrl+F", require_change=False)
        OUT["phases"]["1b_ctrl_f"] = cf
        print("Ctrl+F:", jdump(cf))
    except Exception as e:
        OUT["phases"]["1b_ctrl_f"] = {"error": str(e)}
    time.sleep(0.35)

    geo = _geometry_wechat_search_target(top_hwnd)
    OUT["phases"]["geometry"] = geo
    if geo and geo.get("x") is not None:
        x, y = int(geo["x"]), int(geo["y"])
        click_src = "geometry"
    else:
        x, y = 566, 248
        click_src = "fallback_566_248"
    OUT["click"] = {"x": x, "y": y, "source": click_src}
    print(f"\n### PHASE 2 click search at ({x},{y}) via {click_src} ###")
    try:
        screen_click(x, y)
        OUT["phases"]["2_click"] = {"ok": True, "x": x, "y": y}
    except Exception as e:
        OUT["phases"]["2_click"] = {"ok": False, "error": str(e)}
    time.sleep(0.35)

    # 2) HWND chain at point
    pt = wintypes.POINT(x, y)
    wfp = int(u.WindowFromPoint(pt) or 0)
    OUT["window_from_point"] = hwnd_info(wfp)

    # RealChildWindowFromPoint needs parent + client coords
    parent_for_child = int(u.GetAncestor(wfp, 2) or top_hwnd or wfp)  # GA_ROOT
    pt_client = wintypes.POINT(x, y)
    u.ScreenToClient(parent_for_child, ctypes.byref(pt_client))
    rcwp = int(u.RealChildWindowFromPoint(parent_for_child, pt_client) or 0)

    # also try from wfp itself
    pt_wfp = wintypes.POINT(x, y)
    u.ScreenToClient(wfp, ctypes.byref(pt_wfp)) if wfp else None
    rcwp_from_wfp = int(u.RealChildWindowFromPoint(wfp, pt_wfp) or 0) if wfp else 0

    flags = CWP_SKIPINVISIBLE | CWP_SKIPDISABLED | CWP_SKIPTRANSPARENT
    cwp_ex = int(u.ChildWindowFromPointEx(parent_for_child, pt_client, flags) or 0)
    cwp_ex_wfp = int(u.ChildWindowFromPointEx(wfp, pt_wfp, flags) or 0) if wfp else 0

    OUT["phases"]["3_hwnd_probe"] = {
        "point": {"x": x, "y": y},
        "WindowFromPoint": hwnd_info(wfp),
        "RealChildWindowFromPoint_root": hwnd_info(rcwp),
        "RealChildWindowFromPoint_wfp": hwnd_info(rcwp_from_wfp),
        "ChildWindowFromPointEx_root": hwnd_info(cwp_ex),
        "ChildWindowFromPointEx_wfp": hwnd_info(cwp_ex_wfp),
        "wfp_parent_chain": chain_from(wfp),
        "top_hwnd_info": hwnd_info(top_hwnd),
        "client_pt_on_root": {"x": pt_client.x, "y": pt_client.y},
    }
    print("\n### PHASE 3 HWND CHAIN ###")
    print(jdump(OUT["phases"]["3_hwnd_probe"]))

    # Ensure search focused again before tests
    press_esc()
    time.sleep(0.2)
    windows_focus_app("微信")
    time.sleep(0.25)
    try:
        windows_press_key("Ctrl+F", require_change=False)
    except Exception:
        pass
    time.sleep(0.3)
    screen_click(x, y)
    time.sleep(0.3)

    # 3a) WM_CHAR to top-level WeChat hwnd
    print("\n### PHASE 4a WM_CHAR -> top-level hwnd", top_hwnd, hex(top_hwnd), "###")
    r_a = send_wm_char(top_hwnd, TOKEN)
    OUT["phases"]["4a_wm_char_top"] = r_a
    print(jdump(r_a))
    time.sleep(0.55)
    ocr_a = ocr_after(top_hwnd, "after_wmchar_toplevel")
    OUT["phases"]["4a_ocr"] = ocr_a

    # Esc cleanup between
    print("\n### Esc between targets ###")
    press_esc()
    time.sleep(0.25)
    windows_focus_app("微信")
    time.sleep(0.25)
    try:
        windows_press_key("Ctrl+F", require_change=False)
    except Exception:
        pass
    time.sleep(0.3)
    screen_click(x, y)
    time.sleep(0.3)

    # 3b) WM_CHAR to WindowFromPoint hwnd
    print("\n### PHASE 4b WM_CHAR -> WindowFromPoint hwnd", wfp, hex(wfp), "###")
    r_b = send_wm_char(wfp, TOKEN)
    OUT["phases"]["4b_wm_char_wfp"] = r_b
    print(jdump(r_b))
    time.sleep(0.55)
    ocr_b = ocr_after(top_hwnd, "after_wmchar_wfp")
    OUT["phases"]["4b_ocr"] = ocr_b

    # Final Esc cleanup
    print("\n### PHASE 5 Esc cleanup ###")
    press_esc()
    press_esc()
    OUT["phases"]["5_cleanup"] = {"esc": True}

    seen_a = bool(ocr_a.get("token_seen"))
    seen_b = bool(ocr_b.get("token_seen"))
    if seen_a and not seen_b:
        winner = "top-level WeChat hwnd"
        winner_hwnd = top_hwnd
    elif seen_b and not seen_a:
        winner = "WindowFromPoint hwnd"
        winner_hwnd = wfp
    elif seen_a and seen_b:
        winner = "BOTH"
        winner_hwnd = {"top": top_hwnd, "wfp": wfp}
    else:
        winner = "NEITHER"
        winner_hwnd = None

    OUT["verdict"] = {
        "token": TOKEN,
        "top_hwnd": top_hwnd,
        "top_hwnd_hex": hex(top_hwnd),
        "wfp_hwnd": wfp,
        "wfp_hwnd_hex": hex(wfp) if wfp else "0x0",
        "wfp_class": (OUT["window_from_point"] or {}).get("class"),
        "wfp_title": (OUT["window_from_point"] or {}).get("title"),
        "ocr_saw_on_toplevel_wmchar": seen_a,
        "ocr_saw_on_wfp_wmchar": seen_b,
        "winner": winner,
        "winner_hwnd": winner_hwnd,
        "click_xy": {"x": x, "y": y, "source": click_src},
    }
    print("\n" + "=" * 72)
    print("### FINAL VERDICT ###")
    print(jdump(OUT["verdict"]))
    print("=" * 72)

    evidence = os.path.join(tempfile.gettempdir(), "wechat_wmchar_hwnd_probe.json")
    with open(evidence, "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)
    print("evidence:", evidence)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback

        traceback.print_exc()
        raise SystemExit(1)
