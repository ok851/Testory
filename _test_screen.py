import subprocess, time
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

# Check screen state in detail
r = subprocess.run([adb, "-s", serial, "shell", "dumpsys power"], capture_output=True, text=True, timeout=10)
for line in r.stdout.split("\n"):
    low = line.strip().lower()
    if any(k in low for k in ["mwakefulness", "mscreenon", "displaypowerstate", "mstayon", "mdreaming"]):
        print("POWER:", line.strip())

r2 = subprocess.run([adb, "-s", serial, "shell", "dumpsys window policy"], capture_output=True, text=True, timeout=10)
for line in r2.stdout.split("\n"):
    low = line.strip().lower()
    if any(k in low for k in ["showing", "keyguard", "secure", "lock", "screen"]):
        print("WINDOW:", line.strip())

# Try screencap to see if we can capture
r3 = subprocess.run([adb, "-s", serial, "shell", "screencap -p /data/local/tmp/test_screen.png"], capture_output=True, text=True, timeout=10)
print("Screencap:", r3.returncode, r3.stderr.strip()[:200])
r4 = subprocess.run([adb, "-s", serial, "shell", "ls -la /data/local/tmp/test_screen.png"], capture_output=True, text=True, timeout=10)
print("Screenshot:", r4.stdout.strip())
