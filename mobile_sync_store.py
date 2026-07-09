# -*- coding: utf-8 -*-
"""PC ↔ 手机 Sync：配对 token、用例 bundle、运行 job 队列。"""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import jsonify, request

_LOCK = threading.RLock()
_PAIR_CODES: Dict[str, Dict[str, Any]] = {}
_DEVICE_TOKENS: Dict[str, Dict[str, Any]] = {}
_RUN_JOBS: Dict[str, Dict[str, Any]] = {}
_RUN_EVENTS: Dict[str, List[Dict[str, Any]]] = {}

# 原缺陷：600s 过长且与 UI 桩 API 断链导致「无效或过期」误报；延长至用户可接受窗口并统一注册。
_PAIR_TTL_SEC = 120
_PAIR_RETRY_WINDOW_SEC = 30
_STORE_PATH: Optional[Path] = None


def _store_file() -> Path:
    global _STORE_PATH
    if _STORE_PATH is None:
        try:
            from install_paths import uat_data_dir

            base = uat_data_dir()
        except Exception:
            base = Path(__file__).resolve().parent
        _STORE_PATH = Path(base) / "mobile_sync" / "tokens.json"
    return _STORE_PATH


def _load_persisted() -> None:
    path = _store_file()
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        tokens = raw.get("device_tokens") or {}
        if isinstance(tokens, dict):
            with _LOCK:
                _DEVICE_TOKENS.update(tokens)
    except Exception:
        pass


def _save_persisted() -> None:
    path = _store_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        payload = {"device_tokens": _DEVICE_TOKENS}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


_load_persisted()


def create_pair_code(user_id: int, tenant_id: Optional[int] = None) -> Dict[str, Any]:
    """生成并注册配对码；返回 code 与过期时间供 UI 倒计时。"""
    code = f"{secrets.randbelow(900000) + 100000:06d}"
    now = time.time()
    with _LOCK:
        _PAIR_CODES[code] = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "created_at": now,
            "used": False,
        }
    return {
        "pair_code": code,
        "created_at": now,
        "expires_at": now + _PAIR_TTL_SEC,
        "expires_in": _PAIR_TTL_SEC,
    }


def confirm_pair(code: str, device_id: str) -> Tuple[bool, str, Optional[str]]:
    code = (code or "").strip()
    device_id = (device_id or "").strip() or "device"
    now = time.time()
    with _LOCK:
        entry = _PAIR_CODES.get(code)
        if not entry:
            return False, "配对码无效或已过期", None
        created = float(entry.get("created_at") or 0)
        if now - created > _PAIR_TTL_SEC:
            _PAIR_CODES.pop(code, None)
            return False, "配对码已过期", None
        if entry.get("used"):
            paired_at = float(entry.get("paired_at") or 0)
            if (
                entry.get("device_id") == device_id
                and paired_at
                and now - paired_at <= _PAIR_RETRY_WINDOW_SEC
                and entry.get("device_token")
            ):
                return True, "ok", str(entry["device_token"])
            return False, "配对码无效或已过期", None
        token = secrets.token_urlsafe(32)
        entry["used"] = True
        entry["device_id"] = device_id
        entry["paired_at"] = now
        entry["device_token"] = token
        _DEVICE_TOKENS[token] = {
            "user_id": entry["user_id"],
            "tenant_id": entry.get("tenant_id"),
            "device_id": device_id,
            "paired_at": now,
        }
    _save_persisted()
    return True, "ok", token


def pair_code_payload(user_id: int, tenant_id: Optional[int] = None) -> Dict[str, Any]:
    """供 Flask 路由返回的标准配对码 JSON。"""
    info = create_pair_code(user_id, tenant_id)
    return {"success": True, **info}


def resolve_device_token() -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
    token = (request.headers.get("X-Mobile-Device-Token") or "").strip()
    if not token:
        return None, (jsonify({"success": False, "error": "缺少设备 token"}), 401)
    with _LOCK:
        meta = _DEVICE_TOKENS.get(token)
    if not meta:
        return None, (jsonify({"success": False, "error": "设备 token 无效"}), 401)
    return dict(meta), None


def list_accessible_cases(db: Any, user_id: int) -> List[Dict[str, Any]]:
    projects = db.get_user_projects(user_id) or []
    out: List[Dict[str, Any]] = []
    for p in projects:
        pid = p.get("id")
        if not pid:
            continue
        for c in db.get_project_cases(int(pid)) or []:
            if (c.get("case_type") or "ui") == "api":
                continue
            out.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "project_id": pid,
                "project_name": p.get("name"),
            })
    return out


def case_bundle(db: Any, case_id: int, user_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    case_row = db.get_test_case_v2(case_id)
    if not case_row:
        return None, "用例不存在"
    pid = case_row.get("project_id")
    if pid and not db.check_project_access(user_id, int(pid), "viewer"):
        return None, "无权限"
    steps = db.get_case_steps(case_id, page=1, page_size=500)
    return {"case": case_row, "steps": steps}, None


def enqueue_run_job(
    *,
    case_id: int,
    steps: List[Dict[str, Any]],
    user_id: int,
    device_id: str = "",
    source: str = "pc",
) -> str:
    job_id = secrets.token_hex(12)
    with _LOCK:
        _RUN_JOBS[job_id] = {
            "job_id": job_id,
            "case_id": case_id,
            "steps": steps,
            "user_id": user_id,
            "device_id": device_id,
            "source": source,
            "status": "pending",
            "created_at": time.time(),
        }
        _RUN_EVENTS[job_id] = []
    return job_id


def pop_pending_run_for_device(device_id: str) -> Optional[Dict[str, Any]]:
    device_id = (device_id or "").strip()
    with _LOCK:
        for job_id, job in list(_RUN_JOBS.items()):
            if job.get("status") != "pending":
                continue
            target = (job.get("device_id") or "").strip()
            if target and device_id and target != device_id:
                continue
            job["status"] = "running"
            return dict(job)
    return None


def append_run_events(job_id: str, payload: Dict[str, Any]) -> bool:
    with _LOCK:
        job = _RUN_JOBS.get(job_id)
        if not job:
            return False
        _RUN_EVENTS.setdefault(job_id, []).append(payload)
        job["status"] = payload.get("status") or job.get("status")
        job["finished_at"] = time.time()
    return True


def list_accessible_projects(db: Any, user_id: int) -> List[Dict[str, Any]]:
    projects = db.get_user_projects(user_id) or []
    out: List[Dict[str, Any]] = []
    for p in projects:
        pid = p.get("id")
        if not pid:
            continue
        out.append({"id": int(pid), "name": p.get("name") or f"项目 #{pid}"})
    return out


def push_case_to_pc(
    db: Any,
    user_id: int,
    *,
    project_id: int,
    name: str,
    steps: List[Dict[str, Any]],
    remote_case_id: Optional[int] = None,
    replace: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if project_id <= 0:
        return None, "缺少 project_id"
    if not db.check_project_access(user_id, int(project_id), "editor"):
        return None, "无项目编辑权限"
    if not isinstance(steps, list) or not steps:
        return None, "steps 为空"
    case_name = (name or "移动端用例").strip() or "移动端用例"
    if remote_case_id and int(remote_case_id) > 0:
        bundle, emsg = case_bundle(db, int(remote_case_id), user_id)
        if emsg:
            return None, emsg
        case_id = int(remote_case_id)
        case_name = (bundle.get("case") or {}).get("name") or case_name
        if replace:
            db.delete_case_steps(case_id)
        existing = db.get_case_steps(case_id, page=1, page_size=500)
        next_order = 1 if replace else len(existing) + 1
    else:
        case_id = db.create_test_case_v2(
            int(project_id),
            case_name,
            platform="android",
            case_type="ui",
        )
        next_order = 1
    created = 0
    for raw in steps:
        if not isinstance(raw, dict):
            continue
        db.create_test_step(
            case_id=case_id,
            step_order=int(raw.get("step_order") or next_order),
            action=(raw.get("action") or "tap").strip(),
            selector_type=(raw.get("selector_type") or raw.get("strategy") or "").strip(),
            selector_value=(raw.get("selector_value") or "").strip(),
            input_value=(raw.get("input_value") or "").strip(),
            description=(raw.get("description") or "").strip(),
            automation_layer="android",
            mobile_spec=json.dumps(raw.get("mobile_spec") or {}, ensure_ascii=False)
            if isinstance(raw.get("mobile_spec"), dict)
            else (raw.get("mobile_spec") or ""),
        )
        next_order += 1
        created += 1
    return {
        "case_id": case_id,
        "name": case_name,
        "project_id": int(project_id),
        "project_name": _project_name(db, int(project_id)),
        "step_count": created,
    }, None


def _project_name(db: Any, project_id: int) -> str:
    try:
        row = db.get_project(project_id)
        if row and row.get("name"):
            return str(row["name"])
    except Exception:
        pass
    return f"项目 #{project_id}"


def register_sync_routes(app, *, api_error_handler, login_required, role_required=None):
    """注册 /api/mobile/sync/* 与设备 token 鉴权的 probe 路由。"""

    def _roles(*args):
        if role_required is None:
            return lambda f: f
        return role_required(*args)

    # ── 健康检查（无认证，供移动端探测服务器可达性）──
    @app.route("/api/ping", methods=["GET"])
    def api_ping():
        return jsonify({"success": True, "message": "pong", "server": "testory"})

    @app.route("/api/mobile/sync/pair/init", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_sync_pair_init():
        from database import Database
        from flask_login import current_user

        db = Database()
        tid = db.get_user_tenant_id(current_user.id)
        return jsonify(pair_code_payload(current_user.id, tid))

    @app.route("/api/mobile/sync/pair/confirm", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_pair_confirm():
        body = request.get_json(silent=True) or {}
        ok, msg, token = confirm_pair(body.get("code") or body.get("pair_code"), body.get("device_id"))
        if not ok:
            return jsonify({"success": False, "error": msg}), 400
        return jsonify({"success": True, "device_token": token})

    @app.route("/api/mobile/sync/cases", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_cases():
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        db = Database()
        cases = list_accessible_cases(db, int(meta["user_id"]))
        return jsonify({"success": True, "cases": cases})

    @app.route("/api/mobile/sync/projects", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_projects():
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        db = Database()
        projects = list_accessible_projects(db, int(meta["user_id"]))
        return jsonify({"success": True, "projects": projects})

    @app.route("/api/mobile/sync/cases/push", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_push_case():
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        body = request.get_json(silent=True) or {}
        project_id = int(body.get("project_id") or 0)
        name = (body.get("name") or "").strip()
        steps_in = body.get("steps") or []
        remote_case_id = body.get("remote_case_id")
        replace = body.get("replace", True) is not False
        db = Database()
        result, emsg = push_case_to_pc(
            db,
            int(meta["user_id"]),
            project_id=project_id,
            name=name,
            steps=steps_in if isinstance(steps_in, list) else [],
            remote_case_id=int(remote_case_id) if remote_case_id else None,
            replace=replace,
        )
        if emsg:
            return jsonify({"success": False, "error": emsg}), 400
        return jsonify({"success": True, **result})

    @app.route("/api/mobile/sync/cases/<int:case_id>/bundle", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_case_bundle(case_id: int):
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        db = Database()
        bundle, emsg = case_bundle(db, case_id, int(meta["user_id"]))
        if emsg:
            return jsonify({"success": False, "error": emsg}), 404
        return jsonify({"success": True, **bundle})

    @app.route("/api/mobile/sync/cases/<int:case_id>/steps", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_upload_steps(case_id: int):
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        body = request.get_json(silent=True) or {}
        steps_in = body.get("steps") or []
        if not isinstance(steps_in, list) or not steps_in:
            return jsonify({"success": False, "error": "steps 为空"}), 400
        db = Database()
        bundle, emsg = case_bundle(db, case_id, int(meta["user_id"]))
        if emsg:
            return jsonify({"success": False, "error": emsg}), 404
        existing = db.get_case_steps(case_id, page=1, page_size=500)
        next_order = len(existing) + 1
        created = 0
        for raw in steps_in:
            if not isinstance(raw, dict):
                continue
            db.create_test_step(
                case_id=case_id,
                step_order=int(raw.get("step_order") or next_order),
                action=(raw.get("action") or "tap").strip(),
                selector_type=(raw.get("selector_type") or raw.get("strategy") or "").strip(),
                selector_value=(raw.get("selector_value") or "").strip(),
                input_value=(raw.get("input_value") or "").strip(),
                description=(raw.get("description") or "").strip(),
                automation_layer="android",
                mobile_spec=json.dumps(raw.get("mobile_spec") or {}, ensure_ascii=False)
                if isinstance(raw.get("mobile_spec"), dict)
                else (raw.get("mobile_spec") or ""),
            )
            next_order += 1
            created += 1
        return jsonify({"success": True, "created": created})

    @app.route("/api/mobile/sync/run", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_sync_run_enqueue():
        from database import Database
        from app import load_case_and_steps

        body = request.get_json(silent=True) or {}
        case_id = int(body.get("case_id") or 0)
        if case_id <= 0:
            return jsonify({"success": False, "error": "缺少 case_id"}), 400
        db = Database()
        case, steps = load_case_and_steps(case_id, db)
        if not case:
            return jsonify({"success": False, "error": "用例不存在"}), 404
        if not steps:
            return jsonify({"success": False, "error": "无步骤"}), 400
        exec_steps = []
        for step in steps:
            s = dict(step)
            s["selector_value"] = db.resolve_variables(
                step.get("selector_value", ""),
                project_id=case.get("project_id"),
                case_id=case_id,
            )
            s["input_value"] = db.resolve_variables(
                step.get("input_value", ""),
                project_id=case.get("project_id"),
                case_id=case_id,
            )
            exec_steps.append(s)
        from flask_login import current_user

        job_id = enqueue_run_job(
            case_id=case_id,
            steps=exec_steps,
            user_id=current_user.id,
            device_id=(body.get("device_id") or "").strip(),
            source="pc",
        )
        return jsonify({"success": True, "job_id": job_id, "step_count": len(exec_steps)})

    @app.route("/api/mobile/sync/run/pending", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_run_pending():
        meta, err = resolve_device_token()
        if err:
            return err
        job = pop_pending_run_for_device(meta.get("device_id") or "")
        if not job:
            return jsonify({"success": True, "has_job": False})
        return jsonify({
            "success": True,
            "has_job": True,
            "job_id": job["job_id"],
            "case_id": job["case_id"],
            "steps": job.get("steps") or [],
        })

    @app.route("/api/mobile/sync/cases/pull-batch", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_cases_pull_batch():
        from database import Database

        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        case_ids = body.get("case_ids") or []
        if not case_ids:
            return jsonify({"success": False, "error": "请选择要拉取的用例"}), 400

        db = Database()
        bundles = []
        for cid in case_ids:
            try:
                bid = int(cid) if str(cid).isdigit() else None
                if bid is None:
                    continue
                cdata, _ = case_bundle(db, bid, int(meta["user_id"]))
                if cdata:
                    bundles.append(cdata)
            except Exception:
                continue
        return jsonify({"success": True, "bundles": bundles})

    @app.route("/api/mobile/sync/run/events", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_run_events_post():
        from database import Database

        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        case_id = body.get("case_id", 0)
        case_name = body.get("case_name", "")
        status = body.get("status", "success")
        error = body.get("error", "")
        device_model = body.get("device_model", "")
        android_version = body.get("android_version", "")
        device_name = body.get("device_name", "")
        results = body.get("results") or []
        total_steps = body.get("total_steps", 0)
        passed_steps = body.get("passed_steps", 0)
        duration_ms = body.get("duration_ms", 0)

        try:
            db = Database()
            run_id = db.create_run_history(
                case_id, status, 0, error,
                extracted_text=device_model,
                expected_text=android_version,
                test_type="android"
            )
            if isinstance(results, list):
                for i, r in enumerate(results):
                    if not isinstance(r, dict):
                        continue
                    s_status = "success" if r.get("success", True) else "error"
                    s_desc = r.get("stepDescription") or r.get("description") or ""
                    s_err = r.get("errorMessage") or r.get("error") or ""
                    db.create_step_result(
                        run_id, None,
                        r.get("stepIndex") or (i + 1),
                        r.get("action") or "",
                        s_desc,
                        android_version,
                        device_model,
                        s_status,
                        s_err,
                        device_name,
                        int(r.get("durationMs") or 0),
                    )
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("移动端运行记录保存失败")
            return jsonify({"success": False, "error": str(e)}), 500

        return jsonify({"success": True, "run_id": run_id})

    @app.route("/api/mobile/sync/run/<job_id>/events", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_run_events(job_id: str):
        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        if not append_run_events(job_id, body):
            return jsonify({"success": False, "error": "job 不存在"}), 404
        _persist_run_history(job_id, body, int(meta["user_id"]))
        return jsonify({"success": True})

    @app.route("/api/mobile/sync/ai/generate", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_ai_generate():
        from database import Database

        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        user_message = (body.get("message") or "").strip()
        if not user_message:
            return jsonify({"success": False, "error": "请输入测试需求描述"}), 400

        user_id = int(meta["user_id"])
        user_data = Database().get_user_by_id(user_id)
        project_name = (user_data.get("project_name") or user_data.get("username") or "") if user_data else ""

        try:
            from ai_local_inference import local_ai_service
            result = local_ai_service.generate_case_and_steps(
                goal=user_message,
                project_name=project_name,
                platform_type="android",
            )
            steps = result.get("steps") or []
            android_steps = []
            for s in steps:
                android_steps.append({
                    "action": s.get("action", "tap"),
                    "selector_type": s.get("selector_type", ""),
                    "selector_value": s.get("selector_value", ""),
                    "input_value": s.get("input_value", ""),
                    "description": s.get("description", ""),
                    "automation_layer": s.get("automation_layer", "android"),
                })
            return jsonify({
                "success": True,
                "case_name": result.get("case_name", "AI生成用例"),
                "description": result.get("description", ""),
                "expected_result": result.get("expected_result", ""),
                "steps": android_steps,
            })
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 500
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("移动端AI生成失败")
            return jsonify({"success": False, "error": f"AI生成失败: {str(e)}"}), 500

    # Vision probe route removed ? mobile mirror/vision feature retired


def _persist_run_history(job_id: str, payload: Dict[str, Any], user_id: int) -> None:
    """手机执行完成后写入 run_history（简化版）。"""
    with _LOCK:
        job = _RUN_JOBS.get(job_id) or {}
    case_id = int(job.get("case_id") or 0)
    if case_id <= 0:
        return
    try:
        from database import Database

        db = Database()
        status = "success" if (payload.get("status") or "") == "success" else "error"
        err = payload.get("error") or ""
        run_id = db.create_run_history(case_id, status, 0, err, "", "", test_type="android")
        results = payload.get("results") or []
        if isinstance(results, list):
            for i, r in enumerate(results):
                if not isinstance(r, dict):
                    continue
                db.create_step_result(
                    run_id,
                    None,
                    r.get("step_order") or (i + 1),
                    r.get("action") or "",
                    "",
                    "",
                    "",
                    r.get("status") or "success",
                    r.get("error") or "",
                    "",
                    0,
                )
    except Exception:
        pass
