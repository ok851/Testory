import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Revert to_server_args: scid only for 3.x
old = '''        args.append(f"scid={scid}")'''
new = '''        if _version_major(version) >= 3:
            args.append(f"scid={scid}")'''

# Only replace the one in to_server_args (not in _abstract_socket_name)
# Find the to_server_args method
idx = text.find("def to_server_args")
if idx >= 0:
    # Find the scid line after to_server_args
    scid_idx = text.find('args.append(f"scid={scid}")', idx)
    if scid_idx >= 0 and scid_idx < idx + 500:
        text = text[:scid_idx] + new + text[scid_idx + len(old):]
        print("OK: reverted to_server_args scid (3.x only)")
    else:
        print("scid line not found after to_server_args")
else:
    print("to_server_args not found")

# Also revert _abstract_socket_name back to 3.x only
old2 = '''def _abstract_socket_name(version: str, scid: str) -> str:
    """adb forward 目标 abstract socket。2.4+ 均使用 scrcpy_<scid> 格式。"""
    return f"localabstract:scrcpy_{scid}"'''

new2 = '''def _abstract_socket_name(version: str, scid: str) -> str:
    """adb forward 目标 abstract socket（3.x 需 scrcpy_<scid>）。"""
    if _version_major(version) >= 3:
        return f"localabstract:scrcpy_{scid}"
    return "localabstract:scrcpy"'''

if old2 in text:
    text = text.replace(old2, new2, 1)
    print("OK: reverted _abstract_socket_name")
else:
    old2_c = old2.replace("\n", "\r\n")
    if old2_c in text:
        new2_c = new2.replace("\n", "\r\n")
        text = text.replace(old2_c, new2_c, 1)
        print("OK (CRLF): reverted _abstract_socket_name")
    else:
        print("_abstract_socket_name pattern not found")

p.write_text(text, encoding="utf-8")
