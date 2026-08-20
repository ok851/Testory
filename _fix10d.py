import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

old = """    def _read_video_handshake(self, sock: socket.socket, deadline: float) -> bytes:
        \"\"\"从已建立的视频 socket 读取握手数据。\"\"\"
        remaining = max(0.5, deadline - time.time())
        sock.settimeout(remaining)"""

new = """    def _read_video_handshake(self, sock: socket.socket, deadline: float) -> bytes:
        \"\"\"从已建立的视频 socket 读取握手数据。\"\"\"
        remaining = max(5.0, deadline - time.time())
        sock.settimeout(remaining)"""

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: increased handshake read timeout")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): increased timeout")
    else:
        print("NOT FOUND")
