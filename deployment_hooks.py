# -*- coding: utf-8 -*-
"""部署模式相关 Flask 钩子与路由（从 app.py 解耦）。"""
from __future__ import annotations

import os
from typing import Any, Callable

from flask import jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from client_config_store import (
    get_auth_token,
    get_team_server_url,
    is_setup_complete,
    mark_setup_complete,
    save_client_config,
    set_auth_token,
    set_local_standalone,
    set_team_server_url,
)
from deployment_config import (
    deployment_context,
    get_website_url,
    hide_billing_ui,
    is_client_mode,
    is_local_standalone_desktop,
    is_server_mode,
    should_delegate_execution_to_clients,
    uses_team_server,
)
from pathlib import Path
from execution_remote import register_routes as register_execution_routes
from execution_remote import start_client_worker
from instance_identity import get_identity_info, get_instance_id, get_machine_id
from license_manager import license_manager
from platform_sync import (
    build_website_payment_url,
    report_current_license_activation,
    report_license_activation,
    sync_product_user,
)
from team_server_proxy import proxy_to_team_server, should_proxy_path


def register_deployment_hooks(app, db_factory: Callable[[], Any], user_model_class=None) -> None:
    def _vendor_available(rel: str) -> bool:
        try:
            static_root = Path(app.static_folder or "static")
            return (static_root / rel.replace("/", os.sep)).is_file()
        except Exception:
            return False

    @app.context_processor
    def inject_deployment():
        ctx = deployment_context()
        ctx["client_setup_complete"] = is_setup_complete() or not is_client_mode()
        ctx["use_local_vendors"] = _vendor_available("vendor/tailwindcss/tailwind.min.js")
        try:
            from mobile_env_config import mobile_enabled

            ctx["mobile_testing_enabled"] = mobile_enabled()
        except ImportError:
            ctx["mobile_testing_enabled"] = False
        return ctx

    @app.before_request
    def proxy_team_server_api():
        if request.method == "OPTIONS":
            return None
        path = request.path or ""
        if not should_proxy_path(path):
            return None
        body = request.get_json(silent=True) if request.is_json else request.get_data()
        data, status = proxy_to_team_server(
            request.method,
            path,
            query_string=request.query_string.decode("utf-8"),
            body=body,
        )
        return jsonify(data), status

    @app.before_request
    def redirect_client_setup():
        if not is_client_mode():
            return None
        path = request.path or ""
        allowed = (
            "/client-setup",
            "/login",
            "/register",
            "/forgot-password",
            "/api/client/",
            "/api/health",
            "/api/startup/status",
            "/static/",
            "/api/auth/login",
            "/api/auth/me",
            "/api/auth/register",
            "/api/auth/forgot-password",
        )
        if any(path.startswith(p) for p in allowed):
            return None
        if not is_setup_complete() and not path.startswith("/api/"):
            if is_local_standalone_desktop():
                return redirect(url_for("login_page"))
            return redirect(url_for("client_setup_page"))

    register_execution_routes(app, db_factory, login_required)

    @app.route("/client-setup")
    def client_setup_page():
        if not is_client_mode():
            return redirect(url_for("index"))
        return render_template(
            "client_setup.html",
            team_server_url=get_team_server_url(),
            setup_complete=is_setup_complete(),
            identity=get_identity_info(),
        )

    @app.route("/api/client/config", methods=["GET"])
    def api_client_config_get():
        return jsonify(
            {
                "success": True,
                "team_server_url": get_team_server_url(),
                "setup_complete": is_setup_complete(),
                "machine_id": get_machine_id(),
                "has_token": bool(get_auth_token()),
            }
        )

    @app.route("/api/client/config", methods=["POST"])
    def api_client_config_save():
        data = request.get_json(silent=True) or {}
        url = (data.get("team_server_url") or "").strip().rstrip("/")
        if url:
            set_team_server_url(url)
            os.environ["TEAM_SERVER_URL"] = url
        if data.get("setup_complete"):
            mark_setup_complete(True)
        return jsonify({"success": True})

    @app.route("/api/client/local-mode", methods=["POST"])
    def api_client_local_mode():
        """本机独立使用：不连接团队服务器，数据保存在本机。"""
        set_local_standalone(True)
        return jsonify(
            {
                "success": True,
                "redirect": "/login",
                "message": "已切换为本机独立模式，请使用本机管理员账号登录。",
            }
        )

    @app.route("/api/client/login", methods=["POST"])
    def api_client_login_to_server():
        data = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        url = (data.get("team_server_url") or get_team_server_url()).strip().rstrip("/")
        if not url:
            return jsonify({"success": False, "error": "请先填写团队服务器地址"}), 400
        if not username or not password:
            return jsonify({"success": False, "error": "请填写用户名和密码"}), 400
        set_team_server_url(url)
        os.environ["TEAM_SERVER_URL"] = url
        try:
            from team_server_client import TeamServerError, login as ts_login

            result = ts_login(username, password)
            if not result.get("success"):
                return jsonify({"success": False, "error": result.get("error") or "登录失败"}), 401
            db = db_factory()
            user_data = db.get_user_by_username(username)
            if user_data:
                from werkzeug.security import check_password_hash
                from flask_login import login_user

                if user_data.get("is_active", 1) and check_password_hash(
                    user_data["password_hash"], password
                ):
                    if user_model_class is not None:
                        login_user(user_model_class(user_data), remember=True)
                sync_product_user(
                    user_data["id"],
                    user_data["username"],
                    email=user_data.get("email") or "",
                    team_server_url=url,
                )
            mark_setup_complete(True)
            return jsonify({"success": True, "user": result.get("user")})
        except TeamServerError as e:
            return jsonify({"success": False, "error": str(e)}), 401
        except Exception as e:
            return jsonify({"success": False, "error": f"无法连接团队服务器：{e}"}), 502

    @app.route("/api/client/license/activate", methods=["POST"])
    def api_client_license_activate():
        data = request.get_json(silent=True) or {}
        key = (data.get("license") or "").strip()
        if not key:
            return jsonify({"success": False, "error": "License 不能为空"}), 400
        binding_type = "machine" if is_client_mode() else "instance"
        binding_id = get_machine_id() if is_client_mode() else get_instance_id()
        result = license_manager.activate_license_key(key, binding_type, binding_id)
        if not result.get("valid"):
            return jsonify({"success": False, "error": result.get("message")}), 400
        info = result.get("info")
        if info and info.license_id:
            report_license_activation(info.license_id, binding_type, binding_id)
        return jsonify({"success": True, "message": result.get("message")})

    @app.route("/api/instance/info", methods=["GET"])
    def api_instance_info():
        db = db_factory()
        return jsonify(
            {
                "success": True,
                "instance_id": get_instance_id(),
                "workspace_id": db.get_default_workspace_id(),
            }
        )

    @app.route("/api/workspace", methods=["GET"])
    @login_required
    def api_get_workspace():
        db = db_factory()
        wid = db.get_user_workspace_id(current_user.id)
        ws = db.get_workspace(wid) if wid else None
        return jsonify({"success": True, "workspace": ws, "workspace_id": wid})

    @app.route("/api/client/payment-link", methods=["POST"])
    @login_required
    def api_client_payment_link():
        """已登录用户获取跳转官网支付的链接。"""
        data = request.get_json(silent=True) or {}
        plan = (data.get("plan") or data.get("plan_type") or "").strip()
        db = db_factory()
        user_data = db.get_user_by_id(current_user.id) or {}
        team_url = get_team_server_url() if is_client_mode() else ""
        url = build_website_payment_url(
            current_user.id,
            current_user.username,
            email=user_data.get("email") or "",
            team_server_url=team_url,
            plan=plan,
        )
        if not url:
            return jsonify({"success": False, "error": "未配置官网地址 WEBSITE_URL"}), 503
        return jsonify({"success": True, "url": url, "website_url": get_website_url()})


def guard_billing_route():
    if hide_billing_ui():
        return redirect(url_for("index"))
    return None


def init_server_instance(db_factory: Callable[[], Any]) -> None:
    if is_server_mode():
        db = db_factory()
        db.set_instance_setting("instance_id", get_instance_id())


def start_background_workers() -> None:
    report_current_license_activation()
    if is_client_mode():
        url = get_team_server_url()
        if url and is_setup_complete():
            start_client_worker(url)


def wire_internal_runner(app, run_case_fn) -> None:
    from execution_remote import register_internal_runner, set_run_case_handler

    def _runner(case_id: int, user_id: int):
        from flask import g

        g.force_local_run = True
        try:
            return run_case_fn(case_id, user_id)
        finally:
            g.force_local_run = False

    register_internal_runner(app, _runner)
    set_run_case_handler(_runner)


def patch_run_case_for_server(db, case_id: int, user_id: int):
    from execution_remote import create_server_execution_job

    return create_server_execution_job(db, case_id, user_id)
