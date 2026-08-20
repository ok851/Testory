import subprocess, time, threading
serial = "3B163L00CF800000"
scrcpy_exe = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\scrcpy\scrcpy.exe"
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"

# Clean up
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

# Run native scrcpy with a window - just test for a few seconds
print("Testing native scrcpy.exe with window...")
proc = subprocess.Popen(
    [scrcpy_exe, "-s", serial, "--verbosity=verbose", "--max-fps=10", "--video-bit-rate=2000000", "--window-title=test"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)

stderr_lines = []
def drain(out, arr):
    for line in out:
        arr.append(line.decode("utf-8", errors="replace").strip())
threading.Thread(target=drain, args=(proc.stderr, stderr_lines), daemon=True).start()
stdout_lines = []
threading.Thread(target=drain, args=(proc.stdout, stdout_lines), daemon=True).start()

time.sleep(6)
print("Process poll:", proc.poll())
print("STDOUT:", stdout_lines[:5])
print("STDERR (first 20):")
for l in stderr_lines[:20]:
    print("  ", l)
proc.terminate()
