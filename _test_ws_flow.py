import sys, subprocess, time
sys.path.insert(0, r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")
for m in list(sys.modules.keys()):
    if "mobile" in m: del sys.modules[m]

adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

# Kill stale
subprocess.run([adb, "-s", serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server"], capture_output=True, timeout=8)

from mobile_scrcpy_bridge import bridge_health, ensure_bridge_started, warm_scrcpy_session, scrcpy_bridge_url
from mobile_env_config import resolve_mirror_backend, scrcpy_available

# Step 1: bridge
ok, msg = ensure_bridge_started()
print("Bridge:", ok, msg)

# Step 2: health
h = bridge_health()
print("Health: running=%s ws=%s" % (h["bridge_running"], h["ws_url"]))

# Step 3: warm
print("Warming...")
warm_ok, warm_err = warm_scrcpy_session(serial)
print("Warm:", warm_ok, warm_err)

# Step 4: WS URL
ws_url = "%s/?serial=%s" % (scrcpy_bridge_url(), serial)
print("WS URL:", ws_url)

# Step 5: WebSocket test
try:
    import websocket
except ImportError:
    print("websocket-client not installed, trying websockets...")
    import asyncio, websockets
    async def test_ws():
        async with websockets.connect(ws_url, max_size=16*1024*1024) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            print("First msg type:", type(msg).__name__, "len:", len(msg) if isinstance(msg, bytes) else msg[:100])
            if isinstance(msg, str):
                print("Text:", msg[:200])
                msg2 = await asyncio.wait_for(ws.recv(), timeout=5)
                if isinstance(msg2, bytes):
                    print("Binary frame: %d bytes, meta=%d" % (len(msg2), msg2[0]))
    asyncio.run(test_ws())
    sys.exit(0)

ws = websocket.create_connection(ws_url, timeout=10)
print("WS connected!")
data = ws.recv()
print("First msg: type=%s len=%d" % (type(data).__name__, len(data) if isinstance(data, bytes) else 0))
if isinstance(data, str):
    print("Text:", data[:200])
ws.settimeout(5)
try:
    frame = ws.recv()
    if isinstance(frame, bytes):
        meta = frame[0] if frame else -1
        print("Frame: %d bytes, meta=%d" % (len(frame), meta))
except Exception as e:
    print("No frame:", e)
ws.close()
