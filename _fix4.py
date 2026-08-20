import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Add cleanup step before param profiles generation
old = """        # ── 生成参数档位 ──
        param_profiles = _generate_param_profiles(self._device_diag)"""

new = """        # ── 阶段0.6：清理残留 scrcpy-server 进程和 stale socket ──
        _kill_stale_scrcpy_servers(self.serial)

        # ── 生成参数档位 ──
        param_profiles = _generate_param_profiles(self._device_diag)"""

if old in text:
    text = text.replace(old, new, 1)
    print("OK: added cleanup step")
else:
    old_crlf = old.replace("\n", "\r\n")
    if old_crlf in text:
        new_crlf = new.replace("\n", "\r\n")
        text = text.replace(old_crlf, new_crlf, 1)
        print("OK (CRLF): added cleanup step")
    else:
        print("NOT FOUND")

p.write_text(text, encoding="utf-8")
