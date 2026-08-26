# -*- coding: utf-8 -*-
"""
远程执行：server 模式排队任务，client 模式轮询领取并在本机执行后回传。
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from modules.core.deployment_config import (
    can_run_automation_locally,
    is_client_mode,
    is_server_mode,
    should_delegate_execution_to_clients,
)
from modules.core.instance_identity import get_machine_id, get_machine_name

_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()
_run_case_fn: Optional[Callable[[int, int], Dict[str, Any]]] = None


def set_run_case_handler(fn: Callable[[int, int], Dict[str, Any]]) -> None:
    """注入 app 内已有的用例执行函数：fn(case_id, user_id) -> result dict。"""
    global _run_case_fn
    _run_case_fn = fn


def create_server_execution_job(db, case_id: int, user_id: int) -> Dict[str, Any]:
    job_id = db.create_execution_job(case_id, user_id)
    return {
        "success": True,
        "remote": True,
        "job_id": job_id,
        "message": "已提交到客户端执行队列，请确保有在线的桌面客户端。",
    }


def register_routes(app, db_factory, login_required_decorator):
    """注册执行队列与客户端 worker API。"""
    from flask import request

    @app.route("/api/execution-jobs", methods=["GET"])
    @login_required_decorator
    def api_list_execution_jobs():
        from flask_login import current_user

        if current_user.role != "admin" and not is_server_mode():
            return {"success": False, "error": "无权访问"}, 403
        db = db_factory()
        status = (request.args.get("status") or "").strip() or None
        jobs = db.list_execution_jobs(limit=100, status=status)
        return {"success": True, "jobs": jobs}

    @app.route("/api/execution-jobs/<int:job_id>", methods=["GET"])
    @login_required_decorator
    def api_get_execution_job(job_id: int):
        db = db_factory()
        job = db.get_execution_job(job_id)
        if not job:
            return {"success": False, "error": "任务不存在"}, 404
        return {"success": True, "job": job}

    @app.route("/api/execution-jobs/claim", methods=["POST"])
    def api_claim_execution_job():
        """桌面客户端 worker 领取 pending 任务（无需 session，需本机网络可达）。"""
        if not is_server_mode() and not can_run_automation_locally():
            return {"success": False, "error": "当前部署模式不支持领取任务"}, 403
        data = request.get_json(silent=True) or {}
        machine_id = (data.get("machine_id") or get_machine_id()).strip()
        machine_name = (data.get("machine_name") or get_machine_name()).strip()
        db = db_factory()
        job = db.claim_execution_job(machine_id, machine_name)
        if not job:
            return {"success": True, "job": None}
        case = db.get_test_case_v2(job["case_id"])
        steps = db.get_case_steps(job["case_id"])
        return {
            "success": True,
            "job": job,
            "case": case,
            "steps": steps,
        }

    @app.route("/api/execution-jobs/<int:job_id>/complete", methods=["POST"])
    def api_complete_execution_job(job_id: int):
        data = request.get_json(silent=True) or {}
        db = db_factory()
        ok = db.complete_execution_job(
            job_id,
            status=(data.get("status") or "error").strip(),
            run_history_id=data.get("run_history_id"),
            error=(data.get("error") or "").strip(),
            result_json=json.dumps(data.get("result") or {}, ensure_ascii=False),
        )
        if not ok:
            return {"success": False, "error": "更新任务失败"}, 404
        return {"success": True}

    @app.route("/api/execution-jobs/report-run", methods=["POST"])
    @login_required_decorator
    def api_report_run_from_client():
        """客户端本地执行完成后，将 run_history 写入团队服务器。"""
        from flask_login import current_user

        data = request.get_json(silent=True) or {}
        case_id = int(data.get("case_id") or 0)
        if not case_id:
            return {"success": False, "error": "case_id 必填"}, 400
        db = db_factory()
        run_id = db.create_run_history(
            case_id,
            data.get("status") or "error",
            float(data.get("duration") or 0),
            data.get("error") or "",
            data.get("extracted_text") or "",
            data.get("expected_text") or "",
        )
        screenshots = data.get("screenshots") or []
        if screenshots:
            try:
                import sqlite3

                conn = sqlite3.connect(db.db_path)
                conn.execute(
                    "UPDATE run_history SET screenshots = ? WHERE id = ?",
                    (json.dumps(screenshots), run_id),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        for sr in data.get("step_results") or []:
            db.create_step_result(
                run_id,
                sr.get("step_id"),
                sr.get("step_order", 0),
                sr.get("action", ""),
                sr.get("selector_value", ""),
                sr.get("input_value", ""),
                sr.get("description", ""),
                sr.get("status", "error"),
                sr.get("error", ""),
                sr.get("screenshot", ""),
                float(sr.get("duration") or 0),
            )
        db.increment_execution_count(current_user.id)
        return {"success": True, "run_history_id": run_id}

    @app.route("/api/client-nodes", methods=["GET"])
    @login_required_decorator
    def api_list_client_nodes():
        from flask_login import current_user

        if current_user.role != "admin":
            return {"success": False, "error": "仅管理员可查看"}, 403
        db = db_factory()
        return {"success": True, "nodes": db.list_client_nodes()}

    @app.route("/api/client-nodes/heartbeat", methods=["POST"])
    def api_client_node_heartbeat():
        data = request.get_json(silent=True) or {}
        machine_id = (data.get("machine_id") or get_machine_id()).strip()
        db = db_factory()
        node_id = db.upsert_client_node(
            machine_id,
            (data.get("machine_name") or get_machine_name()).strip(),
            user_id=data.get("user_id"),
            status="online",
            capabilities=data.get("capabilities"),
        )
        return {"success": True, "node_id": node_id}


def register_internal_runner(app, run_case_callable) -> None:
    """注册 worker 本机执行入口（绕过 server 入队）。"""
    import os
    from flask import jsonify, request

    from modules.core.deployment_config import should_delegate_execution_to_clients

    @app.route("/api/internal/run-case", methods=["POST"])
    def api_internal_run_case():
        secret = (os.environ.get("EXECUTION_WORKER_SECRET") or "uat-local-worker").strip()
        if (request.headers.get("X-Execution-Worker-Secret") or "") != secret:
            return jsonify({"success": False, "error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        case_id = int(data.get("case_id") or 0)
        user_id = int(data.get("user_id") or 0)
        if not case_id:
            return jsonify({"success": False, "error": "case_id required"}), 400
        if should_delegate_execution_to_clients():
            return jsonify({"success": False, "error": "server mode cannot run locally"}), 400
        result = run_case_callable(case_id, user_id)
        return jsonify(result)


def _worker_loop(server_base: str) -> None:
    import urllib.error
    import urllib.request

    while not _worker_stop.is_set():
        try:
            payload = json.dumps(
                {"machine_id": get_machine_id(), "machine_name": get_machine_name()},
                ensure_ascii=False,
            ).encode("utf-8")
            url = server_base.rstrip("/") + "/api/execution-jobs/claim"
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            job = (data or {}).get("job")
            if not job:
                _worker_stop.wait(2.0)
                continue
            case_id = int(job["case_id"])
            user_id = int(job["user_id"])
            job_id = int(job["id"])
            if _run_case_fn:
                result = _run_case_fn(case_id, user_id)
            else:
                from modules.execution.case_runner_local import run_case_on_local_app

                result = run_case_on_local_app(case_id, user_id)
            complete_url = server_base.rstrip("/") + f"/api/execution-jobs/{job_id}/complete"
            complete_body = json.dumps(
                {
                    "status": result.get("status") or ("success" if result.get("success") else "error"),
                    "run_history_id": result.get("run_history_id"),
                    "error": result.get("error") or "",
                    "result": result,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            creq = urllib.request.Request(
                complete_url,
                data=complete_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(creq, timeout=30)
        except Exception:
            _worker_stop.wait(3.0)


def start_client_worker(server_base: str) -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop,
        args=(server_base,),
        name="uat-execution-worker",
        daemon=True,
    )
    _worker_thread.start()


def stop_client_worker() -> None:
    _worker_stop.set()


# 客户端模式下需本地执行、不代理到团队服务器的 API 前缀
LOCAL_API_PREFIXES = (
    "/api/health",
    "/api/client/",
    "/api/execution-jobs/claim",
    "/api/execution-jobs/report-run",
    "/api/client-nodes/",
    "/api/internal/",
    "/api/playwright/",
    "/api/browser/",
    "/api/desktop/",
    "/api/web-capture/",
    "/api/element-picker/",
    "/api/element_picker/",
    "/api/web-dom-picker/",
    "/api/navigate",
    "/api/scroll",
    "/api/click",
    "/api/hover",
    "/api/double_click",
    "/api/right_click",
    "/api/wait_",
    "/api/extract_",
    "/api/page_",
    "/api/analyze_content",
    "/api/screenshot",
    "/api/enable_element",
    "/api/disable_element",
    "/api/get_selected_element",
    "/api/license/",
)


def is_local_client_api(path: str) -> bool:
    if path.startswith("/api/cases/") and path.endswith("/run"):
        return True
    if path.startswith("/api/cases/current-run"):
        return True
    for prefix in LOCAL_API_PREFIXES:
        if path.startswith(prefix):
            return True
    return False
