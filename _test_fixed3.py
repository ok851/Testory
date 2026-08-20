import subprocess, time, sys
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

# Clean up
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)
subprocess.run([adb, "-s", serial, "forward", "--remove-all"], capture_output=True, timeout=8)
time.sleep(1)

# Wake screen
for _ in range(3):
    subprocess.run([adb, "-s", serial, "shell", "input keyevent 224"], capture_output=True, timeout=5)
    time.sleep(0.3)
subprocess.run([adb, "-s", serial, "shell", "input swipe 540 1800 540 600 300"], capture_output=True, timeout=5)
time.sleep(0.3)
subprocess.run([adb, "-s", serial, "shell", "wm dismiss-keyguard"], capture_output=True, timeout=5)
time.sleep(0.5)
subprocess.run([adb, "-s", serial, "shell", "svc power stayon true"], capture_output=True, timeout=5)
time.sleep(0.5)

r = subprocess.run([adb, "-s", serial, "shell", "dumpsys power"], capture_output=True, text=True, timeout=10)
screen_on = "mWakefulness=Awake" in r.stdout
print("Screen:", "ON" if screen_on else "OFF")
if not screen_on:
    sys.exit(1)

from mobile_scrcpy_bridge import ensure_scrcpy_device_session
print("Trying scrcpy session...")
sess, err = ensure_scrcpy_device_session(serial)
print("Session:", sess)
print("Error:", err if not sess else "none")
if sess:
    print("Running:", sess.running)
    print("Version:", sess._version)
    print("Port:", sess.local_port)
    print("Control:", sess._control_socket)
    # Try reading a frame
    import queue
    from mobile_scrcpy_bridge import get_scrcpy_relay, ScrcpyPacket
    relay = get_scrcpy_relay(serial)
    sid, q = relay.subscribe()
    print("Waiting for frame...")
    try:
        pkt = q.get(timeout=8)
        if isinstance(pkt, ScrcpyPacket):
            print("Got packet! size=%d config=%s key=%s" % (len(pkt.payload), pkt.is_config, pkt.is_key))
        else:
            print("Got data:", type(pkt), len(pkt) if pkt else 0)
    except queue.Empty:
        print("No frame received in 8s")
    relay.unsubscribe(sid)
