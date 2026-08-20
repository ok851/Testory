import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Fix 1: _abstract_socket_name — use scid for all versions
old1 = '''def _abstract_socket_name(version: str, scid: str) -> str:
    """adb forward 目标 abstract socket（3.x 需 scrcpy_<scid>）。"""
    if _version_major(version) >= 3:
        return f"localabstract:scrcpy_{scid}"
    return "localabstract:scrcpy"'''

new1 = '''def _abstract_socket_name(version: str, scid: str) -> str:
    """adb forward 目标 abstract socket。2.4+ 均使用 scrcpy_<scid> 格式。"""
    return f"localabstract:scrcpy_{scid}"'''

if old1 in text:
    text = text.replace(old1, new1, 1)
    print("OK: fixed _abstract_socket_name")
else:
    old1_c = old1.replace("\n", "\r\n")
    if old1_c in text:
        new1_c = new1.replace("\n", "\r\n")
        text = text.replace(old1_c, new1_c, 1)
        print("OK (CRLF): fixed _abstract_socket_name")
    else:
        print("NOT FOUND for _abstract_socket_name")

# Fix 2: to_server_args — add scid for all versions
old2 = '''        if _version_major(version) >= 3:
            args.append(f"scid={scid}")'''

new2 = '''        args.append(f"scid={scid}")'''

if old2 in text:
    text = text.replace(old2, new2, 1)
    print("OK: fixed to_server_args scid")
else:
    old2_c = old2.replace("\n", "\r\n")
    if old2_c in text:
        new2_c = new2.replace("\n", "\r\n")
        text = text.replace(old2_c, new2_c, 1)
        print("OK (CRLF): fixed to_server_args scid")
    else:
        print("NOT FOUND for to_server_args")

p.write_text(text, encoding="utf-8")
