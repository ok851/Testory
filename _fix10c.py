import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Add new methods before _wait_tcp_handshake
marker = "    def _wait_tcp_handshake(self, deadline: float)"

new_methods = '''    def _wait_tcp_connect(self, deadline: float) -> socket.socket:
        """仅建立 TCP 连接（不读握手），供控制通道先建立。"""
        last_err = None
        while time.time() < deadline:
            proc = self._shell_proc
            if proc and proc.poll() is not None:
                hint = self._stderr_hint()
                msg = "scrcpy-server 进程已退出"
                if hint:
                    msg += f"：{hint}"
                raise RuntimeError(msg)
            try:
                sock = socket.create_connection(("127.0.0.1", self.local_port), timeout=2)
                sock.settimeout(20.0)
                return sock
            except OSError as exc:
                last_err = exc
            time.sleep(0.35)
        raise RuntimeError(f"scrcpy TCP 连接超时（{last_err}）")

    def _read_video_handshake(self, sock: socket.socket, deadline: float) -> bytes:
        """从已建立的视频 socket 读取握手数据。"""
        remaining = max(0.5, deadline - time.time())
        sock.settimeout(remaining)
        try:
            device_name = read_forward_handshake(sock)
            return device_name
        except (ConnectionError, socket.timeout) as exc:
            hint = self._stderr_hint()
            msg = "scrcpy 握手读取失败"
            if hint:
                msg += f"：{hint}"
            raise RuntimeError(msg) from exc

'''

if marker in text:
    text = text.replace(marker, new_methods + marker, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added _wait_tcp_connect and _read_video_handshake")
else:
    print("NOT FOUND: _wait_tcp_handshake")
