import pathlib
p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform")

bridge = (p / "mobile_scrcpy_bridge.py").read_text(encoding="utf-8")
routes = (p / "mobile_routes.py").read_text(encoding="utf-8")
html = (p / "templates" / "mobile_testing.html").read_text(encoding="utf-8")
js = (p / "static" / "js" / "mobile_studio.js").read_text(encoding="utf-8")

checks = [
    ("bridge: CONTROL default 1", 'or "1"' in bridge and "MOBILE_SCRCPY_CONTROL" in bridge),
    ("bridge: _version_candidates simplified", "return [primary] if primary" in bridge),
    ("bridge: _kill_stale_scrcpy_servers", "def _kill_stale_scrcpy_servers" in bridge),
    ("bridge: kill in _do_start_once", "_kill_stale_scrcpy_servers(serial)" in bridge),
    ("bridge: screen check lowercase", "mwakefulness=awake" in bridge),
    ("bridge: wake checks screen first", "screen_on, _ = _check_device_screen_on" in bridge),
    ("bridge: _wait_tcp_connect", "def _wait_tcp_connect" in bridge),
    ("bridge: _read_video_handshake", "def _read_video_handshake" in bridge),
    ("bridge: startup delay", "time.sleep(1.0)" in bridge),
    ("routes: connect no warm", "_mirror_payload" not in routes.split("def _connect_response_with_mirror")[1].split("\ndef ")[0]),
    ("routes: connect has scrcpy_available", "scrcpy_available" in routes.split("def _connect_response_with_mirror")[1].split("\ndef ")[0]),
    ("html: initMirror global", "window.initMirror = initMirror" in html),
    ("html: warm=1", "warm=1" in html),
    ("html: udid check", "if(!udid)" in html),
    ("html: loading state", "loading" in html),
    ("html: player cleanup", "_mirrorPlayer" in html),
    ("js: initMirror after connect", "window.initMirror" in js),
]

print("=== Verification ===")
all_ok = True
for name, ok in checks:
    status = "OK" if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"  [{status}] {name}")

print(f"\n{'All checks passed!' if all_ok else 'Some checks FAILED!'}")
