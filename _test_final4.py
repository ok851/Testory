import subprocess, time, sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

from mobile_scrcpy_bridge import ensure_scrcpy_device_session, _check_device_screen_on
ok, msg = _check_device_screen_on(serial)
print(f"Screen: {ok}, {msg}")

print("Trying scrcpy session...")
sess, err = ensure_scrcpy_device_session(serial)
if not sess:
    print("FAILED:", err[:500])
    sys.exit(1)

print(f"SUCCESS! Running={sess.running} Version={sess._version}")
print(f"Control: {sess._control_socket}")
