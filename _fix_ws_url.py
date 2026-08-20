import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_routes.py")
text = p.read_text(encoding="utf-8")

# Fix mirror/status to include mirror_ws_url with serial parameter
old = '''        if request.args.get("warm") == "1" and udid:
            from mobile_scrcpy_bridge import warm_scrcpy_session
            warm_ok, warm_err = warm_scrcpy_session(udid)
            out["scrcpy_warm_ok"] = warm_ok
            out["scrcpy_warm_error"] = "" if warm_ok else warm_err
            if warm_ok:
                out["mirror_backend"] = "scrcpy_ws"
                out["mirror_fallback_reason"] = ""
            elif out.get("mirror_backend") == "scrcpy_ws":
                out["mirror_fallback_reason"] = warm_err or "scrcpy 预热失败"
        return jsonify(out)'''

new = '''        if request.args.get("warm") == "1" and udid:
            from mobile_scrcpy_bridge import warm_scrcpy_session
            warm_ok, warm_err = warm_scrcpy_session(udid)
            out["scrcpy_warm_ok"] = warm_ok
            out["scrcpy_warm_error"] = "" if warm_ok else warm_err
            if warm_ok:
                out["mirror_backend"] = "scrcpy_ws"
                out["mirror_fallback_reason"] = ""
            elif out.get("mirror_backend") == "scrcpy_ws":
                out["mirror_fallback_reason"] = warm_err or "scrcpy 预热失败"
        # include mirror_ws_url with serial for frontend WebSocket connection
        if out.get("mirror_backend") == "scrcpy_ws" and udid:
            from mobile_env_config import scrcpy_bridge_url
            from urllib.parse import quote
            client_host = (request.host or "").split(":")[0] if request else "127.0.0.1"
            out["mirror_ws_url"] = f"{scrcpy_bridge_url(client_host)}/?serial={quote(udid, safe='')}"
        return jsonify(out)'''

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added mirror_ws_url with serial")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): added mirror_ws_url")
    else:
        print("NOT FOUND")
