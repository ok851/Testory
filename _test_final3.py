import subprocess, time, sys, queue
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

from mobile_scrcpy_bridge import ensure_scrcpy_device_session, get_scrcpy_relay, ScrcpyPacket, _check_device_screen_on
ok, msg = _check_device_screen_on(serial)
print(f"Screen: {ok}, {msg}")

print("Trying scrcpy session...")
sess, err = ensure_scrcpy_device_session(serial)
if not sess:
    print("FAILED:", err[:500])
    sys.exit(1)

print(f"SUCCESS! Running={sess.running} Version={sess._version}")
print(f"Control: {sess._control_socket}")

relay = get_scrcpy_relay(serial)
sid, q = relay.subscribe()
print("Waiting for frames...")
frame_count = 0
deadline = time.time() + 10
while time.time() < deadline:
    try:
        pkt = q.get(timeout=2)
        if isinstance(pkt, ScrcpyPacket):
            frame_count += 1
            if frame_count <= 3:
                print(f"Frame {frame_count}: size={len(pkt.payload)} config={pkt.is_config} key={pkt.is_key}")
    except queue.Empty:
        continue
print(f"Total frames: {frame_count}")
relay.unsubscribe(sid)
