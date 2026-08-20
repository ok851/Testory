import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Replace the TCP handshake + control section
old = """        # ⑤ TCP 连接（视频 + 控制）
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

new = """        # ⑤ 建立 TCP 连接（视频 + 控制）
        # scrcpy-server 在 control=true 时等待两条 TCP 连接都建立后才发送握手
        # 必须先连控制通道，再读视频握手
        deadline = time.time() + 15.0
        sock = self._wait_tcp_connect(deadline)
        self._socket = sock

        # ⑥ 控制通道（在读握手前建立，否则 server 会超时退出）
        if _scrcpy_control_enabled():
            try:
                ctrl = socket.create_connection(("127.0.0.1", self.local_port), timeout=8)
                ctrl.settimeout(5.0)
                self._control_socket = ctrl
            except Exception as exc:
                uat_logger.warning("scrcpy 控制通道未连接 serial=%s: %s", serial, exc)
                self._control_socket = None

        # ⑦ 读取视频握手（此时两条连接已建立，server 会发送握手）
        device_name = self._read_video_handshake(sock, deadline)
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
    print("OK: split TCP connect and handshake read")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        print("OK (CRLF): split TCP connect and handshake read")
    else:
        print("NOT FOUND")
        # Debug
        idx = text.find("# ⑤ TCP")
        if idx >= 0:
            print(repr(text[idx:idx+100]))

p.write_text(text, encoding="utf-8")
