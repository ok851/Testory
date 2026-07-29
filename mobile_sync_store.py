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


def normalize_device_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """DB/API 步骤 → 手机可执行 IR：mobile_spec 字符串展开，IR 字段上提到顶层。"""
    out = dict(step)
    ms = out.get("mobile_spec")
    if isinstance(ms, str) and ms.strip():
        try:
            ms = json.loads(ms)
        except Exception:
            ms = {}
    if not isinstance(ms, dict):
        ms = {}
    for k in (
        "assert_text",
        "wait_duration_ms",
        "pre_wait_ms",
        "max_retries",
        "optional",
        "assert_type",
        "save_as",
        "key_code",
        "repeat_max",
        "until_assert_text",
        "captcha_hint",
        "captcha_fallback",
        "roi",
        "scroll_amount",
        "swipe_direction",
    ):
        if ms.get(k) is not None and out.get(k) is None:
            out[k] = ms.get(k)
    out["mobile_spec"] = ms
    return out


def normalize_device_steps(steps: Any) -> List[Dict[str, Any]]:
    if not isinstance(steps, list):
        return []
    return [normalize_device_step(s) for s in steps if isinstance(s, dict)]


def case_bundle(db: Any, case_id: int, user_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    case_row = db.get_test_case_v2(case_id)
    if not case_row:
        return None, "用例不存在"
    pid = case_row.get("project_id")
    if pid and not db.check_project_access(user_id, int(pid), "viewer"):
        return None, "无权限"
    steps = db.get_case_steps(case_id, page=1, page_size=500)
    return {"case": case_row, "steps": normalize_device_steps(steps)}, None


def enqueue_run_job(
    *,
    case_id: int,
    steps: List[Dict[str, Any]],
    user_id: int,
    device_id: str = "",
    source: str = "pc",
    job_kind: str = "run_steps",
    job_meta: Optional[Dict[str, Any]] = None,
) -> str:
    job_id = secrets.token_hex(12)
    with _LOCK:
        _RUN_JOBS[job_id] = {
            "job_id": job_id,
            "case_id": case_id,
            "steps": normalize_device_steps(steps),
            "user_id": user_id,
            "device_id": device_id,
            "source": source,
            "job_kind": (job_kind or "run_steps").strip() or "run_steps",
            "job_meta": dict(job_meta or {}),
            "status": "pending",
            "created_at": time.time(),
        }
        _RUN_EVENTS[job_id] = []
    return job_id


def pop_pending_run_for_device(
    device_id: str,
    *,
    job_kind: str = "",
) -> Optional[Dict[str, Any]]:
    """取出一条 pending job 并标为 running。

    job_kind 非空时只匹配该类型（避免 APK 取码轮询误吞 run_steps 任务）。
    """
    device_id = (device_id or "").strip()
    want_kind = (job_kind or "").strip().lower()
    with _LOCK:
        for job_id, job in list(_RUN_JOBS.items()):
            if job.get("status") != "pending":
                continue
            target = (job.get("device_id") or "").strip()
            if target and device_id and target != device_id:
                continue
            if want_kind:
                kind = str(job.get("job_kind") or "run_steps").strip().lower()
                if kind != want_kind:
                    continue
            job["status"] = "running"
            return dict(job)
    return None


def requeue_run_job(job_id: str) -> bool:
    """将误取的 running job 退回 pending（未被执行时使用）。"""
    with _LOCK:
        job = _RUN_JOBS.get(job_id)
        if not job:
            return False
        if str(job.get("status") or "") != "running":
            return False
        job["status"] = "pending"
        job.pop("finished_at", None)
        return True


def append_run_events(job_id: str, payload: Dict[str, Any]) -> bool:
    with _LOCK:
        job = _RUN_JOBS.get(job_id)
        if not job:
            return False
        _RUN_EVENTS.setdefault(job_id, []).append(payload)
        status = (payload.get("status") or "").strip().lower()
        err_code = str(payload.get("error_code") or "").strip().upper()
        # 本机忙：退回 pending，勿终态失败（Agent await 可继续等到下次 poll）
        if status == "busy" or err_code == "MOBILE_BUSY":
            if str(job.get("status") or "") == "running":
                job["status"] = "pending"
                job.pop("finished_at", None)
            return True
        if status in ("success", "error", "failed", "cancelled", "ok"):
            job["status"] = "success" if status in ("success", "ok") else (
                "cancelled" if status == "cancelled" else "error"
            )
            job["finished_at"] = time.time()
            job["result_payload"] = dict(payload)
        elif payload.get("status"):
            job["status"] = payload.get("status")
        return True


def get_run_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _RUN_JOBS.get(job_id)
        return dict(job) if job else None


def wait_for_run_job(
    job_id: str,
    *,
    timeout_sec: float = 600.0,
    poll_interval_sec: float = 1.0,
) -> Dict[str, Any]:
    """阻塞等待手机本机跑完并上报事件。返回 job 快照（含 result_payload）。"""
    deadline = time.time() + max(1.0, float(timeout_sec))
    terminal = {"success", "error", "failed", "cancelled"}
    while time.time() < deadline:
        job = get_run_job(job_id)
        if not job:
            return {
                "job_id": job_id,
                "status": "error",
                "error": "run job 不存在",
            }
        st = str(job.get("status") or "").strip().lower()
        if st in terminal:
            return job
        time.sleep(max(0.2, float(poll_interval_sec)))
    job = get_run_job(job_id) or {"job_id": job_id}
    job = dict(job)
    job["status"] = "error"
    job["error"] = job.get("error") or (
        f"等待手机本机执行超时（{int(timeout_sec)}s）；请在手机上完成该阶段后重试"
    )
    job["error_code"] = "MOBILE_DEVICE_AWAIT_TIMEOUT"
    return job


def _safe_llm_status_payload(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(profile, dict) or not profile:
        return {
            "ready": False,
            "provider": "",
            "model": "",
            "profile_id": "",
            "message": "PC 未绑定或未激活大模型，请在 PC 端 AI 配置中添加并激活",
        }
    provider = str(profile.get("provider") or profile.get("type") or "").strip()
    model = str(
        profile.get("model")
        or profile.get("model_name")
        or profile.get("default_model")
        or ""
    ).strip()
    pid = str(profile.get("id") or "").strip()
    ready = bool(provider or model)
    return {
        "ready": ready,
        "provider": provider,
        "model": model,
        "profile_id": pid,
        "message": "就绪" if ready else "配置不完整，请检查 PC 端模型绑定",
    }


def _mobile_ai_chitchat_reply(message: str) -> Optional[Dict[str, Any]]:
    """短问候不走完整用例生成，避免无意义的长 prompt + LLM 耗时。"""
    raw = (message or "").strip()
    if not raw or len(raw) > 24:
        return None
    normalized = raw.rstrip("？?！!~～。.! ").strip().lower()
    greetings = {
        "你是谁", "你好", "您好", "在吗", "谢谢", "谢谢你",
        "hi", "hello", "hey", "帮助", "help", "你能做什么", "怎么用",
    }
    if normalized not in greetings and raw.strip().rstrip("？?！!") not in greetings:
        return None
    return {
        "case_name": "",
        "description": (
            "我是 Testory 手机端 AI 助手：你描述测试场景，我通过 PC 已绑定的大模型生成步骤；"
            "录制与回放都在手机本机完成。请直接说要测什么，例如：打开设置并开启飞行模式。"
        ),
        "expected_result": "",
        "steps": [],
    }


def _normalize_phone_ai_action(action: str) -> str:
    a = (action or "tap").strip().lower()
    mapping = {
        "click": "tap",
        "input_text": "input",
        "type": "input",
        "open_app": "tap",
        "close_app": "back",
        "assert_text": "assert",
        "assert_element": "assert",
    }
    return mapping.get(a, a)


def _mobile_ai_free_chat(message: str, profile: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    """自由对话：不用「强制 JSON 用例」系统提示，避免慢且答非所问。"""
    from ai_local_inference import local_ai_service
    from ai_multi_provider import dispatch_chat_completion_messages

    messages = [
        {
            "role": "system",
            "content": (
                "你是 Testory 手机端测试助手。用简洁中文回答。"
                "用户当前在「对话」模式：不要输出 JSON，不要假装已经在手机上点开了应用。"
                "若用户想生成可回放步骤，提示切换到「生成用例」模式后再描述场景。"
                "可简要说明建议步骤，但标明需手动切换模式才会生成用例。"
            ),
        },
        {"role": "user", "content": message},
    ]
    raw = dispatch_chat_completion_messages(
        messages,
        None,
        profile,
        local_ai_service,
        temperature=0.4,
        timeout=min(90, int(__import__("os").environ.get("LOCAL_LLM_TIMEOUT", "240") or 240)),
    )
    text = ""
    if isinstance(raw, dict):
        text = str(raw.get("content") or raw.get("text") or "").strip()
        if not text:
            # OpenAI-compat shape
            try:
                choices = raw.get("choices") or []
                if choices:
                    msg = (choices[0] or {}).get("message") or {}
                    text = str(msg.get("content") or "").strip()
            except Exception:
                pass
    elif isinstance(raw, str):
        text = raw.strip()
    if not text:
        text = "（模型未返回文本）请检查 PC 端 custom_openai 配置，或切换到「生成用例」模式重试。"
    return {
        "success": True,
        "case_name": "",
        "description": text[:4000],
        "expected_result": "",
        "steps": [],
        "mode": "chat",
        "ai_status": status,
    }


def list_accessible_projects(db: Any, user_id: int) -> List[Dict[str, Any]]:
    projects = db.get_user_projects(user_id) or []
    out: List[Dict[str, Any]] = []
    for p in projects:
        pid = p.get("id")
        if not pid:
            continue
        out.append({"id": int(pid), "name": p.get("name") or f"项目 #{pid}"})
    return out


def _merge_step_ir_into_mobile_spec(raw: Dict[str, Any]) -> Dict[str, Any]:
    ms = raw.get("mobile_spec")
    if isinstance(ms, str) and ms.strip():
        try:
            ms = json.loads(ms)
        except Exception:
            ms = {}
    if not isinstance(ms, dict):
        ms = {}
    for k in (
        "assert_text",
        "wait_duration_ms",
        "pre_wait_ms",
        "max_retries",
        "optional",
        "assert_type",
        "save_as",
        "key_code",
        "repeat_max",
        "until_assert_text",
        "captcha_hint",
        "captcha_fallback",
        "roi",
        "scroll_amount",
        "swipe_direction",
    ):
        if raw.get(k) is not None and k not in ms:
            ms[k] = raw.get(k)
    return ms


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
        ms = _merge_step_ir_into_mobile_spec(raw)
        db.create_test_step(
            case_id=case_id,
            step_order=int(raw.get("step_order") or next_order),
            action=(raw.get("action") or "tap").strip(),
            selector_type=(raw.get("selector_type") or raw.get("strategy") or "").strip(),
            selector_value=(raw.get("selector_value") or "").strip(),
            input_value=(raw.get("input_value") or "").strip(),
            description=(raw.get("description") or "").strip(),
            automation_layer="android",
            mobile_spec=json.dumps(ms, ensure_ascii=False),
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
            ms = _merge_step_ir_into_mobile_spec(raw)
            db.create_test_step(
                case_id=case_id,
                step_order=int(raw.get("step_order") or next_order),
                action=(raw.get("action") or "tap").strip(),
                selector_type=(raw.get("selector_type") or raw.get("strategy") or "").strip(),
                selector_value=(raw.get("selector_value") or "").strip(),
                input_value=(raw.get("input_value") or "").strip(),
                description=(raw.get("description") or "").strip(),
                automation_layer="android",
                mobile_spec=json.dumps(ms, ensure_ascii=False),
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
        job_kind = (request.args.get("job_kind") or "").strip()
        job = pop_pending_run_for_device(
            meta.get("device_id") or "",
            job_kind=job_kind,
        )
        if not job:
            return jsonify({"success": True, "has_job": False})
        return jsonify({
            "success": True,
            "has_job": True,
            "job_id": job["job_id"],
            "case_id": job["case_id"],
            "steps": normalize_device_steps(job.get("steps") or []),
            "job_kind": job.get("job_kind") or "run_steps",
            "job_meta": job.get("job_meta") or {},
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
        st = str(body.get("status") or "").strip().lower()
        err_code = str(body.get("error_code") or "").strip().upper()
        if st != "busy" and err_code != "MOBILE_BUSY":
            _persist_run_history(job_id, body, int(meta["user_id"]))
        return jsonify({"success": True})

    @app.route("/api/mobile/sync/ai/status", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_ai_status():
        meta, err = resolve_device_token()
        if err:
            return err
        try:
            from ai_multi_provider import get_active_llm_profile

            profile = get_active_llm_profile()
        except Exception:
            profile = None
        payload = _safe_llm_status_payload(profile)
        return jsonify({
            "success": True,
            "connected": True,
            **payload,
        })

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
        # chat=自由对话（默认）；generate=生成可回放步骤（用户显式选择）
        mode = (body.get("mode") or body.get("intent") or "chat").strip().lower()
        if mode in ("case", "steps", "plan", "generate_case"):
            mode = "generate"
        if mode not in ("chat", "generate"):
            mode = "chat"

        user_id = int(meta["user_id"])
        user_data = Database().get_user_by_id(user_id)
        project_name = (user_data.get("project_name") or user_data.get("username") or "") if user_data else ""

        try:
            from ai_multi_provider import get_active_llm_profile

            profile = get_active_llm_profile()
        except Exception:
            profile = None
        status = _safe_llm_status_payload(profile)
        if not status.get("ready"):
            return jsonify({
                "success": False,
                "error": status.get("message") or "PC 未绑定大模型",
                "ai_status": status,
            }), 400

        # 寒暄：本地即时回复
        chitchat = _mobile_ai_chitchat_reply(user_message)
        if chitchat is not None:
            return jsonify({
                "success": True,
                **chitchat,
                "mode": mode,
                "ai_status": status,
            })

        # 对话模式：短回复，不强制生成用例 JSON
        if mode == "chat":
            try:
                return jsonify(_mobile_ai_free_chat(user_message, profile, status))
            except ValueError as e:
                return jsonify({"success": False, "error": str(e), "ai_status": status}), 500
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception("移动端AI对话失败")
                return jsonify({
                    "success": False,
                    "error": f"AI对话失败: {str(e)}",
                    "ai_status": status,
                }), 500

        try:
            from ai_local_inference import local_ai_service

            result = local_ai_service.generate_case_and_steps(
                goal=user_message,
                project_name=project_name,
                profile=profile,
                platform_type="android",
            )
            steps = result.get("steps") or []
            android_steps = []
            for s in steps:
                android_steps.append({
                    "action": _normalize_phone_ai_action(s.get("action", "tap")),
                    "selector_type": s.get("selector_type", ""),
                    "selector_value": s.get("selector_value", ""),
                    "input_value": s.get("input_value", ""),
                    "description": s.get("description", ""),
                    "automation_layer": s.get("automation_layer", "android"),
                })
            meta_out = result.get("meta") if isinstance(result.get("meta"), dict) else {}
            return jsonify({
                "success": True,
                "mode": "generate",
                "case_name": result.get("case_name", "AI生成用例"),
                "description": result.get("description", ""),
                "expected_result": result.get("expected_result", ""),
                "steps": android_steps,
                "ai_status": {
                    **status,
                    "provider": meta_out.get("provider") or status.get("provider"),
                    "model": meta_out.get("model") or status.get("model"),
                },
            })
        except ValueError as e:
            return jsonify({"success": False, "error": str(e), "ai_status": status}), 500
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("移动端AI生成失败")
            return jsonify({
                "success": False,
                "error": f"AI生成失败: {str(e)}",
                "ai_status": status,
            }), 500

    @app.route("/api/mobile/sync/captcha/solve", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_captcha_solve():
        """手机截验证码 ROI → PC VLM → 返回结构化解法供本机手势。"""
        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        b64 = (body.get("image_base64") or "").strip()
        if not b64:
            return jsonify({"success": False, "error": "缺少 image_base64"}), 400
        import base64

        try:
            if "," in b64 and b64.startswith("data:"):
                b64 = b64.split(",", 1)[1]
            image_bytes = base64.b64decode(b64)
        except Exception:
            return jsonify({"success": False, "error": "image_base64 无效"}), 400
        hint = (body.get("captcha_hint") or body.get("hint") or "").strip()
        instruction = (body.get("instruction") or "").strip()
        try:
            from ai_vision_local import captcha_vision_solve

            raw = captcha_vision_solve(
                image_bytes,
                instruction=instruction,
                captcha_hint=hint,
            )
        except Exception as e:
            return jsonify({"success": False, "error": f"VLM 调用失败: {e}"}), 500
        solution = {}
        if raw:
            text = raw.strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    solution = parsed
            except Exception:
                solution = {"type": "unknown", "raw": text[:500]}
        return jsonify({
            "success": bool(solution) and solution.get("type") not in (None, "unknown"),
            "solution": solution,
            "raw": (raw or "")[:2000],
        })

    # Vision probe route removed — mobile mirror/vision feature retired


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
