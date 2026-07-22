# -*- coding: utf-8 -*-
import ctypes, io, os, time, tempfile
from ctypes import wintypes
from PIL import Image, ImageChops, ImageStat
from windows_desktop_tools import windows_focus_app, windows_press_key, get_desktop_target, _geometry_wechat_search_target
from desktop_input import screen_click, paste_text_via_wm_paste, postmessage_type_text_to_hwnd, _set_clipboard_unicode
from desktop_ocr import extract_text_from_bytes
from screen_tools import capture_hwnd_png

TEMP = tempfile.gettempdir()
u = ctypes.windll.user32
TOKEN = "舒琪"


def esc():
    for k in ("Esc", "Escape"):
        try:
            windows_press_key(k, require_change=False)
        except Exception:
            pass
        time.sleep(0.1)


def activate():
    f = windows_focus_app("微信")
    top = int(f.get("hwnd") or get_desktop_target().get("hwnd") or 0)
    time.sleep(0.2)
    windows_press_key("Ctrl+F", require_change=False)
    time.sleep(0.3)
    geo = _geometry_wechat_search_target(top)
    x, y = int(geo["x"]), int(geo["y"])
    screen_click(x, y)
    time.sleep(0.3)
    return top, x, y


def band_ocr(top, x, y, tag):
    png, _ = capture_hwnd_png(top)
    path = os.path.join(TEMP, f"ime_extra_{tag}.png")
    open(path, "wb").write(png)
    im = Image.open(io.BytesIO(png))
    rect = wintypes.RECT()
    u.GetWindowRect(top, ctypes.byref(rect))
    ix, iy = x - rect.left, y - rect.top
    box = (max(0, ix - 200), max(0, iy - 50), min(im.width, ix + 400), min(im.height, iy + 60))
    crop = im.crop(box)
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    t = extract_text_from_bytes(buf.getvalue()) or ""
    has = ("舒" in t) or ("琪" in t)
    print(f"[{tag}] has_chinese={has} ocr={t[:300]!r} png={path}")
    return crop, t


def visible_kids(top):
    kids = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h):
            kids.append(int(h))
        return True

    u.EnumChildWindows(top, cb, 0)
    return kids


esc()
top, x, y = activate()
base, _ = band_ocr(top, x, y, "baseline_empty")

esc()
top, x, y = activate()
kids = visible_kids(top)
print("visible kids", kids)
for h in [top] + kids:
    r = postmessage_type_text_to_hwnd(h, TOKEN)
    print("wm_char", h, r)
time.sleep(0.5)
im1, _ = band_ocr(top, x, y, "wmchar_all_hwnds")
diff = ImageChops.difference(base.convert("RGB"), im1.convert("RGB"))
print("pixel mean diff vs empty", ImageStat.Stat(diff).mean)

esc()
top, x, y = activate()
_set_clipboard_unicode(TOKEN)
for vk in (0x11, 0x56):
    u.keybd_event(vk, 0, 0, 0)
for vk in (0x56, 0x11):
    u.keybd_event(vk, 0, 2, 0)
time.sleep(0.5)
band_ocr(top, x, y, "ctrl_v")

esc()
top, x, y = activate()
kids = visible_kids(top)
for h in [top] + kids:
    print("paste", h, paste_text_via_wm_paste(h, TOKEN))
time.sleep(0.5)
band_ocr(top, x, y, "wm_paste_all")
esc()
print("DONE")
