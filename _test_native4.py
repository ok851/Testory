import subprocess, time, threading
serial = "3B163L00CF800000"
scrcpy_exe = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\scrcpy\scrcpy.exe"
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"

# Clean up
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

# Run native scrcpy with window
print("Testing native scrcpy.exe...")
proc = subprocess.Popen(
    [scrcpy_exe, "-s", serial, "--verbosity=verbose", "--max-fps=10", "--video-bit-rate=2000000", "--window-title=test"],
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

time.sleep(10)
print("Process poll:", proc.poll())
print("\nAll output:")
for l in all_lines:
    print("  ", l)
proc.terminate()
