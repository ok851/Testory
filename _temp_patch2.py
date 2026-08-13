# -*- coding: utf-8 -*-
"""Add job status check endpoint to mobile_sync_store.py"""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_sync_store.py"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Insert the new endpoint after the /api/mobile/sync/run/<job_id>/events endpoint
marker = (
    '        return jsonify({"success": True})\n'
    '\n'
    '    @app.route("/api/mobile/sync/ai/status", methods=["GET"])'
)
replacement = (
    '        return jsonify({"success": True})\n'
    '\n'
    '    @app.route("/api/mobile/sync/run/<job_id>/status", methods=["GET"])\n'
    '    @api_error_handler\n'
    '    def api_mobile_sync_run_job_status(job_id: str):\n'
    '        """轻量级状态查询：手机回放中每步轮询，检测 PC 是否已取消。"""\n'
    '        meta, err = resolve_device_token()\n'
    '        if err:\n'
    '            return err\n'
    '        info = get_run_job_status_lite(job_id)\n'
    '        if info is None:\n'
    '            return jsonify({"success": False, "error": "job 不存在"}), 404\n'
    '        # 手机端只需判断是否应中止\n'
    '        info["should_abort"] = info["status"] == "cancelled"\n'
    '        return jsonify({"success": True, **info})\n'
    '\n'
    '    @app.route("/api/mobile/sync/ai/status", methods=["GET"])'
)
assert marker in content, "marker not found"
content = content.replace(marker, replacement, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: added /api/mobile/sync/run/<job_id>/status endpoint")
