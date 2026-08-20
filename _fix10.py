import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Fix: connect control socket BEFORE reading handshake
# The server expects both connections before sending the video handshake
old = """        # ⑤ TCP 握手
        sock, device_name = self._wait_tcp_handshake(time.time() + 15.0)
        uat_logger.info(
            "scrcpy 已连接: serial=%s version=%s profile=%s device=%s",
            serial, self._version, params.profile_name,
            device_name.split(b"\\x00")[0].decode("utf-8", errors="replace"),
        )
        self._socket = sock

        # ⑥ 控制通道
        if _scrcpy_control_enabled():
            try:
                ctrl = socket.create_connection(("127.0.0.1", self.local_port), timeout=8)
                ctrl.settimeout(5.0)
                self._control_socket = ctrl
            except Exception as exc:
                uat_logger.warning("scrcpy 控制通道未连接 serial=%s: %s", serial, exc)
                self._control_socket = None
        self._current_params = params
        self.running = True"""

new = """        # ⑤ TCP 连接（视频 + 控制）
        # scrcpy-server 在 control=true 时等待两条 TCP 连接都建立后才发送握手
        sock, device_name = self._wait_tcp_handshake(time.time() + 15.0)
        self._socket = sock

        # ⑥ 控制通道（必须在握手读取前建立，否则 server 会超时退出）
        if _scrcpy_control_enabled():
            try:
                ctrl = socket.create_connection(("127.0.0.1", self.local_port), timeout=8)
                ctrl.settimeout(5.0)
                self._control_socket = ctrl
            except Exception as exc:
                uat_logger.warning("scrcpy 控制通道未连接 serial=%s: %s", serial, exc)
                self._control_socket = None

        uat_logger.info(
            "scrcpy 已连接: serial=%s version=%s profile=%s device=%s control=%s",
            serial, self._version, params.profile_name,
            device_name.split(b"\\x00")[0].decode("utf-8", errors="replace"),
            bool(self._control_socket),
        )
        self._current_params = params
        self.running = True"""

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: reordered control socket before handshake completion")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): reordered control socket")
    else:
        print("NOT FOUND")
