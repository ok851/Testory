import subprocess, time, sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import _kill_stale_scrcpy_servers, _run_adb

serial = "3B163L00CF800000"
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"

# First start a server
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(0.5)

# Start a dummy server
r = subprocess.run([adb, "-s", serial, "shell", "CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.genymobile.scrcpy.Server 2.4"], capture_output=True, timeout=5)
print(f"Dummy server: rc={r.returncode}")

# Check if process exists
r2 = subprocess.run([adb, "-s", serial, "shell", "ps -ef | grep scrcpy"], capture_output=True, text=True, timeout=5)
print(f"Processes: {r2.stdout.strip()}")

# Try pkill
r3 = _run_adb(serial, "shell", "pkill -f com.genymobile.scrcpy.Server", timeout=8)
print(f"pkill rc: {r3.returncode}")

time.sleep(0.5)
r4 = subprocess.run([adb, "-s", serial, "shell", "ps -ef | grep scrcpy"], capture_output=True, text=True, timeout=5)
print(f"After pkill: {r4.stdout.strip()}")

# Check abstract sockets
r5 = subprocess.run([adb, "-s", serial, "shell", "cat /proc/net/unix | grep scrcpy"], capture_output=True, text=True, timeout=5)
print(f"Sockets: {r5.stdout.strip()}")
