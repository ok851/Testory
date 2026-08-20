import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Add a small delay between control connect and handshake read
old = """        # ⑦ 读取视频握手（此时两条连接已建立，server 会发送握手）
        device_name = self._read_video_handshake(sock, deadline)"""

new = """        # ⑦ 读取视频握手（此时两条连接已建立，server 会发送握手）
        time.sleep(0.3)  # 给 server 时间初始化两条连接
        device_name = self._read_video_handshake(sock, deadline)"""

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added delay before handshake read")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): added delay")
    else:
        print("NOT FOUND")
