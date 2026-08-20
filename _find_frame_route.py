import re
text = open(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_routes.py", encoding="utf-8").read()
# Find mirror/frame route
idx = text.find("/api/mobile/mirror/frame")
if idx >= 0:
    start = max(0, idx - 200)
    print(text[start:idx+300])
else:
    print("NOT FOUND")
