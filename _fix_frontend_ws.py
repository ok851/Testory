import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\templates\mobile_testing.html")
text = p.read_text(encoding="utf-8")

# Use mirror_ws_url (includes serial) instead of bridge.ws_url
old = "var wsUrl=data.bridge && data.bridge.ws_url ? data.bridge.ws_url : null;"
new = "var wsUrl=data.mirror_ws_url || (data.bridge && data.bridge.ws_url ? data.bridge.ws_url : null);"

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: use mirror_ws_url with serial")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): use mirror_ws_url")
    else:
        print("NOT FOUND")
