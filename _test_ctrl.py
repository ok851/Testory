import subprocess, time, socket, threading
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

import sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import _stable_serial_port, find_scrcpy_server_jar

jar = find_scrcpy_server_jar()
subprocess.run([adb, "-s", serial, "push", jar, "/data/local/tmp/scrcpy-server.jar"], capture_output=True, timeout=60)

port = _stable_serial_port(serial)
subprocess.run([adb, "-s", serial, "forward", "--remove", "tcp:%d" % port], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "tcp:%d" % port, "localabstract:scrcpy"], capture_output=True, timeout=8)

# Test with control=false
shell_cmd = (
    "CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / "
    "com.genymobile.scrcpy.Server 2.4 max_fps=30 video_bit_rate=8000000 "
    "tunnel_forward=true control=false audio=false show_touches=false "
    "send_frame_meta=true log_level=error max_size=1920"
)
print("Testing control=false...")
proc = subprocess.Popen([adb, "-s", serial, "shell", shell_cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(3)
print("Poll:", proc.poll())
try:
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.settimeout(10)
    first = sock.recv(1)
    print("First byte:", repr(first))
    if first == b"\x00":
        name = sock.recv(64)
        print("Device:", name.split(b"\x00")[0].decode("utf-8", errors="replace"))
    sock.close()
    print("SUCCESS with control=false!")
except Exception as e:
    print("Failed:", e)
proc.terminate()
time.sleep(1)

# Now test with control=true
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)
subprocess.run([adb, "-s", serial, "forward", "tcp:%d" % port, "localabstract:scrcpy"], capture_output=True, timeout=8)

shell_cmd2 = (
    "CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / "
    "com.genymobile.scrcpy.Server 2.4 max_fps=30 video_bit_rate=8000000 "
    "tunnel_forward=true control=true audio=false show_touches=false "
    "send_frame_meta=true log_level=error max_size=1920"
)
print("\nTesting control=true...")
proc2 = subprocess.Popen([adb, "-s", serial, "shell", shell_cmd2], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
stderr_lines = []
def drain(out, arr):
    for line in out:
        arr.append(line.decode("utf-8", errors="replace").strip())
threading.Thread(target=drain, args=(proc2.stderr, stderr_lines), daemon=True).start()
time.sleep(3)
print("Poll:", proc2.poll())
try:
    sock2 = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock2.settimeout(10)
    first2 = sock2.recv(1)
    print("First byte:", repr(first2))
    if first2 == b"\x00":
        name2 = sock2.recv(64)
        print("Device:", name2.split(b"\x00")[0].decode("utf-8", errors="replace"))
    sock2.close()
    print("SUCCESS with control=true!")
except Exception as e:
    print("Failed:", type(e).__name__, str(e)[:200])
    print("Stderr:", stderr_lines[:5])
proc2.terminate()
