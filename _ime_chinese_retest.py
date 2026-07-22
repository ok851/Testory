# -*- coding: utf-8 -*-
"""Retest WeChat Chinese input with fixed SendInput + full captures + ASCII control."""
from __future__ import annotations

import ctypes
import io
import os
import sys
import tempfile
time = __import__("time")
from ctypes import wintypes
from typing import Any, Dict, List, Tuple

TOKEN = "舒琪"
ASCII = "QWER99"
TEMP = tempfile.gettempdir()
WM_CHAR, WM_UNICHAR, WM_IME_CHAR, WM_PASTE = 0x0102, 0x0109, 0x0286, 0x0302
UNICODE_NOCHAR = 0xFFFF
KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]


def user32():
    return ctypes.windll.user32


def press_esc():
    from windows_desktop_tools import windows_press_key
    for k in ("Esc", "Escape"):
        try:
            windows_press_key(k, require_change=False)
        except Exception:
            pass
        time.sleep(0.1)


def enum_children(hwnd: int) -> List[Dict[str, Any]]:
    u = user32()
    out: List[Dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _lp):
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, buf, 256)
        tbuf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, tbuf, 256)
        rect = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(rect))
        out.append({
            "hwnd": int(h),
            "class": buf.value,
            "title": tbuf.value,
            "rect": (rect.left, rect.top, rect.right, rect.bottom),
            "visible": bool(u.IsWindowVisible(h)),
        })
        return True

    u.EnumChildWindows(hwnd, cb, 0)
    return out


def activate_search() -> Tuple[int, int, int]:
    from windows_desktop_tools import (
        windows_focus_app,
        windows_press_key,
        get_desktop_target,
        _geometry_wechat_search_target,
    )
    from desktop_input import screen_click

    focus = windows_focus_app("微信")
    top = int((focus.get("hwnd") if isinstance(focus, dict) else 0) or get_desktop_target().get("hwnd") or 0)
    time.sleep(0.25)
    try:
        windows_press_key("Ctrl+F", require_change=False)
    except Exception as e:
        print("Ctrl+F:", e)
    time.sleep(0.35)
    geo = _geometry_wechat_search_target(top) if top else None
    if geo and geo.get("x") is not None:
        x, y = int(geo["x"]), int(geo["y"])
        src = "geometry"
    else:
        x, y = 566, 248
        src = "fallback"
    print(f"activate_search top={top} click=({x},{y}) via {src}")
    screen_click(x, y)
    time.sleep(0.35)
    return top, x, y


def send_wm(hwnd: int, msg: int, text: str, *, unichar_probe=False, use_post=False) -> Dict[str, Any]:
    u = user32()
    fn = u.PostMessageW if use_post else u.SendMessageW
    fn.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    if not use_post:
        fn.restype = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    details = []
    if unichar_probe:
        r = int(u.SendMessageW(hwnd, msg, UNICODE_NOCHAR, 0) or 0)
        details.append({"probe_FFFF": r, "accepts": bool(r)})
        print(f"  WM_UNICHAR probe -> {r}")
    sent = 0
    for ch in text:
        code = ord(ch)
        rr = fn(hwnd, msg, int(code), 1)
        details.append({"ch": ch, "code": code, "ret": int(rr or 0) if not use_post else bool(rr)})
        sent += 1
        time.sleep(0.02)
    return {"ok": True, "sent": sent, "hwnd": hwnd, "msg": hex(msg), "post": use_post, "details": details}


def sendinput_unicode(text: str) -> Dict[str, Any]:
    u = user32()
    k = ctypes.windll.kernel32
    units = []
    for ch in text:
        c = ord(ch)
        if c > 0xFFFF:
            cc = c - 0x10000
            units += [0xD800 + (cc >> 10), 0xDC00 + (cc & 0x3FF)]
        else:
            units.append(c)
    fg = int(u.GetForegroundWindow() or 0)
    print(f"  FG={fg} sizeof(INPUT)={ctypes.sizeof(INPUT)}")
    sent = 0
    for unit in units:
        for flag in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT()
            inp.type = 1
            inp.ki = KEYBDINPUT(0, int(unit) & 0xFFFF, flag, 0, 0)
            k.SetLastError(0)
            n = int(u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) or 0)
            if n != 1:
                return {"ok": False, "sent": sent, "error": f"SendInput={n} err={k.GetLastError()}", "units": units, "fg": fg}
        sent += 1
        time.sleep(0.015)
    return {"ok": True, "sent": sent, "units": units, "fg": fg}


def wm_paste(hwnd: int, text: str) -> Dict[str, Any]:
    from desktop_input import paste_text_via_wm_paste
    return paste_text_via_wm_paste(hwnd, text)


def ocr_check(joined: str) -> Dict[str, bool]:
    return {
        "has_shu": "舒" in joined,
        "has_qi": "琪" in joined,
        "has_either": ("舒" in joined) or ("琪" in joined),
        "has_ascii": ASCII in joined.replace(" ", "").replace("\n", "").upper() or all(c in joined.upper() for c in ASCII[:4]),
    }


def capture_and_ocr(top: int, x: int, y: int, n: int, label: str) -> Dict[str, Any]:
    from desktop_ocr import extract_text_from_bytes
    from screen_tools import capture_hwnd_png
    from PIL import Image

    png, meta = capture_hwnd_png(top)
    full_path = os.path.join(TEMP, f"ime_test_{n}_full.png")
    roi_path = os.path.join(TEMP, f"ime_test_{n}.png")
    if png:
        with open(full_path, "wb") as f:
            f.write(png)

    u = user32()
    rect = wintypes.RECT()
    u.GetWindowRect(top, ctypes.byref(rect))
    im = Image.open(io.BytesIO(png)) if png else None
    roi_png = b""
    if im is not None:
        ix, iy = x - rect.left, y - rect.top
        # Wider search-bar band near top of window (WeChat search is near top)
        # Also try a top-band crop that usually contains the search edit text
        bands = []
        # click-centered
        bands.append(("click", (
            max(0, ix - 200), max(0, iy - 50),
            min(im.width, ix + 400), min(im.height, iy + 60),
        )))
        # top search strip (common WeChat layout)
        bands.append(("topstrip", (80, 40, min(im.width, 700), 140)))
        best_text = ""
        best_blob = b""
        best_name = ""
        for name, box in bands:
            crop = im.crop(box)
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            blob = buf.getvalue()
            try:
                t = extract_text_from_bytes(blob) or ""
            except Exception as e:
                t = f"<ocr_err {e}>"
            print(f"  band[{name}] box={box} ocr={t[:200]!r}")
            if len(t) > len(best_text):
                best_text, best_blob, best_name = t, blob, name
            # prefer if Chinese token chars present
            if ("舒" in t) or ("琪" in t) or (ASCII[:4] in t.upper()):
                best_text, best_blob, best_name = t, blob, name
                break
        roi_png = best_blob
        with open(roi_path, "wb") as f:
            f.write(roi_png)
        # also full OCR briefly
        try:
            full_t = extract_text_from_bytes(png) or ""
        except Exception as e:
            full_t = f"<err {e}>"
        joined = best_text + "\n" + full_t
        flags = ocr_check(joined)
        print(f"=== OCR {n} [{label}] band={best_name} flags={flags} ===")
        print(f"  full_png={full_path} roi={roi_path}")
        print(f"  preview={joined[:400]!r}")
        return {"png": roi_path, "full": full_path, "preview": joined[:800], **flags, "band": best_name}
    return {"error": "no png"}


def main() -> int:
    print("=" * 72)
    print("RETEST Chinese bake-off token=", TOKEN)
    print("=" * 72)

    top, x, y = activate_search()
    kids = enum_children(top)
    print(f"child count={len(kids)}")
    for c in kids[:30]:
        if c["visible"] or "Edit" in c["class"] or "Qt" in c["class"]:
            print(" ", c)

    # ASCII control to prove focus
    print("\n### ASCII CONTROL WM_CHAR ###")
    r = send_wm(top, WM_CHAR, ASCII)
    print(r)
    time.sleep(0.5)
    ctrl = capture_and_ocr(top, x, y, 0, "ascii_control")
    print("ASCII control found?", ctrl.get("has_ascii"), ctrl.get("preview", "")[:200])

    methods = [
        (1, "WM_CHAR", lambda h: send_wm(h, WM_CHAR, TOKEN)),
        (2, "WM_UNICHAR", lambda h: send_wm(h, WM_UNICHAR, TOKEN, unichar_probe=True)),
        (3, "WM_IME_CHAR", lambda h: send_wm(h, WM_IME_CHAR, TOKEN)),
        (4, "SendInput UNICODE", lambda h: sendinput_unicode(TOKEN)),
        (5, "clipboard WM_PASTE", lambda h: wm_paste(h, TOKEN)),
    ]
    # Also method 1b PostMessage WM_CHAR
    results = [{"n": 0, "label": "ASCII control", "ocr": ctrl}]

    for n, label, fn in methods:
        print("\n" + "#" * 72)
        print(f"METHOD {n}: {label}")
        press_esc()
        time.sleep(0.2)
        top, x, y = activate_search()
        if n == 4:
            from windows_desktop_tools import windows_focus_app
            from desktop_input import screen_click
            windows_focus_app("微信")
            time.sleep(0.15)
            screen_click(x, y)
            time.sleep(0.2)
        print("  send:", fn(top))
        # also PostMessage variant for WM_CHAR chinese on method 1
        if n == 1:
            print("  also PostMessageW WM_CHAR:", send_wm(top, WM_CHAR, TOKEN, use_post=True))
        time.sleep(0.6)
        ocr = capture_and_ocr(top, x, y, n, label)
        results.append({"n": n, "label": label, "ocr": ocr})

    press_esc()
    print("\n" + "=" * 72)
    print("SUMMARY")
    winners = []
    for r in results:
        o = r["ocr"]
        print(f"  [{r['n']}] {r['label']:22s} either={o.get('has_either')} shu={o.get('has_shu')} qi={o.get('has_qi')} ascii={o.get('has_ascii')} png={o.get('png')}")
        if o.get("has_either"):
            winners.append(r["n"])
    print("WINNERS:", winners if winners else "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
