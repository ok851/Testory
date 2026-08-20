import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

old = """        self._start_stderr_drain()

        uat_logger.info(
            "scrcpy 尝试启动: serial=%s version=%s profile=%s fps=%d bitrate=%d max_size=%d",
            serial, self._version, params.profile_name,
            params.max_fps, params.video_bit_rate, params.max_size,
        )

        # ⑤ 建立 TCP 连接（视频 + 控制）"""

new = """        self._start_stderr_drain()
        time.sleep(1.0)  # 等待 server 进程启动并绑定 abstract socket

        uat_logger.info(
            "scrcpy 尝试启动: serial=%s version=%s profile=%s fps=%d bitrate=%d max_size=%d",
            serial, self._version, params.profile_name,
            params.max_fps, params.video_bit_rate, params.max_size,
        )

        # ⑤ 建立 TCP 连接（视频 + 控制）"""

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added startup delay")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): added startup delay")
    else:
        print("NOT FOUND")
