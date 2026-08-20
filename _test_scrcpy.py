import subprocess, time, socket, sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import _stable_serial_port, _abstract_socket_name, _stable_serial_scid, find_scrcpy_server_jar

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

# Wake screen aggressively
for _ in range(3):
    subprocess.run([adb, "-s", serial, "shell", "input keyevent 224"], capture_output=True, timeout=5)
    time.sleep(0.3)
subprocess.run([adb, "-s", serial, "shell", "input swipe 540 1800 540 600 300"], capture_output=True, timeout=5)
time.sleep(0.3)
subprocess.run([adb, "-s", serial, "shell", "wm dismiss-keyguard"], capture_output=True, timeout=5)
time.sleep(0.5)
subprocess.run([adb, "-s", serial, "shell", "svc power stayon true"], capture_output=True, timeout=5)
time.sleep(1)

# Verify screen
r = subprocess.run([adb, "-s", serial, "shell", "dumpsys power"], capture_output=True, text=True, timeout=10)
screen_on = "mWakefulness=Awake" in r.stdout or "displayPowerState=on" in r.stdout.lower()
print("Screen:", "ON" if screen_on else "OFF")
print("Power output (first 300):", r.stdout[:300])

# Kill stale
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

# Manual start
jar = find_scrcpy_server_jar()
port = _stable_serial_port(serial)
scid = _stable_serial_scid(serial)
abstract = _abstract_socket_name("2.4", scid)
print("JAR:", jar)
print("Port:", port)
print("Abstract:", abstract)
print("SCID:", scid)

# Push jar
r1 = subprocess.run([adb, "-s", serial, "push", jar, "/data/local/tmp/scrcpy-server.jar"], capture_output=True, text=True, timeout=60)
print("Push:", r1.stdout.strip())

# Forward
subprocess.run([adb, "-s", serial, "forward", "--remove", "tcp:%d" % port], capture_output=True, timeout=8)
r2 = subprocess.run([adb, "-s", serial, "forward", "tcp:%d" % port, abstract], capture_output=True, text=True, timeout=8)
print("Forward: rc=%d err=%s" % (r2.returncode, r2.stderr.strip()))

# Start server
shell_cmd = (
    "CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / "
    "com.genymobile.scrcpy.Server 2.4 max_fps=30 video_bit_rate=8000000 "
    "tunnel_forward=true control=true audio=false show_touches=false "
    "send_frame_meta=true log_level=error max_size=1920"
)
print("Starting server...")
proc = subprocess.Popen(
    [adb, "-s", serial, "shell", shell_cmd],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
)
time.sleep(3)
poll = proc.poll()
print("Process poll:", poll)

# Read any stderr so far
import select, threading
stderr_lines = []
def drain():
    for line in proc.stderr:
        stderr_lines.append(line.decode("utf-8", errors="replace").strip())
t = threading.Thread(target=drain, daemon=True)
t.start()

# Try TCP connect
print("Connecting to 127.0.0.1:%d..." % port)
try:
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    print("Connected! Reading handshake...")
    sock.settimeout(5)
    first = sock.recv(1)
    if first == b"\x00":
        name = sock.recv(64)
        print("Device name:", name.split(b"\x00")[0].decode("utf-8", errors="replace"))
    else:
        print("First byte:", first)
    sock.close()
except Exception as e:
    print("Connect failed:", e)
    time.sleep(1)
    poll2 = proc.poll()
    print("Process poll after fail:", poll2)
    print("Stderr lines:", stderr_lines[:10])

proc.terminate()
