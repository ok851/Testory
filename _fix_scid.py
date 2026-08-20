import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Fix scid to be positive (fit in Java int)
old = '    bucket = zlib.crc32(key.encode("utf-8")) & 0xFFFFFFFF'
new = '    bucket = zlib.crc32(key.encode("utf-8")) & 0x7FFFFFFF  # must fit in Java int'

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: scid masked to 0x7FFFFFFF")
else:
    print("NOT FOUND")
