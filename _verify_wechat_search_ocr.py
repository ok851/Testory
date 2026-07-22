# -*- coding: utf-8 -*-
"""Honest WeChat search-box E2E verification. Does NOT send messages."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from typing import Any, Dict, Optional

TOKEN = "ZZTESTORY8821"
OUT: Dict[str, Any] = {"token": TOKEN, "phases": {}, "verdict": {}}


def jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def token_in_text(text: str) -> bool:
    t = (text or "").upper().replace(" ", "").replace("\n", "")
    needle = TOKEN.upper()
    if needle in t:
        return True
    # OCR sometimes splits chars
    compact = "".join(ch for ch in t if ch.isalnum())
    return needle in compact


def ocr_probe(png: Optional[bytes], label: str) -> Dict[str, Any]:
    from desktop_ocr import extract_text_from_bytes
    from screen_tools import get_screen_text

    result: Dict[str, Any] = {"label": label, "png_bytes": len(png or b"")}
    text_bytes = ""
    try:
        if png:
            text_bytes = extract_text_from_bytes(png) or ""
        result["extract_text_from_bytes"] = text_bytes
        result["token_in_extract"] = token_in_text(text_bytes)
    except Exception as e:
        result["extract_error"] = f"{type(e).__name__}: {e}"
        result["token_in_extract"] = False

    try:
        gst = get_screen_text()
        result["get_screen_text"] = gst
        texts = []
        if isinstance(gst, dict):
            for k in ("text", "full_text", "ocr_text"):
                if gst.get(k):
                    texts.append(str(gst.get(k)))
            blocks = gst.get("blocks") or gst.get("texts") or gst.get("items") or []
            if isinstance(blocks, list):
                for b in blocks:
                    if isinstance(b, dict):
                        texts.append(str(b.get("text") or b.get("content") or ""))
                    else:
                        texts.append(str(b))
            # nested data
            data = gst.get("data")
            if isinstance(data, dict) and data.get("text"):
                texts.append(str(data.get("text")))
            if isinstance(data, list):
                for b in data:
                    if isinstance(b, dict):
                        texts.append(str(b.get("text") or ""))
        joined = "\n".join(texts)
        result["get_screen_text_joined"] = joined[:4000]
        result["token_in_get_screen_text"] = token_in_text(joined) or token_in_text(jdump(gst))
    except Exception as e:
        result["get_screen_text_error"] = f"{type(e).__name__}: {e}"
        result["token_in_get_screen_text"] = False

    result["token_seen"] = bool(result.get("token_in_extract") or result.get("token_in_get_screen_text"))
    return result


def save_png(png: Optional[bytes], name: str) -> Optional[str]:
    if not png:
        return None
    path = os.path.join(tempfile.gettempdir(), name)
    with open(path, "wb") as f:
        f.write(png)
    return path


def clear_search() -> Dict[str, Any]:
    from windows_desktop_tools import windows_press_key, windows_type_text, clear_search_input_focus

    steps = []
    # Esc twice to leave search without sending
    r1 = windows_press_key("Esc", require_change=False)
    steps.append({"esc1": r1})
    time.sleep(0.2)
    r2 = windows_press_key("Escape", require_change=False)
    steps.append({"esc2": r2})
    try:
        clear_search_input_focus()
        steps.append({"clear_search_input_focus": True})
    except Exception as e:
        steps.append({"clear_search_input_focus": str(e)})
    return {"steps": steps}


def main() -> int:
    from windows_desktop_tools import (
        windows_focus_app,
        windows_click_element,
        windows_type_text,
        windows_press_key,
        get_desktop_target,
        _geometry_wechat_search_target,
    )
    from screen_tools import capture_hwnd_png
    from desktop_input import screen_click, sendinput_type_text, _paste_unicode_via_clipboard

    print("=" * 72)
    print("WeChat E2E honest verify — NO messaging contacts")
    print("token:", TOKEN)
    print("=" * 72)

    # --- Phase 1: focus ---
    focus = windows_focus_app("微信")
    OUT["phases"]["1_focus"] = focus
    print("\n### PHASE 1 focus FULL JSON ###")
    print(jdump(focus))
    hwnd = int((focus.get("hwnd") or get_desktop_target().get("hwnd") or 0))
    if not hwnd:
        hwnd = int(get_desktop_target().get("hwnd") or 0)
    OUT["hwnd"] = hwnd
    time.sleep(0.4)

    # --- Phase 2: click search ---
    click = windows_click_element("搜索")
    OUT["phases"]["2_click_search"] = click
    print("\n### PHASE 2 click FULL JSON ###")
    print(jdump(click))
    time.sleep(0.35)
    hwnd = int(get_desktop_target().get("hwnd") or hwnd or 0)
    OUT["hwnd"] = hwnd
    OUT["search_xy"] = get_desktop_target().get("search_xy")

    # --- Phase 3: type token ---
    typed = windows_type_text(TOKEN, clear=True, require_change=False)
    OUT["phases"]["3_type"] = typed
    print("\n### PHASE 3 type FULL JSON ###")
    print(jdump(typed))
    time.sleep(0.45)

    # --- Phase 4: capture + OCR ---
    png, meta = capture_hwnd_png(hwnd)
    path = save_png(png, f"wechat_e2e_{TOKEN}_phase4.png")
    OUT["phases"]["4_capture"] = {"meta": meta, "png_path": path, "png_bytes": len(png or b"")}
    print("\n### PHASE 4 capture ###")
    print(jdump(OUT["phases"]["4_capture"]))

    ocr4 = ocr_probe(png, "after_windows_type_text")
    OUT["phases"]["4_ocr"] = ocr4
    print("\n### PHASE 4 OCR ###")
    print(jdump({k: v for k, v in ocr4.items() if k != "get_screen_text"}))
    print("token_seen_phase4:", ocr4.get("token_seen"))
    print("extract preview:", (ocr4.get("extract_text_from_bytes") or "")[:800])

    # --- Phase 5: alternative if OCR missed ---
    alt: Dict[str, Any] = {"needed": not bool(ocr4.get("token_seen"))}
    if alt["needed"]:
        print("\n### PHASE 5 ALTERNATIVE: physical click + paste ###")
        geo = _geometry_wechat_search_target(hwnd)
        alt["geometry"] = geo
        if geo and geo.get("x") is not None:
            x, y = int(geo["x"]), int(geo["y"])
            try:
                screen_click(x, y)
                alt["screen_click"] = {"ok": True, "x": x, "y": y}
            except Exception as e:
                alt["screen_click"] = {"ok": False, "error": str(e), "x": x, "y": y}
            time.sleep(0.25)
            # clear then paste
            try:
                windows_press_key("Ctrl+A", require_change=False)
                time.sleep(0.05)
            except Exception:
                pass
            try:
                _paste_unicode_via_clipboard(TOKEN)
                alt["paste"] = {"ok": True, "via": "clipboard"}
            except Exception as e:
                alt["paste"] = {"ok": False, "error": str(e)}
            time.sleep(0.45)
            png5, meta5 = capture_hwnd_png(hwnd)
            path5 = save_png(png5, f"wechat_e2e_{TOKEN}_phase5_paste.png")
            alt["capture_after_paste"] = {"meta": meta5, "png_path": path5, "png_bytes": len(png5 or b"")}
            ocr5 = ocr_probe(png5, "after_physical_click_paste")
            alt["ocr_after_paste"] = ocr5
            print(jdump({k: v for k, v in ocr5.items() if k != "get_screen_text"}))
            print("token_seen_after_paste:", ocr5.get("token_seen"))

            # If paste "ok" but OCR empty -> sendinput ASCII
            if not ocr5.get("token_seen"):
                print("\n### PHASE 5b: sendinput_type_text after physical click ###")
                try:
                    screen_click(x, y)
                    time.sleep(0.2)
                    windows_press_key("Ctrl+A", require_change=False)
                    time.sleep(0.05)
                    windows_press_key("Delete", require_change=False)
                    time.sleep(0.05)
                except Exception as e:
                    alt["pre_sendinput"] = str(e)
                try:
                    sendinput_type_text(TOKEN)
                    alt["sendinput"] = {"ok": True, "text": TOKEN}
                except Exception as e:
                    alt["sendinput"] = {"ok": False, "error": str(e)}
                time.sleep(0.5)
                png5b, meta5b = capture_hwnd_png(hwnd)
                path5b = save_png(png5b, f"wechat_e2e_{TOKEN}_phase5b_sendinput.png")
                alt["capture_after_sendinput"] = {
                    "meta": meta5b,
                    "png_path": path5b,
                    "png_bytes": len(png5b or b""),
                }
                ocr5b = ocr_probe(png5b, "after_sendinput")
                alt["ocr_after_sendinput"] = ocr5b
                print(jdump({k: v for k, v in ocr5b.items() if k != "get_screen_text"}))
                print("token_seen_after_sendinput:", ocr5b.get("token_seen"))
                print("extract preview:", (ocr5b.get("extract_text_from_bytes") or "")[:800])
        else:
            alt["error"] = "no geometry point for WeChat search"
    else:
        print("\n### PHASE 5 skipped (OCR already saw token) ###")

    OUT["phases"]["5_alternative"] = alt

    # --- Phase 6: clear ---
    cleared = clear_search()
    OUT["phases"]["6_clear"] = cleared
    print("\n### PHASE 6 clear ###")
    print(jdump(cleared))
    time.sleep(0.3)
    png_end, meta_end = capture_hwnd_png(hwnd)
    path_end = save_png(png_end, f"wechat_e2e_{TOKEN}_cleared.png")
    OUT["phases"]["6_after_clear_capture"] = {
        "meta": meta_end,
        "png_path": path_end,
        "png_bytes": len(png_end or b""),
    }

    # Verdict
    seen = bool(ocr4.get("token_seen"))
    seen_alt = False
    if isinstance(alt.get("ocr_after_paste"), dict):
        seen_alt = seen_alt or bool(alt["ocr_after_paste"].get("token_seen"))
    if isinstance(alt.get("ocr_after_sendinput"), dict):
        seen_alt = seen_alt or bool(alt["ocr_after_sendinput"].get("token_seen"))

    OUT["verdict"] = {
        "focus_success_flag": bool(focus.get("success")),
        "click_success_flag": bool(click.get("success")),
        "type_success_flag": bool(typed.get("success")),
        "type_verified_flag": typed.get("verified"),
        "ocr_saw_token_after_windows_type": seen,
        "ocr_saw_token_after_alternative": seen_alt,
        "ocr_saw_token_any_path": bool(seen or seen_alt),
        "skeptical_note": (
            "Do NOT trust success=true alone. "
            "OCR presence of the unique token is the ground truth for visible search text."
        ),
        "honest_answer": (
            "YES — OCR saw ZZTESTORY8821"
            if (seen or seen_alt)
            else "NO — OCR never saw ZZTESTORY8821 despite tool success flags"
        ),
    }

    print("\n" + "=" * 72)
    print("### FINAL VERDICT ###")
    print(jdump(OUT["verdict"]))
    print("=" * 72)

    # Write full evidence dump
    evidence_path = os.path.join(tempfile.gettempdir(), f"wechat_e2e_{TOKEN}_evidence.json")
    with open(evidence_path, "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)
    print("evidence_json:", evidence_path)
    return 0 if OUT["verdict"]["ocr_saw_token_any_path"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
