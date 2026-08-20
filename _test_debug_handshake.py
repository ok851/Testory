import subprocess, time, socket, threading, sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import _stable_serial_port, find_scrcpy_server_jar, _scrcpy_control_enabled

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

jar = find_scrcpy_server_jar()
subprocess.run([adb, "-s", serial, "push", jar, "/data/local/tmp/scrcpy-server.jar"], capture_output=True, timeout=60)

port = _stable_serial_port(serial)
subprocess.run([adb, "-s", serial, "forward", "--remove", "tcp:%d" % port], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "tcp:%d" % port, "localabstract:scrcpy"], capture_output=True, timeout=8)

ctrl = _scrcpy_control_enabled()
print("Control enabled:", ctrl)

shell_cmd = (
    "CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / "
    "com.genymobile.scrcpy.Server 2.4 max_fps=30 video_bit_rate=8000000 "
    "tunnel_forward=true control=%s audio=false show_touches=false "
    "send_frame_meta=true log_level=verbose max_size=1920" % ("true" if ctrl else "false")
)
proc = subprocess.Popen([adb, "-s", serial, "shell", shell_cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

stderr_lines = []
def drain(out, arr):
    for line in out:
        arr.append(line.decode("utf-8", errors="replace").strip())
threading.Thread(target=drain, args=(proc.stderr, stderr_lines), daemon=True).start()

time.sleep(2)
print("Process poll:", proc.poll())

# Step 1: Connect video socket
print("Connecting video socket...")
try:
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.settimeout(10)
    print("Video socket connected")
except Exception as e:
    print("Video connect failed:", e)
    proc.terminate()
    sys.exit(1)

# Step 2: Connect control socket (if enabled)
if ctrl:
    print("Connecting control socket...")
    try:
        ctrl_sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        ctrl_sock.settimeout(5)
        print("Control socket connected")
    except Exception as e:
        print("Control connect failed:", e)

time.sleep(0.5)
print("Process poll after both connects:", proc.poll())
print("Stderr:", stderr_lines[:5])

# Step 3: Read handshake
print("Reading handshake...")
try:
    first = sock.recv(1)
    print("First byte:", repr(first))
    if first == b"\x00":
        name = sock.recv(64)
        print("Device:", name.split(b"\x00")[0].decode("utf-8", errors="replace"))
    elif first:
        rest = sock.recv(63)
        name = first + rest
        print("Device (no dummy):", name.split(b"\x00")[0].decode("utf-8", errors="replace"))
    print("SUCCESS!")
except Exception as e:
    print("Handshake failed:", type(e).__name__, str(e)[:200])
    print("Process poll:", proc.poll())
    print("Stderr:", stderr_lines[:10])

sock.close()
proc.terminate()
