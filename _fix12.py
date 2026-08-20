import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

old = """    def _read_video_handshake(self, sock: socket.socket, deadline: float) -> bytes:
        \"\"\"从已建立的视频 socket 读取握手数据。\"\"\"
        remaining = max(5.0, deadline - time.time())
        sock.settimeout(remaining)
        try:
            device_name = read_forward_handshake(sock)
            return device_name
        except (ConnectionError, socket.timeout) as exc:
            hint = self._stderr_hint()
            msg = "scrcpy 握手读取失败"
            if hint:
                msg += f"：{hint}"
            raise RuntimeError(msg) from exc"""

new = """    def _read_video_handshake(self, sock: socket.socket, deadline: float) -> bytes:
        \"\"\"从已建立的视频 socket 读取握手数据。\"\"\"
        remaining = max(5.0, deadline - time.time())
        sock.settimeout(remaining)
        try:
            device_name = read_forward_handshake(sock)
            return device_name
        except Exception as exc:
            proc_alive = self._shell_proc and self._shell_proc.poll() is None
            hint = self._stderr_hint()
            msg = f"scrcpy 握手读取失败 ({type(exc).__name__}: {exc}, proc_alive={proc_alive})"
            if hint:
                msg += f" stderr={hint[:200]}"
            raise RuntimeError(msg) from exc"""

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: improved error logging")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): improved error logging")
    else:
        print("NOT FOUND")
