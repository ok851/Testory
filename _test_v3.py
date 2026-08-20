import subprocess, time, sys, queue
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")

# Clear module cache
for mod_name in list(sys.modules.keys()):
    if "mobile_scrcpy" in mod_name or "mobile_env" in mod_name:
        del sys.modules[mod_name]

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

from mobile_scrcpy_bridge import (
    ensure_scrcpy_device_session, get_scrcpy_relay, ScrcpyPacket,
    _check_device_screen_on, find_scrcpy_server_jar, _scrcpy_server_version
)

jar = find_scrcpy_server_jar()
ver = _scrcpy_server_version()
print(f"JAR: {jar}")
print(f"Version: {ver}")

ok, msg = _check_device_screen_on(serial)
print(f"Screen: {ok}, {msg}")

print("Trying scrcpy v3.3.4 session...")
sess, err = ensure_scrcpy_device_session(serial)
if not sess:
    print(f"FAILED: {err[:600]}")
    sys.exit(1)

print(f"SUCCESS! Running={sess.running} Version={sess._version}")
print(f"Control: {sess._control_socket}")

# Start relay and get frames
relay = get_scrcpy_relay(serial)
ok2, err2 = relay.ensure_started()
print(f"Relay: ok={ok2}")

sid, q = relay.subscribe()
frame_count = 0
deadline = time.time() + 10
while time.time() < deadline:
    try:
        pkt = q.get(timeout=2)
        if isinstance(pkt, ScrcpyPacket):
            frame_count += 1
            if frame_count <= 5:
                print(f"Frame {frame_count}: size={len(pkt.payload)} config={pkt.is_config} key={pkt.is_key}")
    except queue.Empty:
        print(f"No frame in 2s (total: {frame_count})")
        continue
print(f"Total frames: {frame_count}")
relay.unsubscribe(sid)
