import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_routes.py")
text = p.read_text(encoding="utf-8")

# Add screencap endpoint after mirror/stop route
marker = '    @app.route("/api/mobile/mirror/scrcpy-stream"'

screencap_route = '''
    @app.route("/api/mobile/mirror/screencap", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_mirror_screencap():
        """返回设备截图（JPEG），供 WebCodecs 不可用时降级投屏。"""
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        from mobile_device_manager import capture_screenshot_frame
        udid = (request.args.get("udid") or "").strip()
        if not udid:
            from mobile_device_manager import get_connected_udid
            udid = get_connected_udid() or ""
        if not udid:
            return jsonify({"success": False, "error": "no device"}), 400
        png_data, fmt = capture_screenshot_frame(udid)
        if not png_data:
            return "", 503
        from flask import Response
        return Response(png_data, mimetype="image/png" if fmt == "png" else "image/jpeg")

'''

if marker in text:
    text = text.replace(marker, screencap_route + marker, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added screencap endpoint")
else:
    print("NOT FOUND: scrcpy-stream route")
