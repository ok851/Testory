# -*- coding: utf-8 -*-
"""Testory 产品官网 — 独立 Flask 应用，默认端口 5200。"""
from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    _env = ROOT / ".env"
    if _env.is_file():
        load_dotenv(_env, encoding="utf-8-sig")
except ImportError:
    pass

from brand_config import brand_context  # noqa: E402
from platform_pay_token import verify_pay_token  # noqa: E402
from platform_sync import platform_api_json  # noqa: E402

WEBSITE_DIR = Path(__file__).resolve().parent
DATA_DIR = WEBSITE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
INQUIRIES_FILE = DATA_DIR / "inquiries.jsonl"
RELEASE_FILES_DIR = ROOT / "data" / "release_files"

app = Flask(
    __name__,
    template_folder=str(WEBSITE_DIR / "templates"),
    static_folder=str(WEBSITE_DIR / "static"),
    static_url_path="/static",
)
app.secret_key = (os.environ.get("WEBSITE_SECRET") or os.environ.get("PLATFORM_ADMIN_SECRET") or secrets.token_hex(32)).strip()

CONTACT_EMAIL = (os.environ.get("WEBSITE_CONTACT_EMAIL") or "16608943238@163.com").strip()
CONTACT_PHONE = (os.environ.get("WEBSITE_CONTACT_PHONE") or "").strip()
PLATFORM_ADMIN_URL = (os.environ.get("PLATFORM_ADMIN_URL") or "http://127.0.0.1:5100").strip().rstrip("/")

PLAN_PRICES = {
    "professional": {"monthly": 99.0, "yearly": 999.0, "name": "团队版"},
    "enterprise": {"monthly": 299.0, "yearly": 2999.0, "name": "企业版"},
}


def _format_file_size(n: int) -> str:
    if not n:
        return ""
    if n >= 1048576:
        return f"{n / 1048576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _latest_release() -> dict | None:
    try:
        from platform_admin.database import PlatformAdminDB

        r = PlatformAdminDB().get_latest_release()
        if not r:
            return None
        has_file = bool((r.get("local_filename") or "").strip() or (r.get("download_url") or "").strip())
        return {
            "id": r.get("id"),
            "version": r.get("version") or "",
            "download_count": r.get("download_count") or 0,
            "has_file": has_file,
            "file_size_bytes": r.get("file_size_bytes") or 0,
            "file_size_label": _format_file_size(int(r.get("file_size_bytes") or 0)),
            "original_filename": r.get("original_filename") or "",
            "download_path": url_for("download_latest"),
        }
    except Exception:
        return None


def _site_context() -> dict:
    release = _latest_release()
    return {
        "contact_email": CONTACT_EMAIL,
        "contact_phone": CONTACT_PHONE,
        "latest_release": release,
        "download_url": (release or {}).get("download_path") or url_for("download_latest"),
        "year": datetime.now().year,
        "pay_user": session.get("pay_user"),
        **brand_context(),
    }


def _auth_pay_user_from_token(token: str) -> dict | None:
    payload = verify_pay_token(token)
    if not payload:
        return None
    session["pay_user"] = payload
    session["pay_token"] = token
    return payload


@app.route("/")
def index():
    return render_template("index.html", **_site_context())


@app.route("/pricing")
def pricing_page():
    token = (request.args.get("pay_token") or "").strip()
    plan = (request.args.get("plan") or "").strip()
    pay_user = session.get("pay_user")
    if token:
        pay_user = _auth_pay_user_from_token(token)
    ctx = _site_context()
    ctx["selected_plan"] = plan or "professional"
    ctx["pay_user"] = pay_user
    ctx["need_login"] = pay_user is None
    ctx["plan_prices"] = PLAN_PRICES
    return render_template("pricing.html", **ctx)


@app.route("/download/latest")
def download_latest():
    """官网一键下载：优先本地安装包，否则跳转控制面公开下载接口。"""
    try:
        from platform_admin.database import PlatformAdminDB

        release = PlatformAdminDB().get_latest_release()
        if not release:
            return render_template(
                "index.html",
                **_site_context(),
                download_error="暂无可用安装包，请联系管理员或留言预约。",
            ), 404
        local_name = (release.get("local_filename") or "").strip()
        if local_name:
            path = RELEASE_FILES_DIR / local_name
            if path.is_file():
                PlatformAdminDB().record_download(
                    int(release["id"]),
                    ip=request.remote_addr or "",
                    user_agent=(request.headers.get("User-Agent") or "")[:500],
                )
                dl_name = (
                    release.get("original_filename")
                    or f"Testory_Setup_{release.get('version', 'latest')}.exe"
                )
                return send_file(path, as_attachment=True, download_name=dl_name)
        return redirect(f"{PLATFORM_ADMIN_URL}/api/public/download/latest")
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/contact", methods=["POST"])
def api_contact():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    company = (data.get("company") or "").strip()
    message = (data.get("message") or "").strip()
    if not name or not email or not message:
        return jsonify({"success": False, "error": "请填写姓名、邮箱与留言内容"}), 400
    record = {
        "name": name,
        "email": email,
        "company": company,
        "message": message,
        "created_at": datetime.now().isoformat(),
        "ip": request.remote_addr or "",
    }
    try:
        with INQUIRIES_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": True, "message": "感谢您的留言，我们会尽快与您联系。"})


@app.route("/api/latest-release", methods=["GET"])
def api_latest_release():
    r = _latest_release()
    return jsonify({"success": True, "release": r})


@app.route("/api/checkout/create-order", methods=["POST"])
def api_checkout_create_order():
    pay_token = session.get("pay_token") or ""
    if not pay_token and not session.get("pay_user"):
        return jsonify({"success": False, "error": "请先在 Testory 软件中登录，再从软件内打开官网支付", "need_login": True}), 401
    data = request.get_json(silent=True) or {}
    result = platform_api_json(
        "/api/website/orders",
        method="POST",
        body={
            "pay_token": pay_token,
            "plan_type": (data.get("plan_type") or "professional").strip(),
            "period": (data.get("period") or "monthly").strip(),
        },
    )
    status = 200 if result.get("success") else 400
    if result.get("need_login"):
        status = 401
    return jsonify(result), status


@app.route("/api/checkout/pay/<order_no>", methods=["POST"])
def api_checkout_pay(order_no: str):
    result = platform_api_json(f"/api/website/orders/{order_no}/pay", method="POST", body={})
    status = 200 if result.get("success") else 400
    return jsonify(result), status


def main():
    port = int(os.environ.get("WEBSITE_PORT", "5200"))
    debug = os.environ.get("WEBSITE_DEBUG", "").strip().lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == "__main__":
    main()
