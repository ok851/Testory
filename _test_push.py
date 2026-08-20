import subprocess, os
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"
jar = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\scrcpy\scrcpy-server"
print("JAR exists:", os.path.exists(jar))
print("JAR size:", os.path.getsize(jar))
r = subprocess.run([adb, "-s", serial, "push", jar, "/data/local/tmp/scrcpy-server.jar"], capture_output=True, text=True, timeout=60)
print("Push stdout:", repr(r.stdout))
print("Push stderr:", repr(r.stderr))
print("Push rc:", r.returncode)
# Verify
r2 = subprocess.run([adb, "-s", serial, "shell", "ls -la /data/local/tmp/scrcpy-server.jar"], capture_output=True, text=True, timeout=10)
print("Verify:", r2.stdout.strip(), r2.stderr.strip())
