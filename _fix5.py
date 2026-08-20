import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Add the function before _find_scrcpy_server_jar
marker = "def find_scrcpy_server_jar() -> Optional[str]:"

new_func = '''
def _kill_stale_scrcpy_servers(serial: str) -> None:
    """清理设备上残留的 scrcpy-server 进程，避免 abstract socket 被占用。"""
    try:
        r = _run_adb(serial, "shell", "pkill -f com.genymobile.scrcpy.Server", timeout=8)
        if r.returncode == 0:
            uat_logger.info("已清理残留 scrcpy-server 进程 serial=%s", serial)
            time.sleep(0.5)
    except Exception:
        pass
    # also try kill -9
    try:
        _run_adb(serial, "shell", "pkill -9 -f com.genymobile.scrcpy.Server", timeout=8)
    except Exception:
        pass

'''

if marker in text:
    text = text.replace(marker, new_func + marker, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added _kill_stale_scrcpy_servers")
else:
    print("NOT FOUND")
