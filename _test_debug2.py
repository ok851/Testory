import subprocess, time, socket, sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
from mobile_scrcpy_bridge import (
    _stable_serial_port, find_scrcpy_server_jar, _scrcpy_control_enabled,
    _kill_stale_scrcpy_servers, _run_adb, _stable_serial_scid, _abstract_socket_name,
    _scrcpy_server_version, read_forward_handshake
)

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

# Simulate exactly what _do_start_once does
_kill_stale_scrcpy_servers(serial)
time.sleep(0.5)

jar = find_scrcpy_server_jar()
_run_adb(serial, "push", jar, "/data/local/tmp/scrcpy-server.jar", timeout=60)

port = _stable_serial_port(serial)
scid = _stable_serial_scid(serial)
ver = _scrcpy_server_version()
abstract = _abstract_socket_name(ver, scid)
print(f"Port={port} SCID={scid} Version={ver} Abstract={abstract}")

_run_adb(serial, "forward", "--remove", f"tcp:{port}")
fwd = _run_adb(serial, "forward", f"tcp:{port}", abstract)
print(f"Forward: rc={fwd.returncode}")

ctrl = _scrcpy_control_enabled()
shell_cmd = (
    f"CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / "
    f"com.genymobile.scrcpy.Server {ver} max_fps=30 video_bit_rate=8000000 "
    f"tunnel_forward=true control={'true' if ctrl else 'false'} audio=false "
    f"show_touches=false send_frame_meta=true log_level=verbose max_size=1920"
)
print(f"Control={ctrl}")
print(f"Cmd: {shell_cmd}")

proc = subprocess.Popen(
    [adb, "-s", serial, "shell", shell_cmd],
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
)

import threading
stderr_lines = []
def drain():
    for line in proc.stderr:
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            stderr_lines.append(text)
            print(f"STDERR: {text}")
t = threading.Thread(target=drain, daemon=True)
t.start()

time.sleep(2)
print(f"Process poll: {proc.poll()}")

# Connect video
print("Connecting video...")
try:
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.settimeout(15)
    print("Video connected")
except Exception as e:
    print(f"Video failed: {e}")
    proc.terminate()
    sys.exit(1)

# Connect control
if ctrl:
    print("Connecting control...")
    try:
        ctrl_sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        ctrl_sock.settimeout(5)
        print("Control connected")
    except Exception as e:
        print(f"Control failed: {e}")

time.sleep(0.5)
print(f"Process poll after connects: {proc.poll()}")

# Read handshake
print("Reading handshake...")
try:
    device_name = read_forward_handshake(sock)
    print(f"Device: {device_name.split(b'\\x00')[0].decode('utf-8', errors='replace')}")
    print("SUCCESS!")
except Exception as e:
    print(f"Handshake failed: {type(e).__name__}: {e}")
    print(f"Process poll: {proc.poll()}")
    print(f"Stderr: {stderr_lines[:5]}")

sock.close()
proc.terminate()
