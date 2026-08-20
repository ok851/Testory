import subprocess, time, socket, sys, threading
from collections import deque
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")

adb_exe = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

subprocess.run([adb_exe, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb_exe, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

from mobile_scrcpy_bridge import (
    _stable_serial_port, find_scrcpy_server_jar, _scrcpy_control_enabled,
    _run_adb, _stable_serial_scid, _abstract_socket_name, _scrcpy_server_version,
    adb_path, _kill_stale_scrcpy_servers, read_forward_handshake
)

_kill_stale_scrcpy_servers(serial)
time.sleep(0.5)

jar = find_scrcpy_server_jar()
_run_adb(serial, "push", jar, "/data/local/tmp/scrcpy-server.jar", timeout=60)

port = _stable_serial_port(serial)
scid = _stable_serial_scid(serial)
ver = _scrcpy_server_version()
abstract = _abstract_socket_name(ver, scid)
ctrl = _scrcpy_control_enabled()

_run_adb(serial, "forward", "--remove", f"tcp:{port}")
_run_adb(serial, "forward", f"tcp:{port}", abstract)

args = [
    "max_fps=30", "video_bit_rate=8000000", "tunnel_forward=true",
    f"control={'true' if ctrl else 'false'}", "audio=false",
    "show_touches=false", "send_frame_meta=true", "log_level=error",
    "max_size=1920"
]
remote = "/data/local/tmp/scrcpy-server.jar"
shell_cmd = f"CLASSPATH={remote} app_process / com.genymobile.scrcpy.Server {ver} {' '.join(args)}"

print(f"Control={ctrl} Port={port} Abstract={abstract}")

proc = subprocess.Popen(
    [adb_path(), "-s", serial, "shell", shell_cmd],
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
)

stderr_lines = deque(maxlen=20)
def drain():
    assert proc.stderr is not None
    try:
        for raw in proc.stderr:
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                stderr_lines.append(text)
    except Exception:
        pass
threading.Thread(target=drain, daemon=True).start()

time.sleep(2)
print(f"Poll: {proc.poll()}")

deadline = time.time() + 15.0
sock = socket.create_connection(("127.0.0.1", port), timeout=10)
sock.settimeout(20.0)
print("Video connected")

if ctrl:
    ctrl_sock = socket.create_connection(("127.0.0.1", port), timeout=8)
    ctrl_sock.settimeout(5.0)
    print("Control connected")

time.sleep(0.3)
remaining = max(5.0, deadline - time.time())
sock.settimeout(remaining)
print(f"Reading handshake (timeout={remaining:.1f}s)...")
try:
    device_name = read_forward_handshake(sock)
    print(f"Device: {device_name.split(b'\\x00')[0].decode('utf-8', errors='replace')}")
    print("SUCCESS!")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    print(f"Poll: {proc.poll()}")
    print(f"Stderr: {list(stderr_lines)[:5]}")

sock.close()
proc.terminate()
