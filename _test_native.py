import subprocess, time
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"
scrcpy_exe = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\scrcpy\scrcpy.exe"

# Clean up
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

# Try native scrcpy.exe with --no-window to see if it connects
print("Testing native scrcpy.exe...")
proc = subprocess.Popen(
    [scrcpy_exe, "-s", serial, "--no-window", "--verbosity=verbose", "--max-fps=10", "--video-bit-rate=2000000"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
)

import threading
stdout_lines = []
stderr_lines = []
def drain(out, arr):
    for line in out:
        arr.append(line.decode("utf-8", errors="replace").strip())
threading.Thread(target=drain, args=(proc.stdout, stdout_lines), daemon=True).start()
threading.Thread(target=drain, args=(proc.stderr, stderr_lines), daemon=True).start()

time.sleep(10)
print("Process poll:", proc.poll())
print("STDOUT:")
for l in stdout_lines[:10]:
    print("  ", l)
print("STDERR:")
for l in stderr_lines[:20]:
    print("  ", l)
proc.terminate()
