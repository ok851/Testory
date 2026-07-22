# -*- coding: utf-8 -*-
"""WeChat search Chinese input method bake-off. Token: 舒琪. No messaging."""
from __future__ import annotations

import ctypes
import io
import os
import sys
import tempfile
import time
from ctypes import wintypes
from typing import Any, Dict, Tuple

TOKEN = "舒琪"
TEMP = tempfile.gettempdir()

WM_CHAR = 0x0102
WM_UNICHAR = 0x0109
WM_IME_CHAR = 0x0286
UNICODE_NOCHAR = 0xFFFF
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002


def user32():
    return ctypes.windll.user32


def press_esc():
    from windows_desktop_tools import windows_press_key
    for k in ("Esc", "Escape"):
        try:
            windows_press_key(k, require_change=False)
        except Exception:
            pass
        time.sleep(0.12)


def activate_search() -> Tuple[int, int, int]:
    from windows_desktop_tools import (
        windows_focus_app,
        windows_press_key,
        get_desktop_target,
        windows_click_element,
        _geometry_wechat_search_target,
    )
    from desktop_input import screen_click

    focus = windows_focus_app("微信")
    print("focus:", {k: focus.get(k) for k in ("ok", "hwnd", "title", "error") if isinstance(focus, dict)})
    top = int((focus.get("hwnd") if isinstance(focus, dict) else 0) or get_desktop_target().get("hwnd") or 0)
    time.sleep(0.3)

    try:
        r = windows_click_element("搜索", require_change=False)
        print("windows_click_element(搜索):", {k: r.get(k) for k in ("ok", "via", "error", "x", "y") if isinstance(r, dict)})
        if isinstance(r, dict) and r.get("ok"):
            x = int(r.get("x") or 0)
            y = int(r.get("y") or 0)
            if x and y:
                return top, x, y
    except Exception as e:
        print("click_element error:", e)

    try:
        windows_press_key("Ctrl+F", require_change=False)
    except Exception as e:
        print("Ctrl+F error:", e)
    time.sleep(0.35)

    geo = _geometry_wechat_search_target(top) if top else None
    if geo and geo.get("x") is not None:
        x, y = int(geo["x"]), int(geo["y"])
        src = "geometry"
    else:
        x, y = 566, 248
        src = "fallback"
    print(f"physical click search ({x},{y}) via {src}")
    screen_click(x, y)
    time.sleep(0.35)
    return top, x, y


def resolve_edit_hwnd(top: int, x: int, y: int) -> int:
    u = user32()
    u.WindowFromPoint.restype = wintypes.HWND
    pt = wintypes.POINT(x, y)
    wfp = int(u.WindowFromPoint(pt) or 0)
    hwnd = wfp or top
    print(f"edit hwnd candidate: {hwnd} ({hex(hwnd)}) wfp={wfp} top={top}")
    return hwnd


def utf16_units(text: str):
    units = []
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:
            c = code - 0x10000
            units.append(0xD800 + (c >> 10))
            units.append(0xDC00 + (c & 0x3FF))
        else:
            units.append(code)
    return units


def send_wm_per_codepoint(hwnd: int, msg: int, text: str, *, unichar_probe: bool = False) -> Dict[str, Any]:
    u = user32()
    u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SendMessageW.restype = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    sent = 0
    results = []
    if unichar_probe:
        try:
            r = int(u.SendMessageW(hwnd, msg, UNICODE_NOCHAR, 0) or 0)
            results.append({"probe_FFFF": r, "accepts": bool(r)})
            print(f"  WM_UNICHAR probe 0xFFFF -> {r} (accepts={bool(r)})")
        except Exception as e:
            results.append({"probe_error": str(e)})
    for ch in text:
        code = ord(ch)
        try:
            rr = int(u.SendMessageW(hwnd, msg, int(code), 1) or 0)
            sent += 1
            results.append({"ch": ch, "code": code, "ret": rr})
        except Exception as e:
            return {"ok": False, "sent": sent, "error": str(e), "details": results}
        time.sleep(0.02)
    time.sleep(0.08)
    return {"ok": True, "sent": sent, "hwnd": hwnd, "msg": hex(msg), "details": results}


def sendinput_unicode_only(text: str) -> Dict[str, Any]:
    u = user32()

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]

    units = utf16_units(text)
    sent = 0
    fg = int(u.GetForegroundWindow() or 0)
    print(f"  FG before SendInput: {fg} ({hex(fg)})")
    for unit in units:
        for flag in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT()
            inp.type = 1
            inp.ki = KEYBDINPUT(0, int(unit) & 0xFFFF, flag, 0, None)
            n = int(u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) or 0)
            if n != 1:
                return {"ok": False, "sent": sent, "error": f"SendInput returned {n}", "units": units, "fg": fg}
        sent += 1
        time.sleep(0.015)
    time.sleep(0.08)
    return {"ok": True, "sent": sent, "units": units, "fg": fg, "via": "KEYEVENTF_UNICODE"}


def method_clipboard_wm_paste(hwnd: int, text: str) -> Dict[str, Any]:
    from desktop_input import paste_text_via_wm_paste
    return paste_text_via_wm_paste(hwnd, text)


def capture_search_roi_ocr(top: int, x: int, y: int, label: str, n: int) -> Dict[str, Any]:
    from desktop_ocr import extract_text_from_bytes
    from screen_tools import capture_hwnd_png

    out: Dict[str, Any] = {"label": label, "n": n}
    png = b""
    try:
        png, meta = capture_hwnd_png(top)
        out["capture_meta"] = meta
    except Exception as e:
        out["hwnd_capture_err"] = str(e)

    path = os.path.join(TEMP, f"ime_test_{n}.png")
    roi_png = b""
    try:
        from PIL import Image
        if png:
            im = Image.open(io.BytesIO(png))
            u = user32()
            rect = wintypes.RECT()
            u.GetWindowRect(top, ctypes.byref(rect))
            wl, wt = rect.left, rect.top
            ix, iy = x - wl, y - wt
            left = max(0, ix - 180)
            top_i = max(0, iy - 40)
            right = min(im.width, ix + 320)
            bot = min(im.height, iy + 50)
            roi = im.crop((left, top_i, right, bot))
            buf = io.BytesIO()
            roi.save(buf, format="PNG")
            roi_png = buf.getvalue()
            out["roi"] = {"left": left, "top": top_i, "right": right, "bot": bot}
    except Exception as e:
        out["roi_err"] = str(e)
        roi_png = png

    blob = roi_png or png
    if blob:
        with open(path, "wb") as f:
            f.write(blob)
        out["png"] = path
        out["png_bytes"] = len(blob)

    texts = []
    for blob2, tag in ((roi_png, "roi"), (png, "full")):
        if not blob2:
            continue
        try:
            t = extract_text_from_bytes(blob2) or ""
            texts.append(t)
            out[f"ocr_{tag}"] = t[:1500]
        except Exception as e:
            out[f"ocr_{tag}_err"] = str(e)

    joined = "\n".join(texts)
    has_shu = "舒" in joined
    has_qi = "琪" in joined
    has_either = has_shu or has_qi
    out["has_shu"] = has_shu
    out["has_qi"] = has_qi
    out["found_either"] = has_either
    out["joined_preview"] = joined[:800]
    print(f"\n=== OCR method {n} [{label}] ===")
    print(f"  PNG: {path}")
    print(f"  has_舒={has_shu} has_琪={has_qi} found_either={has_either}")
    print(f"  OCR preview: {joined[:500]!r}")
    return out


def main() -> int:
    print("=" * 72)
    print("WeChat Chinese IME bake-off — token", TOKEN, "— NO messaging contacts")
    print("=" * 72)

    top, x, y = activate_search()
    hwnd = resolve_edit_hwnd(top, x, y)

    methods = [
        (1, "WM_CHAR SendMessageW per codepoint",
         lambda h: send_wm_per_codepoint(h, WM_CHAR, TOKEN)),
        (2, "WM_UNICHAR 0x0109 per codepoint (probe 0xFFFF first)",
         lambda h: send_wm_per_codepoint(h, WM_UNICHAR, TOKEN, unichar_probe=True)),
        (3, "WM_IME_CHAR 0x0286 per codepoint",
         lambda h: send_wm_per_codepoint(h, WM_IME_CHAR, TOKEN)),
        (4, "KEYEVENTF_UNICODE SendInput UTF-16 units (FG WeChat)",
         lambda h: sendinput_unicode_only(TOKEN)),
        (5, "clipboard + WM_PASTE",
         lambda h: method_clipboard_wm_paste(h, TOKEN)),
    ]

    results = []
    for n, label, fn in methods:
        print("\n" + "#" * 72)
        print(f"METHOD {n}: {label}")
        print("#" * 72)
        if n > 1:
            press_esc()
            time.sleep(0.25)
            top, x, y = activate_search()
            hwnd = resolve_edit_hwnd(top, x, y)

        if n == 4:
            from windows_desktop_tools import windows_focus_app
            from desktop_input import screen_click
            windows_focus_app("微信")
            time.sleep(0.2)
            screen_click(x, y)
            time.sleep(0.25)
            fg = int(user32().GetForegroundWindow() or 0)
            print(f"  ensured FG={fg} hex={hex(fg)} top={top}")

        use_hwnd = hwnd or top
        print(f"  sending to hwnd={use_hwnd} ({hex(use_hwnd or 0)})")
        try:
            send_result = fn(use_hwnd)
        except Exception as e:
            send_result = {"ok": False, "error": str(e)}
        print("  send_result:", send_result)

        if n in (1, 2, 3, 5) and top and top != use_hwnd:
            try:
                alt = fn(top)
                print(f"  also sent to top={top}:", alt)
                send_result = {"wfp": send_result, "top": alt}
            except Exception as e:
                print("  top send error:", e)

        time.sleep(0.55)
        ocr = capture_search_roi_ocr(top, x, y, label, n)
        results.append({
            "n": n,
            "label": label,
            "send": send_result,
            "ocr": {
                "found_either": ocr.get("found_either"),
                "has_shu": ocr.get("has_shu"),
                "has_qi": ocr.get("has_qi"),
                "png": ocr.get("png"),
                "preview": ocr.get("joined_preview"),
            },
        })

    press_esc()

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    winners = []
    for r in results:
        fe = r["ocr"].get("found_either")
        print(f"  [{r['n']}] {r['label'][:50]:50s} found_舒/琪={fe}  png={r['ocr'].get('png')}")
        if fe:
            winners.append(r["n"])
    if winners:
        print("WINNERS (OCR found 舒 or 琪):", winners)
    else:
        print("WINNERS: NONE — no method produced human-readable 舒/琪 via OCR")
        print("(PNGs saved under %TEMP% for manual inspection)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
