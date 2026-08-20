import subprocess, time, sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import _check_device_screen_on, _try_wake_screen, _diagnose_device_for_scrcpy

serial = "3B163L00CF800000"

# First, make sure screen is on
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
subprocess.run([adb, "-s", serial, "shell", "input keyevent 224"], capture_output=True, timeout=5)
time.sleep(1)
subprocess.run([adb, "-s", serial, "shell", "input swipe 540 1800 540 600 300"], capture_output=True, timeout=5)
time.sleep(0.5)
subprocess.run([adb, "-s", serial, "shell", "wm dismiss-keyguard"], capture_output=True, timeout=5)
time.sleep(0.5)
subprocess.run([adb, "-s", serial, "shell", "svc power stayon true"], capture_output=True, timeout=5)
time.sleep(0.5)

print("=== Screen check ===")
ok, msg = _check_device_screen_on(serial)
print(f"Screen on: {ok}, msg: {msg}")

print("\n=== Full diagnosis ===")
diag = _diagnose_device_for_scrcpy(serial)
print(f"Screen OK: {diag['screen_ok']}")
print(f"Screen msg: {diag['screen_msg']}")
print(f"Warnings: {diag['warnings']}")
print(f"SDK: {diag['sdk_level']}")
print(f"Memory: {diag['total_memory_mb']}MB")
