import sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import _adb_exec

serial = "3B163L00CF800000"
code, out, err = _adb_exec(serial, "dumpsys power", timeout=10)
text = (out + err).lower()

print(f"out length: {len(out)}")
print(f"err length: {len(err)}")
print(f"text length: {len(text)}")
print(f"mwakefulness=awake in text: {'mwakefulness=awake' in text}")

# Find the exact line
for i, line in enumerate(text.split("\n")):
    if "wakefulness" in line:
        print(f"Line {i}: {repr(line)}")

# Now test the function
from mobile_scrcpy_bridge import _check_device_screen_on
ok, msg = _check_device_screen_on(serial)
print(f"\nFunction result: ok={ok}, msg={msg}")
