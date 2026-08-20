import subprocess, time, threading
serial = "3B163L00CF800000"
scrcpy_exe = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\scrcpy\scrcpy.exe"
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"

# Kill native scrcpy
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

# Check what port native scrcpy uses
print("=== Checking native scrcpy port usage ===")
proc = subprocess.Popen(
    [scrcpy_exe, "-s", serial, "--verbosity=debug", "--max-fps=10", "--video-bit-rate=2000000", "--window-title=test"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)

all_lines = []
def drain(out, name):
    for line in out:
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            all_lines.append(name + ": " + text)
threading.Thread(target=drain, args=(proc.stdout, "OUT"), daemon=True).start()
threading.Thread(target=drain, args=(proc.stderr, "ERR"), daemon=True).start()

time.sleep(5)

# Check what forwards are active
r = subprocess.run([adb, "-s", serial, "forward", "--list"], capture_output=True, text=True, timeout=5)
print("Active forwards:", r.stdout.strip())

# Check what the server is doing
r2 = subprocess.run([adb, "-s", serial, "shell", "cat /proc/net/unix"], capture_output=True, text=True, timeout=5)
for line in r2.stdout.split("\n"):
    if "scrcpy" in line:
        print("Unix socket:", line.strip())

# Print relevant lines
for l in all_lines[:15]:
    if "server" in l.lower() or "forward" in l.lower() or "connect" in l.lower() or "port" in l.lower() or "tunnel" in l.lower():
        print("  ", l)

proc.terminate()
