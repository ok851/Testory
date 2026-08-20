import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Add kill stale servers at the beginning of _do_start_once
old = '''    def _do_start_once(self, params: ScrcpyDeviceParams) -> None:
        """使用指定参数档位启动 scrcpy-server 一次。"""
        serial = self.serial
        remote = "/data/local/tmp/scrcpy-server.jar"

        # ① 推送 jar'''

new = '''    def _do_start_once(self, params: ScrcpyDeviceParams) -> None:
        """使用指定参数档位启动 scrcpy-server 一次。"""
        serial = self.serial
        remote = "/data/local/tmp/scrcpy-server.jar"

        # ① 清理残留进程（每次尝试前都清理，避免 abstract socket 被占用）
        _kill_stale_scrcpy_servers(serial)

        # ② 推送 jar'''

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added kill stale in _do_start_once")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): added kill stale")
    else:
        print("NOT FOUND")
