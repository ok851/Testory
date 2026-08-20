import subprocess, time, socket, threading, sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import _stable_serial_port, _abstract_socket_name, _stable_serial_scid, find_scrcpy_server_jar

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

# Clean up
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

# Push jar
jar = find_scrcpy_server_jar()
r1 = subprocess.run([adb, "-s", serial, "push", jar, "/data/local/tmp/scrcpy-server.jar"], capture_output=True, text=True, timeout=60)
print("Push:", r1.stderr.strip()[:100])

# WITH tunnel_forward=true
port = _stable_serial_port(serial)
abstract = _abstract_socket_name("2.4", _stable_serial_scid(serial))
print("Port:", port, "Abstract:", abstract)
subprocess.run([adb, "-s", serial, "forward", "--remove", "tcp:%d" % port], capture_output=True, timeout=8)
r2 = subprocess.run([adb, "-s", serial, "forward", "tcp:%d" % port, abstract], capture_output=True, text=True, timeout=8)
print("Forward: rc=%d" % r2.returncode)

# Start server WITH tunnel_forward=true
shell_cmd = (
    "CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / "
    "com.genymobile.scrcpy.Server 2.4 max_fps=30 video_bit_rate=8000000 "
    "tunnel_forward=true control=true audio=false show_touches=false "
    "send_frame_meta=true log_level=verbose"
)
print("Starting server (tunnel_forward=true)...")
proc = subprocess.Popen(
    [adb, "-s", serial, "shell", shell_cmd],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
)

stderr_lines = []
def drain_stderr():
    for line in proc.stderr:
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            stderr_lines.append(text)
t = threading.Thread(target=drain_stderr, daemon=True)
t.start()

# Wait a bit for server to start
time.sleep(3)
print("Process poll:", proc.poll())
print("Stderr so far:")
for l in stderr_lines[:10]:
    print("  ", l)

# Try TCP connect
print("\nConnecting to 127.0.0.1:%d..." % port)
try:
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    print("TCP connected!")
    sock.settimeout(15)
    print("Waiting for handshake...")
    first = sock.recv(1)
    print("First byte:", repr(first))
    if first == b"\x00":
        name = sock.recv(64)
        device_name = name.split(b"\x00")[0].decode("utf-8", errors="replace")
        print("Device name:", device_name)
        # Read frame meta (8 bytes) + packet
        meta = sock.recv(8)
        print("Frame meta:", meta.hex())
        # Read some data
        data = sock.recv(4096)
        print("Data length:", len(data))
    elif first:
        # No dummy byte, first byte is part of device name
        rest = sock.recv(63)
        full = first + rest
        device_name = full.split(b"\x00")[0].decode("utf-8", errors="replace")
        print("Device name (no dummy):", device_name)
    sock.close()
    print("SUCCESS!")
except Exception as e:
    print("Failed:", type(e).__name__, str(e)[:300])
    time.sleep(1)
    print("Process poll:", proc.poll())
    print("Stderr:")
    for l in stderr_lines[:15]:
        print("  ", l)

proc.terminate()
