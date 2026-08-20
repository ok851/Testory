import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Fix: all search strings must be lowercase to match .lower() text
old = '''    screen_on = ("mWakefulness=Awake" in text or
                 "mWakefulness=1" in text or
                 "displaypowerstate=on" in text or
                 "mscreenon=true" in text or
                 "mscreenonearly=true" in text or
                 "mstate=on" in text)'''

new = '''    screen_on = ("mwakefulness=awake" in text or
                 "mwakefulness=1" in text or
                 "displaypowerstate=on" in text or
                 "mscreenon=true" in text or
                 "mscreenonearly=true" in text or
                 "mstate=on" in text)'''

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: fixed case sensitivity in _check_device_screen_on")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): fixed case sensitivity")
    else:
        print("NOT FOUND")
