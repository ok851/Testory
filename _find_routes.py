import re
text = open(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_routes.py", encoding="utf-8").read()
for m in re.finditer(r'@app\.route\("/api/mobile/mirror/[^"]+"', text):
    line = text[:m.start()].count("\n") + 1
    print(f"Line {line}: {m.group()}")
