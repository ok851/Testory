# -*- coding: utf-8 -*-
"""Testory 产品官网 — 独立 Flask 应用，默认端口 5200。"""
from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_DIR.parents[1]
for _p in (PROJECT_DIR, REPO_ROOT / "packages"):
    _ps = str(_p)
    if _p.is_dir() and _ps not in sys.path:
        sys.path.insert(0, _ps)

from testory_common.bootstrap import bootstrap_project  # noqa: E402

bootstrap_project(project_dir=PROJECT_DIR)

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

from testory_common.brand import brand_context  # noqa: E402
from testory_common.pay_token import verify_pay_token  # noqa: E402
from testory_common.platform_client import platform_api_json  # noqa: E402

DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
INQUIRIES_FILE = DATA_DIR / "inquiries.jsonl"

app = Flask(
    __name__,
    template_folder=str(PROJECT_DIR / "templates"),
    static_folder=str(PROJECT_DIR / "static"),
    static_url_path="/static",
)
app.secret_key = (
    os.environ.get("WEBSITE_SECRET") or os.environ.get("PLATFORM_ADMIN_SECRET") or secrets.token_hex(32)
).strip()

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


def _fetch_release_from_admin() -> dict | None:
    result = platform_api_json("/api/public/latest-release")
    if not result.get("success"):
        return None
    release = result.get("release")
    if not release:
        return None
    return {
        "id": release.get("id"),
        "version": release.get("version") or "",
        "download_count": release.get("download_count") or 0,
        "has_file": bool(release.get("has_file")),
        "file_size_bytes": release.get("file_size_bytes") or 0,
        "file_size_label": _format_file_size(int(release.get("file_size_bytes") or 0)),
        "original_filename": release.get("original_filename") or "",
        "direct_download_url": (release.get("direct_download_url") or "").strip(),
        "download_path": url_for("download_latest"),
    }


def _latest_release() -> dict | None:
    try:
        return _fetch_release_from_admin()
    except Exception:
        return None


def _site_context() -> dict:
    release = _latest_release()
    download_url = url_for("download_latest")
    has_file = bool(release and release.get("has_file"))
    return {
        "contact_email": CONTACT_EMAIL,
        "contact_phone": CONTACT_PHONE,
        "latest_release": release if has_file else {"has_file": False, "version": "", "download_count": 0, "file_size_label": ""},
        "download_url": download_url,
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


@app.route("/docs")
def docs_page():
    return render_template("docs.html", **_site_context())


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


@app.route("/help/components")
def help_components():
    """组件安装说明帮助页。"""
    return render_template("help_components.html", **_site_context())


def _proxy_admin_installer_download() -> Response:
    """内网拉取控制面安装包并透传给浏览器（同域下载，不暴露后台端口）。"""
    admin_url = f"{PLATFORM_ADMIN_URL}/api/public/download/latest"
    req = urllib.request.Request(
        admin_url,
        headers={"User-Agent": (request.headers.get("User-Agent") or "Testory-Website/1.0")[:500]},
    )
    try:
        upstream = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return Response("暂无可用安装包", status=404, mimetype="text/plain; charset=utf-8")
        return Response(f"下载失败: {e}", status=502, mimetype="text/plain; charset=utf-8")
    except Exception as e:
        return Response(f"下载失败: {e}", status=502, mimetype="text/plain; charset=utf-8")

    headers = {}
    for key in ("Content-Disposition", "Content-Type", "Content-Length"):
        val = upstream.headers.get(key)
        if val:
            headers[key] = val

    def generate():
        try:
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    return Response(generate(), headers=headers)


@app.route("/download/latest")
def download_latest():
    """同域下载：本地包经内网代理；外部 CDN 仍 302。"""
    release = _fetch_release_from_admin()
    if not release:
        return (
            render_template(
                "index.html",
                **_site_context(),
                download_error="暂无可用安装包，请联系管理员或留言预约。",
            ),
            404,
        )
    target = (release.get("direct_download_url") or "").strip()
    website_base = (os.environ.get("WEBSITE_URL") or request.host_url or "").strip().rstrip("/")
    self_url = f"{website_base}/download/latest" if website_base else url_for("download_latest", _external=True)
    real_self_url = request.host_url.rstrip("/") + "/download/latest"
    if target.startswith("http://") or target.startswith("https://"):
        target_norm = target.rstrip("/")
        if target_norm != self_url.rstrip("/") and target_norm != real_self_url.rstrip("/") and not target.startswith(PLATFORM_ADMIN_URL):
            return redirect(target)
    return _proxy_admin_installer_download()


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
        return (
            jsonify(
                {
                    "success": False,
                    "error": "请先在 Testory 软件中登录，再从软件内打开官网支付",
                    "need_login": True,
                }
            ),
            401,
        )
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
