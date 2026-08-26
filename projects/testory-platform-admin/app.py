# -*- coding: utf-8 -*-
"""创始人控制面 Web 应用（独立项目）。"""
from __future__ import annotations

import os
import secrets
import sys
import time
import uuid
from functools import wraps
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_DIR.parents[1]
for _p in (PROJECT_DIR, REPO_ROOT / "packages"):
    _ps = str(_p)
    if _p.is_dir() and _ps not in sys.path:
        sys.path.insert(0, _ps)

from testory_common.bootstrap import bootstrap_project  # noqa: E402

bootstrap_project(project_dir=PROJECT_DIR)

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from admin_database import PlatformAdminDB  # noqa: E402
from modules.auth.license_manager import LicenseManager, LicenseType  # noqa: E402
from testory_common.pay_token import verify_pay_token  # noqa: E402

RELEASE_FILES_DIR = PROJECT_DIR / "data" / "release_files"
RELEASE_FILES_DIR.mkdir(parents=True, exist_ok=True)
WEBSITE_URL = (os.environ.get("WEBSITE_URL") or "http://127.0.0.1:5200").strip().rstrip("/")
SYNC_SECRET = (os.environ.get("PLATFORM_SYNC_SECRET") or os.environ.get("PLATFORM_ADMIN_SECRET") or "").strip()

PLAN_PRICES = {
    "professional": {"monthly": 99.0, "yearly": 999.0, "name": "团队版"},
    "enterprise": {"monthly": 299.0, "yearly": 2999.0, "name": "企业版"},
}

app = Flask(
    __name__,
    template_folder=str(PROJECT_DIR / "templates"),
    static_folder=str(PROJECT_DIR / "static"),
    static_url_path="/static",
)
app.secret_key = (os.environ.get("PLATFORM_ADMIN_SECRET") or secrets.token_hex(32)).strip()
PATH_PREFIX = (os.environ.get("PLATFORM_ADMIN_PATH_PREFIX") or "").strip().rstrip("/")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
if os.environ.get("PLATFORM_ADMIN_SECURE_COOKIES", "").strip().lower() in ("1", "true", "yes"):
    app.config["SESSION_COOKIE_SECURE"] = True

_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_LOGIN_RATE_WINDOW_SEC = 60
_LOGIN_RATE_MAX = 5
_db = PlatformAdminDB()
_lm = LicenseManager()

_admin_user = (os.environ.get("PLATFORM_ADMIN_USER") or "founder").strip()
_admin_pass = (os.environ.get("PLATFORM_ADMIN_PASSWORD") or "").strip()
if not _admin_pass:
    _admin_pass = secrets.token_urlsafe(12)
    print(f"[platform_admin] 首次启动默认密码（请尽快修改）: {_admin_pass}")
_db.ensure_admin(_admin_user, generate_password_hash(_admin_pass))


class _ScriptNameMiddleware:
    """识别 Nginx /admin 子路径，使 url_for 与静态资源带正确前缀。"""

    def __init__(self, app, default_prefix: str = ""):
        self.app = app
        self.default_prefix = (default_prefix or "").rstrip("/")

    def __call__(self, environ, start_response):
        prefix = (environ.get("HTTP_X_SCRIPT_NAME") or self.default_prefix or "").strip().rstrip("/")
        if prefix:
            environ["SCRIPT_NAME"] = prefix
        return self.app(environ, start_response)


app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
if PATH_PREFIX:
    app.wsgi_app = _ScriptNameMiddleware(app.wsgi_app, PATH_PREFIX)


def _client_ip() -> str:
    return (request.headers.get("X-Real-IP") or request.remote_addr or "").strip()


def _login_rate_ok(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < _LOGIN_RATE_WINDOW_SEC]
    _LOGIN_ATTEMPTS[ip] = attempts
    return len(attempts) < _LOGIN_RATE_MAX


def _record_login_failure(ip: str) -> None:
    _LOGIN_ATTEMPTS.setdefault(ip, []).append(time.time())


@app.context_processor
def inject_admin_globals():
    return {"website_url": WEBSITE_URL}


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("platform_admin"):
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "error": "未登录"}), 401
            return redirect(url_for("login_page"))
        return fn(*args, **kwargs)

    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        ip = _client_ip()
        if not _login_rate_ok(ip):
            return render_template("login.html", error="登录尝试过于频繁，请稍后再试")
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = _db.get_admin_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            _LOGIN_ATTEMPTS.pop(ip, None)
            session["platform_admin"] = username
            return redirect(url_for("dashboard"))
        _record_login_failure(ip)
        return render_template("login.html", error="用户名或密码错误")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("platform_admin", None)
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def dashboard():
    stats = _db.dashboard_stats()
    return render_template("dashboard.html", stats=stats, active_page="dashboard")


@app.route("/licenses")
@login_required
def licenses_page():
    return render_template("licenses.html", licenses=_db.list_licenses(), active_page="licenses")


@app.route("/releases")
@login_required
def releases_page():
    return render_template("releases.html", releases=_db.list_releases(), active_page="releases")


@app.route("/orders")
@login_required
def orders_page():
    return render_template("orders.html", orders=_db.list_orders(), active_page="orders")


@app.route("/users")
@login_required
def users_page():
    return render_template("users.html", users=_db.list_product_users(), active_page="users")


@app.route("/visits")
@login_required
def visits_page():
    stats = _db.visit_stats(30)
    recent = _db.list_recent_visits(80)
    return render_template(
        "visits.html",
        stats=stats,
        recent=recent,
        active_page="visits",
    )


def _direct_download_url(release: dict) -> str:
    url = (release.get("download_url") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{WEBSITE_URL}/download/latest"


def _check_sync_secret() -> bool:
    if not SYNC_SECRET:
        return False
    return (request.headers.get("X-Platform-Sync-Secret") or "") == SYNC_SECRET


def _serve_release_download(release: dict):
    """记录下载并返回文件或重定向。"""
    rid = int(release["id"])
    _db.record_download(rid, ip=request.remote_addr or "", user_agent=(request.headers.get("User-Agent") or "")[:500])
    local_name = (release.get("local_filename") or "").strip()
    if local_name:
        path = RELEASE_FILES_DIR / local_name
        if path.is_file():
            dl_name = (release.get("original_filename") or f"Testory_Setup_{release.get('version', 'latest')}.exe").strip()
            return send_file(path, as_attachment=True, download_name=dl_name)
    url = (release.get("download_url") or "").strip()
    if url and (url.startswith("http://") or url.startswith("https://")):
        return redirect(url)
    return jsonify({"success": False, "error": "未配置安装包文件或下载地址"}), 404


@app.route("/api/public/download/latest", methods=["GET"])
def api_public_download_latest():
    """官网/公开：下载最新版安装包（直接触发浏览器下载）。"""
    release = _db.get_latest_release()
    if not release:
        return jsonify({"success": False, "error": "暂无可用安装包"}), 404
    return _serve_release_download(release)


@app.route("/api/public/latest-release", methods=["GET"])
def api_public_latest_release():
    release = _db.get_latest_release()
    if not release:
        return jsonify({"success": True, "release": None})
    payload = {
        "id": release.get("id"),
        "version": release.get("version"),
        "download_count": release.get("download_count"),
        "has_file": bool(release.get("local_filename") or (release.get("download_url") and (release.get("download_url", "").startswith("http://") or release.get("download_url", "").startswith("https://")))),
        "file_size_bytes": release.get("file_size_bytes") or 0,
        "original_filename": release.get("original_filename") or "",
        "website_download_url": f"{WEBSITE_URL}/download/latest",
        "direct_download_url": _direct_download_url(release),
    }
    return jsonify({"success": True, "release": payload})


@app.route("/api/public/visit", methods=["POST"])
def api_public_visit():
    """官网埋点：记录一次页面访问（无需登录）。"""
    data = request.get_json(silent=True) or {}
    visitor_id = (data.get("visitor_id") or "").strip()
    path = (data.get("path") or "/").strip() or "/"
    if not visitor_id:
        return jsonify({"success": False, "error": "visitor_id 必填"}), 400
    if len(path) > 500:
        path = path[:500]
    _db.record_site_visit(
        visitor_id=visitor_id,
        path=path,
        referrer=(data.get("referrer") or "")[:500],
        title=(data.get("title") or "")[:200],
        ip=(
            (data.get("ip") or "").strip()
            or (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
        ),
        user_agent=(data.get("user_agent") or request.headers.get("User-Agent") or "")[:500],
    )
    return jsonify({"success": True})


@app.route("/api/platform/users/sync", methods=["POST"])
def api_sync_product_user():
    """客户端/团队服务器登录后同步用户到平台用户库。"""
    if not _check_sync_secret():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    uid = int(data.get("user_id") or data.get("external_user_id") or 0)
    username = (data.get("username") or "").strip()
    if not uid or not username:
        return jsonify({"success": False, "error": "user_id 与 username 必填"}), 400
    pid = _db.upsert_product_user(
        uid,
        username,
        email=(data.get("email") or "").strip(),
        team_server_url=(data.get("team_server_url") or "").strip(),
        license_type=(data.get("license_type") or "free").strip(),
    )
    return jsonify({"success": True, "platform_user_id": pid})


@app.route("/api/platform/pay-token/verify", methods=["POST"])
def api_verify_pay_token():
    """官网校验软件跳转支付 token。"""
    data = request.get_json(silent=True) or {}
    payload = verify_pay_token((data.get("pay_token") or data.get("token") or "").strip())
    if not payload:
        return jsonify({"success": False, "error": "支付凭证无效或已过期，请返回软件重新打开"}), 401
    _db.upsert_product_user(
        int(payload["uid"]),
        payload["username"],
        email=payload.get("email") or "",
        team_server_url=payload.get("team_server_url") or "",
    )
    return jsonify({"success": True, "user": payload})


@app.route("/api/licenses", methods=["POST"])
@login_required
def api_create_license():
    data = request.get_json(silent=True) or {}
    ltype_raw = (data.get("license_type") or "professional").strip().lower()
    type_map = {
        "free": LicenseType.FREE,
        "professional": LicenseType.PROFESSIONAL,
        "enterprise": LicenseType.ENTERPRISE,
    }
    ltype = type_map.get(ltype_raw, LicenseType.PROFESSIONAL)
    issued_to = (data.get("issued_to") or "Customer").strip()
    days = int(data.get("expires_days") or 365)
    binding_type = (data.get("binding_type") or "").strip()
    binding_id = (data.get("binding_id") or "").strip()
    seat_count = int(data.get("seat_count") or 0)
    license_id = f"lic_{uuid.uuid4().hex[:16]}"
    custom = {}
    if seat_count > 0:
        custom["max_users"] = seat_count
    # 仅当填写 binding_id 时才写入密钥；否则留空，由客户端激活时绑定本机
    key_binding_type = binding_type if binding_id else ""
    key_binding_id = binding_id if binding_id else ""
    key = _lm.generate_license(
        ltype,
        issued_to=issued_to,
        expires_days=days,
        custom_limits=custom or None,
        license_id=license_id,
        binding_type=key_binding_type,
        binding_id=key_binding_id,
        seat_count=seat_count,
    )
    result = _lm.validate_license(key)
    expires = result["info"].expires_at if result.get("info") else ""
    _db.insert_license(
        license_id,
        key,
        ltype.value,
        issued_to,
        binding_type,
        binding_id,
        expires,
    )
    if binding_id:
        _db.record_activation(license_id, binding_type or "machine", binding_id)
    return jsonify({"success": True, "license_id": license_id, "license_key": key})


@app.route("/api/licenses/<license_id>", methods=["GET"])
@login_required
def api_get_license(license_id: str):
    lic = _db.get_license(license_id)
    if not lic:
        return jsonify({"success": False, "error": "未找到 License"}), 404
    activations = _db.list_license_activations(license_id)
    return jsonify({"success": True, "license": lic, "activations": activations})


@app.route("/api/licenses/<license_id>/revoke", methods=["POST"])
@login_required
def api_revoke_license(license_id: str):
    ok = _db.revoke_license(license_id)
    if not ok:
        return jsonify({"success": False, "error": "未找到 License"}), 404
    return jsonify({"success": True})


@app.route("/api/licenses/revoked", methods=["GET"])
def api_revoked_list():
    """客户端/服务器心跳拉取吊销列表（无需登录）。"""
    return jsonify({"success": True, "revoked": _db.list_revoked_license_ids()})


@app.route("/api/licenses/activate", methods=["POST"])
def api_record_activation():
    data = request.get_json(silent=True) or {}
    license_id = (data.get("license_id") or "").strip()
    binding_type = (data.get("binding_type") or "").strip()
    binding_id = (data.get("binding_id") or "").strip()
    if not license_id or not binding_id:
        return jsonify({"success": False, "error": "license_id 与 binding_id 必填"}), 400
    if _db.is_license_revoked(license_id):
        return jsonify({"success": False, "error": "License 已吊销"}), 403
    _db.record_activation(license_id, binding_type, binding_id)
    return jsonify({"success": True})


@app.route("/api/releases", methods=["POST"])
@login_required
def api_create_release():
    data = request.get_json(silent=True) or {}
    version = (data.get("version") or "").strip()
    url = (data.get("download_url") or "").strip()
    if not version:
        return jsonify({"success": False, "error": "version 必填"}), 400
    rid = _db.insert_release(
        version,
        url,
        channel=(data.get("channel") or "stable").strip(),
        sha256=(data.get("sha256") or "").strip(),
    )
    return jsonify({"success": True, "release_id": rid})


@app.route("/api/releases/upload", methods=["POST"])
@login_required
def api_upload_release():
    """上传安装包文件并关联到指定版本（或自动创建版本）。"""
    version = (request.form.get("version") or "").strip()
    release_id = request.form.get("release_id", type=int)
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"success": False, "error": "请选择安装包文件"}), 400
    if not release_id and not version:
        return jsonify({"success": False, "error": "请填写版本号或选择已有版本"}), 400
    if not release_id:
        release_id = _db.insert_release(version, download_url="")
    safe_ext = Path(f.filename).suffix.lower() or ".exe"
    stored = f"release_{release_id}_{uuid.uuid4().hex[:10]}{safe_ext}"
    dest = RELEASE_FILES_DIR / stored
    f.save(dest)
    size = dest.stat().st_size if dest.is_file() else 0
    _db.update_release_file(release_id, stored, f.filename, size)
    return jsonify(
        {
            "success": True,
            "release_id": release_id,
            "original_filename": f.filename,
            "file_size_bytes": size,
            "public_download_url": f"{WEBSITE_URL}/download/latest",
        }
    )


@app.route("/api/releases/<int:release_id>/download", methods=["GET", "POST"])
def api_track_download(release_id: int):
    release = _db.get_release(release_id)
    if not release:
        return jsonify({"success": False, "error": "版本不存在"}), 404
    if request.method == "GET":
        return _serve_release_download(release)
    _db.record_download(release_id, ip=request.remote_addr or "", user_agent=(request.headers.get("User-Agent") or "")[:500])
    return jsonify({"success": True})


@app.route("/api/website/orders", methods=["POST"])
def api_website_create_order():
    """官网创建订单（需 pay_token）。"""
    data = request.get_json(silent=True) or {}
    user = {}
    payload = verify_pay_token((data.get("pay_token") or "").strip())
    if payload:
        user = payload
    if not user:
        return jsonify({"success": False, "error": "请先登录软件后再前往官网支付", "need_login": True}), 401
    plan = (data.get("plan_type") or "professional").strip()
    period = (data.get("period") or "monthly").strip()
    prices = PLAN_PRICES.get(plan, PLAN_PRICES["professional"])
    amount = prices.get(period, prices["monthly"])
    order_no = f"WEB{uuid.uuid4().hex[:12].upper()}"
    _db.insert_order(
        order_no,
        user.get("username") or "",
        plan,
        float(amount),
        status="pending",
        user_id=int(user.get("uid") or 0),
        username=user.get("username") or "",
        email=user.get("email") or "",
        period=period,
    )
    return jsonify(
        {
            "success": True,
            "order": {
                "order_no": order_no,
                "plan_type": plan,
                "plan_name": prices["name"],
                "period": period,
                "amount": amount,
                "username": user.get("username"),
            },
        }
    )


@app.route("/api/website/orders/<order_no>/pay", methods=["POST"])
def api_website_mock_pay(order_no: str):
    """官网模拟支付完成并签发 License（生产环境对接微信/支付宝回调）。"""
    order = _db.get_order(order_no)
    if not order:
        return jsonify({"success": False, "error": "订单不存在"}), 404
    if order.get("status") == "paid":
        return jsonify({"success": True, "message": "订单已支付", "license_id": order.get("license_id")})
    plan_map = {
        "professional": LicenseType.PROFESSIONAL,
        "enterprise": LicenseType.ENTERPRISE,
        "free": LicenseType.FREE,
    }
    ltype = plan_map.get(order.get("plan_type"), LicenseType.PROFESSIONAL)
    license_id = f"lic_{uuid.uuid4().hex[:16]}"
    key = _lm.generate_license(
        ltype,
        issued_to=order.get("username") or order.get("customer_name") or "Customer",
        expires_days=365 if order.get("period") == "yearly" else 30,
        license_id=license_id,
    )
    result = _lm.validate_license(key)
    expires = result["info"].expires_at if result.get("info") else ""
    _db.insert_license(
        license_id,
        key,
        ltype.value,
        order.get("username") or "",
        binding_type="",
        binding_id="",
        expires_at=expires,
    )
    _db.update_order_status(order_no, "paid", license_id)
    return jsonify({"success": True, "license_id": license_id, "license_key": key})


@app.route("/api/orders", methods=["POST"])
@login_required
def api_create_order():
    data = request.get_json(silent=True) or {}
    order_no = (data.get("order_no") or f"ORD{uuid.uuid4().hex[:12].upper()}").strip()
    oid = _db.insert_order(
        order_no,
        (data.get("customer_name") or "").strip(),
        (data.get("plan_type") or "professional").strip(),
        float(data.get("amount") or 0),
        status=(data.get("status") or "pending").strip(),
        license_id=(data.get("license_id") or "").strip(),
    )
    return jsonify({"success": True, "order_id": oid, "order_no": order_no})


@app.route("/api/stats", methods=["GET"])
@login_required
def api_stats():
    return jsonify({"success": True, "stats": _db.dashboard_stats()})


def main():
    port = int(os.environ.get("PLATFORM_ADMIN_PORT", "5100"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
