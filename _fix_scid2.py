import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
lines = p.read_text(encoding="utf-8").split("\n")

for i, line in enumerate(lines):
    if "zlib.crc32" in line and "0xFFFFFFFF" in line:
        lines[i] = line.replace("0xFFFFFFFF", "0x7FFFFFFF")
        print(f"Fixed line {i+1}: {lines[i].strip()}")
        break

p.write_text("\n".join(lines), encoding="utf-8")
print("OK")
