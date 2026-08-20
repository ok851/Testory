import sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import _check_device_screen_on

serial = "3B163L00CF800000"
ok, msg = _check_device_screen_on(serial)
print(f"Screen: ok={ok}, msg={msg}")
