import subprocess, time, socket, threading, sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import _stable_serial_port, find_scrcpy_server_jar, _run_adb

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

# Clean up
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

# Push jar
jar = find_scrcpy_server_jar()
subprocess.run([adb, "-s", serial, "push", jar, "/data/local/tmp/scrcpy-server.jar"], capture_output=True, timeout=60)

# Use localabstract:scrcpy (no scid) for 2.4
port = _stable_serial_port(serial)
subprocess.run([adb, "-s", serial, "forward", "--remove", "tcp:%d" % port], capture_output=True, timeout=8)
r = subprocess.run([adb, "-s", serial, "forward", "tcp:%d" % port, "localabstract:scrcpy"], capture_output=True, text=True, timeout=8)
print("Forward: rc=%d" % r.returncode)

# Start server WITHOUT scid
shell_cmd = (
    "CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / "
    "com.genymobile.scrcpy.Server 2.4 max_fps=10 video_bit_rate=2000000 "
    "tunnel_forward=true control=false audio=false show_touches=false "
    "send_frame_meta=true log_level=verbose"
)
print("Starting server...")
proc = subprocess.Popen(
    [adb, "-s", serial, "shell", shell_cmd],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
)

stderr_lines = []
def drain(out, arr):
    for line in out:
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            arr.append(text)
threading.Thread(target=drain, args=(proc.stderr, stderr_lines), daemon=True).start()

time.sleep(3)
print("Process poll:", proc.poll())
print("Stderr:", stderr_lines[:5])

# Check what abstract sockets exist on device
r2 = subprocess.run([adb, "-s", serial, "shell", "cat /proc/net/unix"], capture_output=True, text=True, timeout=10)
for line in r2.stdout.split("\n"):
    if "scrcpy" in line:
        print("Socket:", line.strip())

# Try TCP connect
print("Connecting to 127.0.0.1:%d..." % port)
try:
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.settimeout(10)
    print("TCP connected! Reading handshake...")
    first = sock.recv(1)
    print("First byte:", repr(first))
    if first == b"\x00":
        name = sock.recv(64)
        print("Device name:", name.split(b"\x00")[0].decode("utf-8", errors="replace"))
    sock.close()
    print("SUCCESS!")
except Exception as e:
    print("Failed:", type(e).__name__, str(e)[:200])
    print("Stderr:", stderr_lines[:10])

proc.terminate()
