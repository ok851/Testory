import subprocess, time, sys, queue
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

# Clean up
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

# Make sure screen is on
subprocess.run([adb, "-s", serial, "shell", "input keyevent 224"], capture_output=True, timeout=5)
time.sleep(0.5)
subprocess.run([adb, "-s", serial, "shell", "input swipe 540 1800 540 600 300"], capture_output=True, timeout=5)
time.sleep(0.3)
subprocess.run([adb, "-s", serial, "shell", "wm dismiss-keyguard"], capture_output=True, timeout=5)
time.sleep(0.5)

from mobile_scrcpy_bridge import ensure_scrcpy_device_session, get_scrcpy_relay, ScrcpyPacket
print("Trying scrcpy session...")
sess, err = ensure_scrcpy_device_session(serial)
if not sess:
    print("FAILED:", err)
    sys.exit(1)

print("SUCCESS!")
print("Running:", sess.running)
print("Version:", sess._version)
print("Port:", sess.local_port)
print("Control:", sess._control_socket)

# Get frames via relay
relay = get_scrcpy_relay(serial)
sid, q = relay.subscribe()
print("\nWaiting for frames...")
frame_count = 0
deadline = time.time() + 10
while time.time() < deadline:
    try:
        pkt = q.get(timeout=2)
        if isinstance(pkt, ScrcpyPacket):
            frame_count += 1
            if frame_count <= 3:
                print("Frame %d: size=%d config=%s key=%s" % (frame_count, len(pkt.payload), pkt.is_config, pkt.is_key))
    except queue.Empty:
        continue
print("Total frames received:", frame_count)
relay.unsubscribe(sid)
