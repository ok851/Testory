import subprocess, time, socket, sys, threading
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

from mobile_scrcpy_bridge import (
    _stable_serial_port, find_scrcpy_server_jar, _scrcpy_control_enabled,
    _run_adb, _stable_serial_scid, _abstract_socket_name, _scrcpy_server_version,
    read_forward_handshake, _kill_stale_scrcpy_servers, adb_path
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

# Build args exactly like to_server_args
args = [
    "max_fps=30", "video_bit_rate=8000000", "tunnel_forward=true",
    f"control={'true' if ctrl else 'false'}", "audio=false",
    "show_touches=false", "send_frame_meta=true", "log_level=verbose",
]
args.append(f"max_size=1920")
shell_cmd = f"CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.genymobile.scrcpy.Server {ver} {' '.join(args)}"

print(f"Control={ctrl} Port={port} Abstract={abstract}")
print(f"Cmd: {shell_cmd}")

proc = subprocess.Popen(
    [adb_path(), "-s", serial, "shell", shell_cmd],
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
)

stderr_lines = []
def drain():
    for line in proc.stderr:
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            stderr_lines.append(text)
threading.Thread(target=drain, daemon=True).start()

time.sleep(2)
print(f"Poll: {proc.poll()}")

# Step 1: Video connect
sock = socket.create_connection(("127.0.0.1", port), timeout=10)
sock.settimeout(15)
print("Video connected")

# Step 2: Control connect
if ctrl:
    ctrl_sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    ctrl_sock.settimeout(5)
    print("Control connected")

# Step 3: Read handshake (EXACTLY like read_forward_handshake)
print("Reading handshake...")
try:
    first = sock.recv(1)
    print(f"recv(1) returned: {repr(first)} (len={len(first)})")
    if not first:
        print("Empty! Connection closed by server")
    elif first == b"\x00":
        rest = sock.recv(64)
        print(f"recv(64) returned: {repr(rest[:20])}... (len={len(rest)})")
        name = rest.split(b"\x00")[0].decode("utf-8", errors="replace")
        print(f"Device: {name}")
    else:
        rest = sock.recv(63)
        full = first + rest
        name = full.split(b"\x00")[0].decode("utf-8", errors="replace")
        print(f"Device (no dummy): {name}")
    print("SUCCESS!")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    print(f"Poll: {proc.poll()}")
    print(f"Stderr: {stderr_lines[:5]}")

sock.close()
proc.terminate()
