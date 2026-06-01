import os
import sys

try:
    from pathlib import Path

    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent / ".env"
    try:
        from env_example_sync import sync_env_from_example

        sync_env_from_example(_env_path.parent)
    except Exception:
        pass
    if _env_path.is_file():
        load_dotenv(_env_path, encoding="utf-8-sig")
    else:
        load_dotenv(encoding="utf-8-sig")
except ImportError:
    pass

if sys.platform == "win32":
    try:
        from desktop_service_bootstrap import bootstrap_desktop_services

        bootstrap_desktop_services()
    except Exception:
        pass

from flask import Flask, render_template, request, jsonify, session, make_response, redirect, url_for, Response, stream_with_context
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import time
import shlex
import shutil
import secrets
import uuid
import json
import re
from typing import Any, Dict, List, Optional
import functools
import threading
import io
import tempfile
import subprocess
from database import Database
from time_utils import beijing_now_iso, utc_now_sqlite_str as utc_sql_str
from api_spec_pipeline import run_api_spec_pipeline
from execution_context import ExecutionContext
from embedded_browser_client import embedded_gateway_config, embedded_gateway_enabled, embedded_gateway_json
from batch_input_parse import parse_batch_input_lines
from playwright_automation import (
    automation,
    normalize_playwright_browser_name,
    force_reset_execution_state,
    parse_platform_scroll_input_value,
    scroll_event_to_platform_input_value,
    sync_analyze_page_content,
    sync_automation_session_usable,
    sync_browser_go_back,
    sync_browser_go_forward,
    sync_browser_keyboard_press,
    sync_browser_keyboard_type,
    sync_browser_mouse_click,
    sync_browser_mouse_wheel,
    sync_browser_reload,
    sync_click_element,
    sync_element_info_at_point,
    sync_get_interactive_page_snapshot,
    sync_get_accessibility_outline_text,
    sync_get_page_diagnostics,
    sync_get_viewport_size,
    sync_close_browser,
    sync_disable_element_selection,
    sync_double_click_element,
    sync_enable_element_selection,
    sync_enter_iframe,
    sync_execute_multiple_test_cases,
    sync_exit_iframe,
    sync_extract_element_data,
    sync_extract_element_json,
    sync_extract_element_text,
    sync_extract_json_from_selected_element,
    sync_fill_input,
    sync_get_all_links,
    sync_get_current_url,
    sync_get_element_count,
    sync_get_page_data,
    sync_get_page_elements,
    sync_get_page_text,
    sync_get_page_title,
    sync_get_selected_element,
    sync_hover_element,
    sync_execute_script_steps,
    sync_run_api_case_for_batch,
    sync_navigate_to,
    sync_right_click_element,
    sync_scroll_by_delta,
    sync_scroll_page,
    sync_start_browser,
    resolve_playwright_headless,
    sync_select_date,
    sync_select_option,
    sync_swipe_element,
    sync_take_screenshot,
    sync_take_screenshot_bytes,
    sync_verify_element,
    sync_wait_for_element_visible,
    sync_wait_for_page_stable,
    sync_wait_for_selector,
    sync_wait_for_timeout,
    worker,
    _url_assert_matches_pa,
)
from playwright_codegen_import import (
    enrich_steps_with_xpath_priority,
    parse_playwright_codegen_to_steps,
)
from selenium_ide_import import parse_selenium_ide_to_steps
from test_report import TestReportGenerator
from report_exporter import ReportExporter
from logger import uat_logger
from license_manager import license_manager, LicenseType
from deployment_hooks import (
    guard_billing_route,
    init_server_instance,
    patch_run_case_for_server,
    register_deployment_hooks,
)
from deployment_config import is_client_mode, should_delegate_execution_to_clients
from client_run_helpers import load_case_and_steps, sync_run_to_team_server
from cloud_llm_gateway import CloudLLMGateway
from ai_local_inference import local_ai_service
from ai_step_normalization import (
    ai_plan_steps_to_playwright_script_steps,
    apply_step_normalization_to_plan,
    dedupe_and_validate_ai_steps,
)
from ai_platform_audit import log_ai_plan_to_audit
import asyncio
import threading
import datetime
# `flask_migrate` 只用于数据库迁移；某些部署环境可能未安装，需保证应用可正常启动。
try:
    from flask_migrate import Migrate  # type: ignore
except ModuleNotFoundError:
    Migrate = None

# 数据驱动批量执行进度（内存态，按 run_id + 用户隔离；完成后首次拉取状态即清理）
_dataset_run_jobs: dict = {}
_dataset_run_lock = threading.Lock()
_case_run_jobs: dict = {}
_case_run_lock = threading.Lock()
_ai_model_cfg_lock = threading.Lock()
_login_fail_lock = threading.Lock()
_login_fail_timestamps: dict = {}

if sys.platform == "win32":
    try:
        from desktop_runtime import (
            desktop_runtime_available,
            desktop_runtime_unavailable_reason,
        )

        if desktop_runtime_available():
            uat_logger.info(
                "桌面视觉自动化依赖就绪 (opencv+mss, 解释器=%s)", sys.executable
            )
        else:
            uat_logger.warning(
                "桌面自动化不可用: %s",
                desktop_runtime_unavailable_reason() or "未知原因",
            )
    except Exception as _desk_boot_exc:
        uat_logger.debug("桌面依赖自检跳过: %s", _desk_boot_exc)


def _is_production_env() -> bool:
    return (
        os.environ.get("FLASK_ENV", "").strip().lower() == "production"
        or os.environ.get("APP_ENV", "").strip().lower() == "production"
    )


def _login_client_ip() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip()[:100] or (request.remote_addr or "")
    return request.remote_addr or ""


def _login_is_rate_limited(ip: str) -> tuple:
    max_n = int(os.environ.get("LOGIN_RATE_LIMIT_MAX", "40"))
    win = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SEC", "900"))
    if max_n <= 0:
        return False, 0
    now = time.time()
    with _login_fail_lock:
        lst = _login_fail_timestamps.get(ip, [])
        lst = [t for t in lst if now - t < win]
        if not lst:
            _login_fail_timestamps.pop(ip, None)
        else:
            _login_fail_timestamps[ip] = lst
        if len(lst) >= max_n:
            retry = int(win - (now - min(lst))) + 1
            return True, max(1, retry)
    return False, 0


def _login_record_failure(ip: str) -> None:
    win = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SEC", "900"))
    now = time.time()
    with _login_fail_lock:
        lst = _login_fail_timestamps.get(ip, [])
        lst = [t for t in lst if now - t < win]
        lst.append(now)
        _login_fail_timestamps[ip] = lst


def _login_clear_failures(ip: str) -> None:
    with _login_fail_lock:
        _login_fail_timestamps.pop(ip, None)


def _case_job_update(user_id: int, **kwargs):
    with _case_run_lock:
        if user_id in _case_run_jobs:
            _case_run_jobs[user_id].update(kwargs)


def _case_run_cancelled(user_id: int) -> bool:
    """当前用户是否已请求停止用例执行（与 /api/cases/current-run/stop 一致）。"""
    with _case_run_lock:
        job = _case_run_jobs.get(user_id)
        return bool(job and job.get('cancel_requested'))


def _env_flag_true(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _on_case_execution_failure(payload: dict) -> None:
    """执行线程内调用：可选本地向量记忆与自动缺陷（不依赖 Flask 上下文）。"""
    uid = payload.get("user_id")
    tid = payload.get("tenant_id")
    case_id = payload.get("case_id")
    case_name = (payload.get("case_name") or "").strip() or "未命名用例"
    project_id = payload.get("project_id")
    err = (payload.get("error") or "").strip()
    trigger = (payload.get("trigger") or "").strip() or "unknown"

    try:
        import ai_memory_store

        if uid is not None and ai_memory_store.memory_enabled():
            lines = [f"[{trigger}] 用例执行失败: {case_name}", f"case_id={case_id}"]
            if err:
                lines.append(f"error: {err[:3500]}")
            text = "\n".join(lines)
            meta = {
                "case_id": case_id,
                "project_id": project_id,
                "run_history_id": payload.get("run_history_id"),
                "trigger": trigger,
            }
            if payload.get("schedule_id") is not None:
                meta["schedule_id"] = payload.get("schedule_id")
            ai_memory_store.ingest(
                int(uid),
                "case_failure",
                text,
                tenant_id=tid if isinstance(tid, int) else None,
                meta=meta,
            )
    except Exception as exc:
        uat_logger.warning(f"[FAILURE_HOOK] 向量记忆写入跳过: {exc}")

    if not _env_flag_true("AUTO_DEFECT_ON_CASE_FAILURE"):
        return
    if uid is None or project_id is None:
        return
    try:
        db = Database()
        title = f"[自动-{trigger}] {case_name} 执行失败"
        desc_parts = [f"触发来源: {trigger}", f"case_id: {case_id}"]
        if err:
            desc_parts.append(f"错误摘要: {err[:2000]}")
        desc = "\n".join(desc_parts)
        db.create_defect(
            int(project_id),
            title[:200],
            int(uid),
            description=desc[:8000],
            severity="medium",
            priority="medium",
            case_id=int(case_id) if case_id is not None else None,
            run_history_id=payload.get("run_history_id"),
            error_message=(err or "")[:4000],
            status="open",
        )
    except Exception as exc:
        uat_logger.warning(f"[FAILURE_HOOK] 自动创建缺陷失败: {exc}")


def _make_execution_context(trigger: str, extra: dict | None = None) -> ExecutionContext:
    uid = current_user.id if current_user.is_authenticated else None
    tid = None
    if uid is not None:
        try:
            tid = Database().get_user_tenant_id(int(uid))
        except Exception:
            tid = None
    return ExecutionContext(
        user_id=uid,
        tenant_id=tid,
        trigger=trigger,
        on_case_failure=_on_case_execution_failure,
        extra=dict(extra or {}),
    )


def _force_stop_browser_async():
    """异步强制停止浏览器，避免阻塞停止接口响应。"""
    try:
        sync_close_browser()
    except Exception:
        pass
    try:
        force_reset_execution_state()
    except Exception:
        pass

def _parse_api_bool(val, default: bool = True) -> bool:
    """解析 JSON 中的布尔或字符串开关（避免对 True 调用 .strip() 导致 500）。"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() not in ("0", "false", "no", "off", "")


# 统一的API错误处理装饰器
def api_error_handler(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            uat_logger.log_exception(f"API Error in {func.__name__}", e)
            detail = str(e)
            if _is_production_env() and os.environ.get("API_DETAILED_ERRORS", "").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                detail = "服务器内部错误，请稍后重试或联系管理员"
            return jsonify({"success": False, "error": detail}), 500
    return wrapper


def _safe_response_payload_for_api_log(rv):
    """供 log_api_request 使用：勿对 SSE 等流式响应调用 get_json（可能阻塞或抛错）。"""
    if rv is None:
        return None
    try:
        ct = (rv.headers.get("Content-Type") or getattr(rv, "mimetype", None) or "").lower()
    except Exception:
        ct = ""
    if "event-stream" in ct or "text/event-stream" in ct:
        return {"_type": "sse"}
    try:
        return rv.get_json(silent=True)
    except Exception:
        return None


# API请求日志装饰器
def log_api_request(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 记录请求，处理没有请求体的情况
        try:
            request_data = (
                request.get_json(silent=True) if request.method in ["POST", "PUT", "PATCH"] else None
            )
        except Exception:
            # 如果解析JSON失败（如请求体为空），使用None
            request_data = None
        uat_logger.log_api_request(func.__name__, request.method, request_data)
        # 执行函数
        response = func(*args, **kwargs)
        # 记录响应
        try:
            if isinstance(response, tuple):
                rv, status = response[0], response[1]
                uat_logger.log_api_response(
                    func.__name__, status, _safe_response_payload_for_api_log(rv)
                )
            else:
                sc = getattr(response, "status_code", None) or 200
                uat_logger.log_api_response(
                    func.__name__, sc, _safe_response_payload_for_api_log(response)
                )
        except Exception:
            # 如果响应无法解析为JSON，记录基本信息
            if isinstance(response, tuple):
                status_code = response[1]
            else:
                status_code = getattr(response, "status_code", None) or 200
            uat_logger.log_api_response(func.__name__, status_code, None)
        return response
    return wrapper


def _init_cors(flask_app: Flask) -> None:
    """跨域：生产默认不启用宽松 CORS；需跨域前端时设置 FLASK_CORS_ORIGINS（逗号分隔）。"""
    raw = os.environ.get("FLASK_CORS_ORIGINS", "").strip()
    if raw:
        origins = [x.strip() for x in raw.split(",") if x.strip()]
        if origins:
            CORS(flask_app, resources={r"/*": {"origins": origins}})
        return
    if os.environ.get("FLASK_ENV", "").lower() == "production" or os.environ.get("APP_ENV", "").lower() == "production":
        return
    CORS(flask_app, resources={r"/*": {"origins": "*"}})


app = Flask(__name__)
_init_cors(app)
# Session 加密密钥：与 docker-compose 中 SECRET_KEY 一致；未设置时每次启动随机（会话在重启后失效）
_secret_key = (os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY") or "").strip()
if _secret_key:
    app.secret_key = _secret_key
else:
    app.secret_key = secrets.token_hex(32)
    uat_logger.warning(
        "未设置环境变量 SECRET_KEY 或 FLASK_SECRET_KEY，已使用临时随机密钥；"
        "生产环境请务必设置固定密钥，否则重启后会话将全部失效。"
    )

# 模板热重载：DEBUG 开启时一定重载；否则在非 production 下也默认重载，避免改 ai_test.html 等必须反复杀进程。
# （仅 .py 逻辑仍建议开 FLASK_DEBUG=1 以启用代码重载，或改后手动重启。）
_flask_debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
_is_prod = os.environ.get("APP_ENV", "").lower() == "production" or os.environ.get("FLASK_ENV", "").lower() == "production"
if _flask_debug or not _is_prod:
    app.config["TEMPLATES_AUTO_RELOAD"] = True

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

_max_upload_mb = int(os.environ.get("MAX_UPLOAD_MB", "50") or "50")
app.config["MAX_CONTENT_LENGTH"] = max(1, _max_upload_mb) * 1024 * 1024

if os.environ.get("TRUST_X_FORWARDED", "").strip().lower() in ("1", "true", "yes"):
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)


@app.after_request
def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=()",
    )
    return response


@app.before_request
def _sync_playwright_browser_from_session():
    """将当前登录用户的浏览器偏好同步到 Playwright worker（与 session / PLAYWRIGHT_BROWSER 一致）。"""
    try:
        if not current_user.is_authenticated:
            return
    except Exception:
        return
    path = request.path or ""
    if not path.startswith("/api/"):
        return
    if path.startswith("/api/auth/") or path.startswith("/api/health"):
        return
    try:
        sess_val = session.get("playwright_browser_engine")
        if sess_val:
            automation.set_browser_engine(str(sess_val).strip().lower())
        else:
            automation.set_browser_engine(None)
    except Exception:
        pass


@app.errorhandler(413)
def _request_entity_too_large(_e):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "请求体或上传文件超过大小限制"}), 413
    return "Payload Too Large", 413


# ==================== AI模型路由与云端安全网关 ====================
_CLOUD_LLM_ENDPOINT = os.environ.get('CLOUD_LLM_ENDPOINT', '').strip()
_CLOUD_LLM_API_KEY = os.environ.get('CLOUD_LLM_API_KEY', '').strip()
_cloud_llm_gateway = None
_AI_MODEL_CFG_FILE = os.path.join(os.path.dirname(__file__), 'ai_model_registry.json')
_AI_CATALOG_FILE = os.path.join(os.path.dirname(__file__), 'ai_provider_catalog.json')
_AI_MODEL_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._:/+\-]{0,199}$')
_AI_PROFILE_MODEL_ID_RE = re.compile(r'^[^\s]{1,220}$')


def _validate_ai_model_name(name: str) -> tuple:
    n = (name or '').strip()
    if not n:
        return False, 'model_name不能为空'
    if len(n) > 200:
        return False, '模型名称过长（最多200字符）'
    if not _AI_MODEL_NAME_RE.match(n):
        return False, '模型名称格式无效：需以字母或数字开头，仅允许字母、数字及 . _ : / + -'
    return True, n


def _get_cloud_llm_gateway():
    global _cloud_llm_gateway
    if _cloud_llm_gateway is None:
        if not _CLOUD_LLM_ENDPOINT or not _CLOUD_LLM_API_KEY:
            return None
        _cloud_llm_gateway = CloudLLMGateway(
            endpoint=_CLOUD_LLM_ENDPOINT,
            api_key=_CLOUD_LLM_API_KEY,
            timeout=45,
        )
    return _cloud_llm_gateway


def _route_ai_model(task_type: str) -> dict:
    """
    双模型策略:
    - 本地开源模型: 高频、低复杂任务
    - 云端大模型: 脚本修复/复杂报错分析（强制脱敏后上云）
    """
    local_light_tasks = {'intent_classification', 'report_summary', 'log_tagging'}
    local_mid_tasks = {'test_case_generation', 'test_step_generation', 'base_fix_suggestion'}
    cloud_tasks = {'script_repair', 'complex_error_analysis'}

    if task_type in local_light_tasks:
        return {'provider': 'local', 'model': 'qwen2-1.5b-or-qwen1.8b'}
    if task_type in local_mid_tasks:
        return {'provider': 'local', 'model': 'llama3-8b-instruct'}
    if task_type in cloud_tasks:
        return {'provider': 'cloud', 'model': 'cloud_llm'}
    return {'provider': 'local', 'model': _get_active_local_model()}


def _default_ai_model_config() -> dict:
    local_mid = os.environ.get('LOCAL_LLM_MODEL_MID', 'llama3:8b-instruct').strip()
    local_light = os.environ.get('LOCAL_LLM_MODEL_LIGHT', 'qwen2:1.5b').strip()
    models = [m for m in [local_mid, local_light, 'qwen:1.8b-chat'] if m]
    dedup_models = []
    for m in models:
        if m not in dedup_models:
            dedup_models.append(m)
    return {
        'active_local_model': local_mid,
        'local_models': dedup_models,
    }


def _load_ai_provider_catalog() -> dict:
    try:
        with open(_AI_CATALOG_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _catalog_provider_meta(provider_id: str) -> dict:
    for p in _load_ai_provider_catalog().get('providers') or []:
        if isinstance(p, dict) and p.get('id') == provider_id:
            return p
    return {}


def _migrate_v1_config_to_v2(raw: dict, defaults: dict) -> dict:
    models = raw.get('local_models')
    if not isinstance(models, list) or not models:
        models = list(defaults.get('local_models') or [])
    clean_models = []
    for m in models:
        m = (str(m) if m is not None else '').strip()
        if m and m not in clean_models:
            clean_models.append(m)
    active_name = (raw.get('active_local_model') or '').strip()
    profiles = []
    active_id = ''
    for m in clean_models:
        pid = str(uuid.uuid4())
        profiles.append({
            'id': pid,
            'provider': 'ollama',
            'api_style': 'ollama',
            'model_type': 'test_case_generation',
            'model_id': m,
            'label': m,
            'api_key': '',
            'base_url': '',
        })
        if m == active_name:
            active_id = pid
    if not active_id and profiles:
        active_id = profiles[0]['id']
    return {'version': 2, 'active_profile_id': active_id, 'profiles': profiles}


def _normalize_v2_config(raw: dict) -> dict:
    profiles_in = raw.get('profiles')
    if not isinstance(profiles_in, list):
        profiles_in = []
    profiles = []
    seen = set()
    for p in profiles_in:
        if not isinstance(p, dict):
            continue
        pid = (p.get('id') or '').strip() or str(uuid.uuid4())
        if pid in seen:
            continue
        seen.add(pid)
        profiles.append({
            'id': pid,
            'provider': (p.get('provider') or 'ollama').strip(),
            'api_style': (p.get('api_style') or 'ollama').strip(),
            'model_type': (p.get('model_type') or 'test_case_generation').strip(),
            'model_id': (p.get('model_id') or '').strip(),
            'label': (p.get('label') or '').strip(),
            'api_key': p.get('api_key') if isinstance(p.get('api_key'), str) else '',
            'base_url': (p.get('base_url') or '').strip() if isinstance(p.get('base_url'), str) else '',
        })
    aid = (raw.get('active_profile_id') or '').strip()
    if aid and not any(x.get('id') == aid for x in profiles):
        aid = profiles[0]['id'] if profiles else ''
    if not aid and profiles:
        aid = profiles[0]['id']
    return {'version': 2, 'active_profile_id': aid, 'profiles': profiles}


def _load_ai_model_config() -> dict:
    """
    读取 ai_model_registry.json。v1 仅含 local_models，将迁移为 v2 profiles。
    """
    with _ai_model_cfg_lock:
        defaults = _default_ai_model_config()
        if not os.path.exists(_AI_MODEL_CFG_FILE):
            return _migrate_v1_config_to_v2({}, defaults)
        try:
            with open(_AI_MODEL_CFG_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return _migrate_v1_config_to_v2({}, defaults)
            if int(raw.get('version') or 0) >= 2 or 'profiles' in raw:
                return _normalize_v2_config(raw)
            return _migrate_v1_config_to_v2(raw, defaults)
        except Exception:
            return _migrate_v1_config_to_v2({}, defaults)


def _save_ai_model_config(cfg: dict) -> None:
    with _ai_model_cfg_lock:
        if 'profiles' in cfg or int(cfg.get('version') or 0) >= 2:
            to_write = _normalize_v2_config(cfg)
            to_write['version'] = 2
        else:
            to_write = cfg
        with open(_AI_MODEL_CFG_FILE, 'w', encoding='utf-8') as f:
            json.dump(to_write, f, ensure_ascii=False, indent=2)


def _mask_profile_for_api(p: dict) -> dict:
    if not isinstance(p, dict):
        return {}
    key = p.get('api_key')
    has_key = bool((key or '').strip()) if isinstance(key, str) else False
    out = {k: v for k, v in p.items() if k != 'api_key'}
    out['has_api_key'] = has_key
    return out


def _resolve_inference_profile(selected: str) -> tuple:
    """
    返回 (profile 或 None, 纯 Ollama 模型名字符串)。
    选中云端 profile 时 profile 非空；仅本地 Ollama 字符串模式时 profile 为空、第二项为模型名。
    """
    cfg = _load_ai_model_config()
    profiles = cfg.get('profiles') or []
    sel = (selected or '').strip()
    if not profiles:
        return (None, sel or os.environ.get('LOCAL_LLM_MODEL_MID', 'llama3:8b-instruct'))
    if not sel:
        sel = (cfg.get('active_profile_id') or '').strip()
    for p in profiles:
        if p.get('id') == sel:
            return (p, '')
    if sel:
        for p in profiles:
            if p.get('provider') == 'ollama' and p.get('model_id') == sel:
                return (p, '')
        return (None, sel)
    return (None, os.environ.get('LOCAL_LLM_MODEL_MID', 'llama3:8b-instruct'))


def _get_active_local_model() -> str:
    """当前默认：优先 active_profile_id，兼容旧版 active_local_model / 环境变量。"""
    cfg = _load_ai_model_config()
    profiles = cfg.get('profiles') or []
    if profiles:
        aid = (cfg.get('active_profile_id') or '').strip()
        if aid:
            return aid
        return profiles[0].get('id') or ''
    return (cfg.get('active_local_model') or '').strip() or os.environ.get('LOCAL_LLM_MODEL_MID', 'llama3:8b-instruct')


def _ai_memory_context_block(
    user_id: int,
    goal: str,
    probe_url: str = "",
    project_name: str = "",
) -> str:
    """LOCAL_MEMORY_ENABLE=1 时从 SQLite 向量记忆检索相似片段，拼入本地 LLM 提示。"""
    try:
        from ai_memory_store import build_query_for_case, format_memory_block, memory_enabled, search

        if not memory_enabled():
            return ""
        _db = Database()
        tid = _db.get_user_tenant_id(user_id)
        q = build_query_for_case(goal, probe_url=probe_url, project_name=project_name)
        hits = search(user_id, q, tenant_id=tid)
        return format_memory_block(hits)
    except Exception as e:
        uat_logger.debug("ai memory search skipped: %s", e)
        return ""


def _ai_build_dom_pack(snap, embed_remote: bool = False) -> str:
    """LOCAL_AI_DOM_PACK=1 时生成分区 DOM 包；内嵌画布远程会话时不拉本地页 a11y（避免串页）。"""
    if not isinstance(snap, dict) or not snap:
        return ""
    try:
        from ai_page_probe import dom_context_pack, dom_context_pack_enabled

        if not dom_context_pack_enabled():
            return ""
        a11y = ""
        if not embed_remote and (os.environ.get("LOCAL_AI_DOM_A11Y", "1").strip().lower() not in ("0", "false", "no")):
            if sync_automation_session_usable():
                try:
                    a11y = sync_get_accessibility_outline_text(48) or ""
                except Exception as e:
                    uat_logger.debug("accessibility outline skipped: %s", e)
        return dom_context_pack(snap, a11y_outline=a11y) or ""
    except Exception as e:
        uat_logger.debug("dom context pack skipped: %s", e)
        return ""


def _ai_str(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _norm_click_repeat_count(raw) -> int:
    """点击步骤连续执行次数：1–99，非法或空视为 1。"""
    if raw is None or raw == '':
        return 1
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    if n < 1:
        n = 1
    if n > 99:
        n = 99
    return n


def _ai_step_to_db_kwargs(step: dict, case_id: int, step_order: int) -> dict:
    """将 AI 步骤转为 create_test_step 参数；navigate 的 URL 写入 url 与 input_value。"""
    action = _ai_str(step.get("action"))
    st = _ai_str(step.get("selector_type"))
    sv = _ai_str(step.get("selector_value"))
    iv = _ai_str(step.get("input_value"))
    desc = _ai_str(step.get("description"))
    url_col = ""
    if action == "navigate":
        url_col = iv or sv
        st, sv = "", ""
        iv = url_col
    lc = step.get("locator_candidates")
    if lc is not None and not isinstance(lc, str):
        try:
            lc = json.dumps(lc, ensure_ascii=False)
        except Exception:
            lc = ""
    elif lc is None:
        lc = ""
    else:
        lc = str(lc).strip()
    crc = 1
    if action == "click":
        crc = _norm_click_repeat_count(step.get("click_repeat_count"))
    cmp_out = "equals"
    if action == "assert":
        cmp_out = _ai_str(step.get("compare_type")) or "text_contains"
    return {
        "case_id": case_id,
        "action": action,
        "selector_type": st,
        "selector_value": sv,
        "input_value": iv,
        "description": desc,
        "step_order": step_order,
        "page_name": "",
        "swipe_x": "",
        "swipe_y": "",
        "url": url_col,
        "enter_iframe": False,
        "iframe_selector": "",
        "compare_type": cmp_out,
        "locator_candidates": lc,
        "click_repeat_count": crc,
    }

# ==================== Flask-Login 初始化 ====================
login_manager = LoginManager(app)
login_manager.login_view = 'login_page'
login_manager.login_message = '请先登录'


@login_manager.unauthorized_handler
def _api_unauthorized():
    """API 未登录时返回 JSON，避免前端 fetch().json() 解析到登录页 HTML。"""
    if (request.path or "").startswith("/api/"):
        return jsonify({"success": False, "error": "未登录，请重新登录"}), 401
    return redirect(url_for("login_page", next=request.path))


@app.errorhandler(404)
def _http_not_found(e):
    """API 路径不存在时返回 JSON，避免前端解析到 HTML 404 页。"""
    if (request.path or "").startswith("/api/"):
        return jsonify({
            "success": False,
            "error": "接口不存在，请确认已重启后端服务（python app.py）",
        }), 404
    return e


class UserModel(UserMixin):
    """Flask-Login 用户模型"""
    def __init__(self, user_data: dict):
        self.id = user_data['id']
        self.username = user_data['username']
        self.role = user_data['role']
        self.is_active_flag = bool(user_data.get('is_active', 1))

    def get_id(self):
        return str(self.id)

    @property
    def is_active(self):
        return self.is_active_flag

@login_manager.user_loader
def load_user(user_id):
    _db = Database()
    user_data = _db.get_user_by_id(int(user_id))
    if user_data:
        return UserModel(user_data)
    return None

def role_required(*roles):
    """角色权限装饰器"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({'success': False, 'error': '未登录'}), 401
            if current_user.role not in roles:
                return jsonify({'success': False, 'error': '权限不足'}), 403
            return func(*args, **kwargs)
        return wrapper
    return decorator

def token_or_login_required(func):
    """支持 Bearer Token 或 Flask-Login Session 两种认证方式（用于 Webhook/CI）"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token_val = auth_header[7:]
            _db = Database()
            token_info = _db.get_token_by_value(token_val)
            if not token_info:
                return jsonify({'success': False, 'error': '无效的 API Token'}), 401
            import datetime
            if token_info.get('expires_at'):
                try:
                    exp = datetime.datetime.strptime(token_info['expires_at'], '%Y-%m-%d %H:%M:%S')
                    if exp < datetime.datetime.now():
                        return jsonify({'success': False, 'error': 'API Token 已过期'}), 401
                except Exception:
                    pass
            return func(*args, **kwargs)
        if current_user.is_authenticated:
            return func(*args, **kwargs)
        return jsonify({'success': False, 'error': '未认证'}), 401
    return wrapper


def project_access_required(min_role='viewer'):
    """项目访问权限装饰器 - 检查用户是否有指定项目的访问权限"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({'success': False, 'error': '未登录'}), 401

            # 从参数中获取 project_id
            project_id = kwargs.get('project_id') or (
                request.view_args.get('project_id') if request.view_args else None
            )
            if not project_id:
                project_id = request.args.get('project_id', type=int)
            if not project_id:
                _body = request.get_json(silent=True) or {}
                pid = _body.get('project_id')
                if pid is not None:
                    try:
                        project_id = int(pid)
                    except (TypeError, ValueError):
                        project_id = None

            if project_id:
                _db = Database()
                if not _db.check_project_access(current_user.id, project_id, min_role):
                    return jsonify({'success': False, 'error': '无权限访问此项目'}), 403

            return func(*args, **kwargs)
        return wrapper
    return decorator


def audit_log(action: str, target_type: str):
    """审计日志装饰器 - 记录用户操作"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)

            # 异步记录审计日志（不阻塞主流程）
            try:
                if current_user.is_authenticated:
                    _db = Database()
                    target_id = None
                    # 尝试从 kwargs 或请求中获取目标ID
                    if 'id' in kwargs:
                        target_id = kwargs['id']
                    elif request.view_args and 'id' in request.view_args:
                        target_id = request.view_args['id']

                    details = None
                    if request.is_json:
                        try:
                            details = json.dumps(request.get_json(silent=True) or {}, ensure_ascii=False)
                        except (TypeError, ValueError):
                            pass

                    _db.add_audit_log(
                        user_id=current_user.id,
                        username=current_user.username,
                        action=action,
                        target_type=target_type,
                        target_id=target_id,
                        details=details,
                        ip_address=request.remote_addr
                    )
            except Exception as e:
                uat_logger.error(f"记录审计日志失败: {e}")

            return result
        return wrapper
    return decorator


def feature_required(feature_name: str):
    """功能可用性检查装饰器 - 检查某功能是否在当前 License 中可用"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not license_manager.check_feature_available(feature_name):
                limits = license_manager.get_limits()
                license_type = limits.get('license_type', 'personal')
                return jsonify({
                    'success': False,
                    'error': f'此功能需要企业版 License。当前版本: {license_type}'
                }), 403
            return func(*args, **kwargs)
        return wrapper
    return decorator


def check_limit(limit_type: str, get_current_value_func=None):
    """限制检查装饰器 - 检查是否超出使用限制"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_value = 0
            if get_current_value_func:
                try:
                    current_value = get_current_value_func()
                except Exception:
                    current_value = 0

            result = license_manager.check_limit(limit_type, current_value)
            if not result['allowed']:
                return jsonify({
                    'success': False,
                    'error': result['message'],
                    'limit': result['limit'],
                    'current': result['current']
                }), 403

            return func(*args, **kwargs)
        return wrapper
    return decorator

# 初始化数据库
db = Database()

# 首次启动自动创建管理员账号
def _ensure_admin():
    if db.count_users() != 0:
        return
    pw_env = (os.environ.get("ADMIN_INITIAL_PASSWORD") or "").strip()
    if pw_env:
        if len(pw_env) < 8:
            uat_logger.warning(
                "ADMIN_INITIAL_PASSWORD 长度不足 8 位，已忽略；将改为随机密码（请查看下一条日志）。"
            )
            pw_plain = secrets.token_urlsafe(14)
            uat_logger.warning(
                f"首次启动：已创建管理员 admin，随机初始密码（仅此一次输出，请保存并尽快修改）: {pw_plain}"
            )
        else:
            pw_plain = pw_env
            uat_logger.info("首次启动：已创建管理员 admin（密码来自环境变量 ADMIN_INITIAL_PASSWORD）。")
    elif os.environ.get("ALLOW_INSECURE_DEFAULT_ADMIN", "").strip().lower() in ("1", "true", "yes"):
        pw_plain = "admin123"
        uat_logger.warning(
            "首次启动：已创建管理员 admin/admin123（ALLOW_INSECURE_DEFAULT_ADMIN 已开启，切勿用于公网或生产）。"
        )
    else:
        pw_plain = secrets.token_urlsafe(14)
        uat_logger.warning(
            f"首次启动：已创建管理员 admin，随机初始密码（仅此一次输出，请保存并尽快修改）: {pw_plain}"
        )
    db.create_user("admin", generate_password_hash(pw_plain), role="admin")


_ensure_admin()

register_deployment_hooks(app, Database, UserModel)
init_server_instance(Database)

# 主页路由
@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/create_project')
@login_required
def create_project_landing():
    """与首页「新建项目」链接一致；支持新标签页打开、收藏夹与直链，避免 404。"""
    return redirect(f"{url_for('index')}?openCreateProject=1")

# ==================== 用户认证路由 ====================

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/profile')
@login_required
def profile_page():
    return render_template('profile.html')

@app.route('/settings')
@login_required
def settings_center_page():
    return render_template('settings.html')


@app.route('/plugin-market')
@login_required
def plugin_market_page():
    return render_template('plugin_market.html')


@app.route('/api/plugin-market/catalog', methods=['GET'])
@login_required
@api_error_handler
def api_plugin_market_catalog():
    from web_capture.plugin_market import get_plugin_catalog

    origin = (request.host_url or '').rstrip('/')
    return jsonify({'success': True, 'plugins': get_plugin_catalog(platform_origin=origin)})


@app.route('/api/plugin-market/install', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_plugin_market_install():
    data = request.get_json(silent=True) or {}
    from web_capture.plugin_market import install_plugin

    plugin_id = (data.get('plugin_id') or data.get('id') or '').strip()
    if not plugin_id and data.get('browser'):
        plugin_id = f"web-capture-{(data.get('browser') or '').strip().lower()}"
    result = install_plugin(plugin_id)
    return jsonify(result), (200 if result.get('success') else 400)


@app.route('/api/plugin-market/status', methods=['GET'])
@login_required
@api_error_handler
def api_plugin_market_status():
    from web_capture.plugin_market import get_plugin_catalog, get_preferred_browser_for_capture

    origin = (request.host_url or '').rstrip('/')
    plugins = get_plugin_catalog(platform_origin=origin)
    installed = [p for p in plugins if p.get('installed')]
    return jsonify({
        'success': True,
        'plugins': plugins,
        'preferred_browser': get_preferred_browser_for_capture(),
        'has_capture_plugin': bool(installed),
    })


@app.route('/api/plugin-market/prepare', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_plugin_market_prepare():
    """兼容旧接口：转为一键安装。"""
    data = request.get_json(silent=True) or {}
    from web_capture.plugin_market import install_plugin

    browser = (data.get('browser') or 'chrome').strip().lower()
    result = install_plugin(f"web-capture-{browser}")
    return jsonify(result), (200 if result.get('success') else 400)


@app.route('/schedules')
@login_required
@feature_required('schedule')
def schedules_page():
    return render_template('schedules.html')

@app.route('/notifications')
@login_required
@role_required('admin')
def notifications_page():
    return render_template('notifications.html')

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    ip = _login_client_ip()
    blocked, retry_after = _login_is_rate_limited(ip)
    if blocked:
        resp = jsonify({"success": False, "error": "登录尝试过于频繁，请稍后再试"})
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429

    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400
    _db = Database()
    user_data = _db.get_user_by_username(username)
    if not user_data or not check_password_hash(user_data['password_hash'], password):
        _login_record_failure(ip)
        return jsonify({'success': False, 'error': '用户名或密码错误'}), 401
    if not user_data.get('is_active', 1):
        return jsonify({'success': False, 'error': '账号已被禁用'}), 403
    _login_clear_failures(ip)
    user = UserModel(user_data)
    login_user(user, remember=True)
    _db.update_user_last_login(user_data['id'])
    uat_logger.info(f"用户 {username} 登录成功")
    try:
        from client_config_store import get_team_server_url
        from deployment_config import is_client_mode
        from platform_sync import sync_product_user

        sync_product_user(
            user_data["id"],
            username,
            email=user_data.get("email") or "",
            team_server_url=get_team_server_url() if is_client_mode() else "",
        )
    except Exception:
        pass
    return jsonify({'success': True, 'user': {'id': user_data['id'], 'username': username, 'role': user_data['role']}})

@app.route('/api/auth/logout', methods=['POST'])
@login_required
def api_logout():
    username = current_user.username
    logout_user()
    uat_logger.info(f"用户 {username} 已注销")
    return jsonify({'success': True})

@app.route('/api/auth/me', methods=['GET'])
@login_required
def api_me():
    """获取当前用户信息（包含权限和License信息）"""
    # 获取当前 License 信息
    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    
    # 获取今日使用统计
    _db = Database()
    today_stats = _db.get_user_usage_stats(current_user.id)
    
    return jsonify({
        'success': True, 
        'user': {
            'id': current_user.id, 
            'username': current_user.username, 
            'role': current_user.role
        },
        'deployment': {
            'mode': __import__('deployment_config').get_deployment_mode().value,
            'team_server_url': __import__('client_config_store').get_team_server_url() if is_client_mode() else '',
        },
        'license': {
            'type': license_info.license_type,
            'features': limits['features'],  # 使用 get_limits 返回的 features
            'limits': {
                'max_projects': limits['max_projects'],
                'max_cases_per_project': limits['max_cases_per_project'],
                'max_executions_per_day': limits['max_executions_per_day']
            }
        },
        'usage': {
            'today_executions': today_stats.get('execution_count', 0) if today_stats else 0,
            'today_created_cases': today_stats.get('created_cases', 0) if today_stats else 0
        }
    })

@app.route('/api/auth/me', methods=['PUT'])
@login_required
@api_error_handler
def api_update_me():
    """更新当前用户信息"""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip() or None

    if email and '@' not in email:
        return jsonify({'success': False, 'error': '邮箱格式不正确'}), 400

    _db = Database()
    _db.update_user(current_user.id, email=email)
    uat_logger.info(f"用户 {current_user.username} 更新了个人信息")
    return jsonify({'success': True})

@app.route('/api/auth/change_password', methods=['POST'])
@login_required
def api_change_password():
    data = request.get_json(silent=True) or {}
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')
    if not old_password or not new_password:
        return jsonify({'success': False, 'error': '密码不能为空'}), 400
    if len(new_password) < 6:
        return jsonify({'success': False, 'error': '新密码长度不能小于6位'}), 400
    _db = Database()
    user_data = _db.get_user_by_id(current_user.id)
    if not check_password_hash(user_data['password_hash'], old_password):
        return jsonify({'success': False, 'error': '原密码错误'}), 401
    _db.update_user(current_user.id, password_hash=generate_password_hash(new_password))
    uat_logger.info(f"用户 {current_user.username} 修改了密码")
    return jsonify({'success': True})

# ==================== 用户管理API（仅管理员） ====================

@app.route('/api/users', methods=['GET'])
@login_required
@role_required('admin')
def api_get_users():
    _db = Database()
    users = _db.get_all_users()
    return jsonify({'success': True, 'users': users})

@app.route('/api/users', methods=['POST'])
@login_required
@role_required('admin')
def api_create_user():
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    email = data.get('email', '').strip() or None
    role = data.get('role', 'tester')
    if not username or not password:
        return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400
    if role not in ('admin', 'tester', 'viewer'):
        return jsonify({'success': False, 'error': '无效的角色，可选: admin/tester/viewer'}), 400
    _db = Database()
    user_id = _db.create_user(username, generate_password_hash(password), email, role)
    if user_id is None:
        return jsonify({'success': False, 'error': '用户名或邮箱已存在'}), 409
    return jsonify({'success': True, 'user_id': user_id})

@app.route('/api/users/<int:user_id>', methods=['PUT'])
@app.route('/api/users/<int:user_id>/update', methods=['POST'])
@login_required
@role_required('admin')
def api_update_user(user_id):
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    if not username:
        return jsonify({'success': False, 'error': '用户名不能为空'}), 400
    role = data.get('role', 'tester')
    if role not in ('admin', 'tester', 'viewer'):
        return jsonify({'success': False, 'error': '无效的角色，可选: admin/tester/viewer'}), 400
    update_kwargs = {'username': username, 'role': role}
    if 'email' in data:
        email_raw = data.get('email')
        if isinstance(email_raw, str):
            update_kwargs['email'] = email_raw.strip() or None
        else:
            update_kwargs['email'] = email_raw
    if data.get('password'):
        update_kwargs['password_hash'] = generate_password_hash(data['password'])
    if 'is_active' in data:
        update_kwargs['is_active'] = data.get('is_active')
    _db = Database()
    success = _db.update_user(user_id, **update_kwargs)
    if not success:
        return jsonify({'success': False, 'error': '更新失败：用户名或邮箱已被占用，或用户不存在'}), 409
    return jsonify({'success': True})

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@app.route('/api/users/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def api_delete_user(user_id):
    if user_id == current_user.id:
        return jsonify({'success': False, 'error': '不能删除当前登录的账号'}), 400
    _db = Database()
    success = _db.delete_user(user_id)
    if not success:
        return jsonify({'success': False, 'error': '删除失败：用户不存在或仍存在无法自动解除的关联数据'}), 400
    return jsonify({'success': True})

@app.route('/create_case_v2')
@login_required
def create_case_v2():
    """旧版独立建用例页；模板已移除，跳转到项目列表（从项目进入「用例管理」创建用例）。"""
    return redirect(url_for('list_projects'))

# AI 测试工作台（本地模型 + 内嵌预览：与后台 Playwright 自动化会话经 /api/navigate 同步）
@app.route('/ai-test')
@login_required
def ai_test_page():
    """AI 生成测试步骤；内嵌区与后台 Playwright 会话同步（可选远程画布为另一进程）。"""
    from ai_chat_tool_loop import ai_chat_tools_enabled
    from openclaw_gateway_client import OpenClawGatewayClient

    _oc = OpenClawGatewayClient()
    resp = make_response(
        render_template(
            'ai_test.html',
            ai_chat_tools_env_enabled=ai_chat_tools_enabled(),
            openclaw_gateway_configured=_oc.is_configured(),
        )
    )
    # 用于核对浏览器是否命中本仓库模板（与页内 #aiTestBuildMarker 文案一致）
    resp.headers['X-AI-Test-Template'] = 'playwright-ui-dedup-2026-04-24'
    return resp


# 项目管理页面
@app.route('/list_projects')
@login_required
def list_projects():
    return render_template('list_projects.html')


def _validate_and_fix_url(url: str) -> tuple:
    """校验并修备URL。返回 (fixed_url, error_msg)。
    fixed_url 为 None 表示应跳过； error_msg 不为 None 表示应报错。
    """
    import re
    if not url or not url.strip():
        return None, None  # 空 URL 跳过

    # 兼容全角冒号等输入，避免 about：blank 等变体漏判
    url = url.strip().replace('：', ':')

    # 跳过无意义占位符URL
    skip_patterns = [
        'example.com', '0.0.0.0', '0.0.0.1', '127.0.0.1',
        'localhost', 'about:blank', 'about:newtab',
    ]
    for pat in skip_patterns:
        if pat in url.lower():
            uat_logger.warning(f"检测到占位符URL ({url})，跳过初始导航")
            return None, None  # 跳过

    # 自动添加协议
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url

    # 判断 URL 格式是否合法
    # 🔥 修复：IP 地址正则表达式增加 100-199 范围的匹配 (1[0-9]{2})
    # IP 第一段 (1-255): (25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[1-9])
    # IP 中间/最后段 (0-255): (25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])
    url_re = re.compile(
        r'^https?://'
        r'(([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}'
        r'|((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[1-9])\.){1}'
        r'((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){2}'
        r'(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]))'
        r'(:\d+)?(/.*)?$'
    )
    if not url_re.match(url):
        return None, f"无效的URL地址: {url}，请检查用例的测试URL配置"

    return url, None


def _get_first_valid_step_url(steps) -> tuple:
    """从步骤中提取首个有效URL。返回 (fixed_url, source_desc)。"""
    if not steps:
        return None, None

    for step in steps:
        action = (step.get('action') or '').strip().lower()
        candidates = []

        if step.get('url'):
            candidates.append(('step.url', step.get('url')))
        if action == 'navigate' and step.get('input_value'):
            candidates.append(('navigate.input_value', step.get('input_value')))

        for source, raw in candidates:
            fixed_url, url_err = _validate_and_fix_url(raw)
            if fixed_url:
                return fixed_url, source
            if url_err:
                uat_logger.warning(f"步骤URL无效，已跳过（{source}）: {url_err}")

    return None, None


def _resolve_case_navigation_url(case: dict = None, case_id: int = None, steps: list = None, fallback_url: str = None) -> tuple:
    """统一解析运行/录制前导航URL。优先级：case.url > step.url/navigate.input_value > fallback_url"""
    local_case = case or {}
    if not local_case and case_id:
        local_case = db.get_test_case(case_id) or {}

    case_url = local_case.get('url')
    if case_url:
        fixed_url, url_err = _validate_and_fix_url(case_url)
        if fixed_url:
            return fixed_url, 'case.url'
        if url_err:
            uat_logger.warning(f"用例URL无效，继续尝试步骤URL: {url_err}")

    local_steps = steps
    if local_steps is None and case_id:
        local_steps = db.get_case_steps(case_id)
    step_url, step_source = _get_first_valid_step_url(local_steps or [])
    if step_url:
        return step_url, step_source

    if fallback_url:
        fixed_url, url_err = _validate_and_fix_url(fallback_url)
        if fixed_url:
            return fixed_url, 'request.url'
        if url_err:
            return None, f"无效的URL地址: {fallback_url}"

    return None, None


def _resolve_case_navigation_url_for_data_row(
    db,
    case: dict,
    case_id: int,
    steps: list,
    row_data: dict,
    project_id,
    resolve_with_row_fn,
):
    """数据驱动每行：与单用例相同的 URL 优先级，并对地址做项目/用例变量与 {{row.*}} 替换。"""
    local_case = case or {}
    raw_case_url = (local_case.get('url') or '').strip()
    if raw_case_url:
        resolved = resolve_with_row_fn(
            db.resolve_variables(raw_case_url, project_id=project_id, case_id=case_id),
            row_data,
        )
        fixed_url, url_err = _validate_and_fix_url((resolved or '').strip())
        if fixed_url:
            return fixed_url, 'case.url'
        if url_err:
            uat_logger.warning(f'[数据驱动] 用例 URL 无效，继续尝试步骤: {url_err}')

    for step in steps or []:
        action = (step.get('action') or '').strip().lower()
        candidates = []
        if step.get('url'):
            candidates.append(('step.url', step.get('url')))
        if action == 'navigate' and step.get('input_value'):
            candidates.append(('navigate.input_value', step.get('input_value')))
        for source, raw in candidates:
            resolved = resolve_with_row_fn(
                db.resolve_variables(raw or '', project_id=project_id, case_id=case_id),
                row_data,
            )
            fixed_url, url_err = _validate_and_fix_url((resolved or '').strip())
            if fixed_url:
                return fixed_url, source
            if url_err:
                uat_logger.warning(f'[数据驱动] 步骤 URL 无效已跳过（{source}）: {url_err}')

    return None, None


# 测试用例管理页面（新版本）
@app.route('/list_cases_v2/<int:project_id>')
@login_required
@project_access_required(min_role='viewer')
def list_cases_v2(project_id):
    return render_template('list_cases_v2.html', project_id=project_id)

# 测试步骤管理页面
@app.route('/list_steps')
@login_required
def list_steps():
    case_id = request.args.get('case_id', type=int)
    if case_id:
        c = db.get_test_case_v2(case_id)
        if c and _app_case_type(c) == 'api':
            from flask import redirect, url_for
            pid = c.get('project_id') or ''
            q = f"?project_id={pid}" if pid else ""
            return redirect(url_for('api_case_detail_page', case_id=case_id) + q)
    return render_template('list_steps.html')

# API: 创建测试用例
@app.route('/api/create_case', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
@audit_log('CREATE_CASE', 'case')
def api_create_case():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '')
    description = data.get('description', '')
    target_url = data.get('target_url', '')
    
    if not name:
        return jsonify({'error': '用例名称不能为空'}), 400
    
    case_id = db.create_test_case(name, description, target_url)
    return jsonify({'success': True, 'case_id': case_id})

# API: 获取所有测试用例
@app.route('/api/test_cases', methods=['GET'])
@login_required
@api_error_handler
@log_api_request
def api_get_test_cases():
    cases = db.get_all_test_cases()
    return jsonify({'cases': cases})

# API: 获取单个测试用例
@app.route('/api/test_case/<int:case_id>', methods=['GET'])
@login_required
@api_error_handler
@log_api_request
def api_get_test_case(case_id):
    case = db.get_test_case(case_id)
    if not case:
        return jsonify({'error': '测试用例不存在'}), 404
    return jsonify({'test_case': case})

# API: 更新测试用例
@app.route('/api/test_case/<int:case_id>', methods=['PUT'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
@audit_log('UPDATE_CASE', 'case')
def api_update_test_case(case_id):
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    description = data.get('description')
    target_url = data.get('target_url')
    
    success = db.update_test_case(case_id, name, description, target_url)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '更新测试用例失败'}), 400

# API: 删除测试用例
@app.route('/api/test_case/<int:case_id>', methods=['DELETE'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
@audit_log('DELETE_CASE', 'case')
def api_delete_test_case(case_id):
    success = db.delete_test_case(case_id)
    
    if success:
        try:
            db.prune_orphan_run_history()
        except Exception:
            pass
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '删除测试用例失败'}), 400

# API: 执行多个测试用例
@app.route('/api/execute_multiple_cases', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
@audit_log('EXECUTE_CASES', 'execution')
def api_execute_multiple_cases():
    data = request.get_json(silent=True) or {}
    case_ids = data.get('case_ids', [])
    
    if not case_ids:
        return jsonify({'success': False, 'error': '缺少测试用例ID列表参数'}), 400
    
    if not isinstance(case_ids, list):
        return jsonify({'success': False, 'error': 'case_ids参数必须是数组'}), 400
    
    # ===== 执行次数限制检查 =====
    user_id = current_user.id
    _db = Database()
    
    # 获取当前 License 信息
    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    is_free_user = license_info.license_type == LicenseType.FREE.value
    
    # 获取今日已执行次数
    today_stats = _db.get_user_usage_stats(user_id)
    current_count = today_stats.get('execution_count', 0) if today_stats else 0
    
    # 获取每日执行限制（-1表示无限制）
    daily_limit = limits.get('max_executions_per_day', -1)
    
    # 免费版每日限制10次
    if is_free_user:
        DAILY_LIMIT = 10
        
        if current_count >= DAILY_LIMIT:
            return jsonify({
                'success': False,
                'error': f'今日执行次数已达上限（{DAILY_LIMIT}次）。请升级至团队版解除限制。',
                'limit_reached': True,
                'current_count': current_count,
                'daily_limit': DAILY_LIMIT,
                'upgrade_url': '/upgrade'
            }), 403
        
        # 检查是否会超出限制
        if current_count + len(case_ids) > DAILY_LIMIT:
            remaining = DAILY_LIMIT - current_count
            return jsonify({
                'success': False,
                'error': f'本次执行将超出今日限制。剩余次数: {remaining}，本次请求: {len(case_ids)}个用例。',
                'limit_exceeded': True,
                'current_count': current_count,
                'daily_limit': DAILY_LIMIT,
                'remaining': remaining,
                'upgrade_url': '/upgrade'
            }), 403
    elif daily_limit > 0 and current_count >= daily_limit:
        # 非免费版但有每日限制的情况
        return jsonify({
            'success': False,
            'error': f'今日执行次数已达上限（{daily_limit}次）。请联系管理员。',
            'limit_reached': True,
            'current_count': current_count,
            'daily_limit': daily_limit
        }), 403
    
    # 记录执行次数（所有版本都记录，用于统计和显示）
    new_count = _db.increment_execution_count(user_id)
    uat_logger.info(f"📊 [LICENSE] 用户 {user_id} 今日执行次数: {new_count}")
    
    # 添加调试信息
    uat_logger.info(f"📥 [API_ENTRY] 接收到的执行顺序: {case_ids}")
    uat_logger.info(f"📥 [API_ENTRY] 用例数量: {len(case_ids)}")
    
    uat_logger.info(f"开始执行多个测试用例，共 {len(case_ids)} 个用例")
    with _case_run_lock:
        _case_run_jobs[user_id] = {
            'active': True,
            'cancel_requested': False,
            'run_mode': 'multiple_cases',
            'case_id': None,
            'case_name': '批量执行',
            'total_steps': len(case_ids),
            'completed_steps': 0,
            'current_step_order': 0,
            'current_action': 'batch_run',
            'message': f'正在批量执行 {len(case_ids)} 个用例',
            'started_at': time.time(),
        }
    
    results = None
    
    try:
        # 同步执行多个测试用例
        uat_logger.info(f"🚀 [API] 开始执行 {len(case_ids)} 个测试用例")
        
        # 🔥 性能优化：移除 API 层冗余的浏览器状态预检测
        # sync_execute_multiple_test_cases 内部已包含完整的浏览器状态检测和恢复逻辑
        
        try:
            # 创建独立的数据库连接实例，确保线程安全
            # 注意: Database 已在文件开头导入，不要在此重复导入，否则会导致变量作用域问题
            thread_db = Database()
            
            # 直接在主线程中同步执行测试用例，确保严格的执行顺序
            uat_logger.info(f"🚀 [API] 在主线程中同步执行测试用例序列: {case_ids}")
            def _should_stop_batch():
                with _case_run_lock:
                    return bool(_case_run_jobs.get(user_id, {}).get('cancel_requested'))

            results = sync_execute_multiple_test_cases(
                case_ids,
                thread_db,
                should_stop=_should_stop_batch,
                execution_context=_make_execution_context("ui"),
            )
            uat_logger.info(f"✅ [API] 多个测试用例同步执行完成")
        except Exception as e:
            uat_logger.error(f"❌ [API] 执行测试用例时发生异常: {str(e)}")
            results = {
                "total_cases": len(case_ids),
                "successful_cases": 0,
                "failed_cases": len(case_ids),
                "case_results": [
                    {
                        "case_id": case_id,
                        "case_name": "未知",
                        "status": "error",
                        "error": f"执行出错: {str(e)}"
                    } for case_id in case_ids
                ]
            }
        
        # 执行结果已在上面获取
        
        # 记录执行结果
        uat_logger.info(f"多个测试用例执行完成，成功: {results['successful_cases']}, 失败: {results['failed_cases']}")
        _case_job_update(
            user_id,
            completed_steps=len(case_ids),
            current_step_order=len(case_ids),
            message=f"批量执行完成：成功 {results.get('successful_cases', 0)}，失败 {results.get('failed_cases', 0)}",
        )
        
    except Exception as e:
        uat_logger.error(f"执行测试用例时出错: {e}")
        results = {
            "total_cases": len(case_ids),
            "successful_cases": 0,
            "failed_cases": len(case_ids),
            "case_results": [
                {
                    "case_id": case_id,
                    "case_name": "未知",
                    "status": "error",
                    "error": f"系统错误: {str(e)}"
                } for case_id in case_ids
            ]
        }
    finally:
        # 确保浏览器资源清理，无论测试用例执行结果如何
        try:
            uat_logger.info("🔧 [API_CLEANUP] 开始清理浏览器资源...")
            # 调用同步关闭浏览器函数确保资源释放
            sync_close_browser()
            uat_logger.info("✅ [API_CLEANUP] 浏览器资源清理完成")
        except Exception as close_error:
            uat_logger.warning(f"⚠️ [API_CLEANUP] 清理浏览器时出现警告: {close_error}")
        with _case_run_lock:
            job = _case_run_jobs.get(user_id)
            if job:
                job['active'] = False
                job['finished_at'] = time.time()
                job['duration'] = round(time.time() - job.get('started_at', time.time()), 2)
    
    with _case_run_lock:
        stopped = bool(_case_run_jobs.get(user_id, {}).get('cancel_requested'))
    response_data = {'success': True, 'results': results, 'stopped': stopped}
    return jsonify(response_data)

# API: 导航到指定URL
@app.route('/api/navigate', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_navigate():
    data = request.get_json(silent=True) or {}
    url = data.get('url', '')
    iframe_selector = data.get('iframe_selector', '')
    
    if not url:
        return jsonify({'error': 'URL不能为空'}), 400
    
    sync_navigate_to(url, iframe_selector=iframe_selector)
    return jsonify({'success': True})

# API: 执行滚动操作
@app.route('/api/scroll', methods=['POST'])
@api_error_handler
@log_api_request
def api_scroll():
    data = request.get_json(silent=True) or {}
    direction = data.get('direction', 'down')
    pixels = data.get('pixels', 500)
    iframe_selector = data.get('iframe_selector', '')
    
    sync_scroll_page(direction, pixels, iframe_selector=iframe_selector)
    return jsonify({'success': True})

# API: 提取元素文本
@app.route('/api/extract_element_text', methods=['POST'])
@api_error_handler
@log_api_request
def api_extract_element_text():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', '')
    selector_type = data.get('selector_type', 'css')
    
    if selector == 'body':
        text = sync_get_page_text()
    else:
        text = sync_extract_element_text(selector, selector_type)
    return jsonify({'success': True, 'text': text})

# API: 提取元素JSON数据
@app.route('/api/extract_element_json', methods=['POST'])
@api_error_handler
@log_api_request
def api_extract_element_json():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', '')
    selector_type = data.get('selector_type', 'css')
    
    if not selector:
        return jsonify({'success': False, 'error': '选择器不能为空'}), 400
    
    json_data = sync_extract_element_json(selector, selector_type)
    return jsonify({'success': True, 'json': json_data})

# API: 获取页面标题
@app.route('/api/page_title', methods=['GET'])
@api_error_handler
@log_api_request
def api_page_title():
    title = sync_get_page_title()
    return jsonify({'success': True, 'title': title})

# API: 获取当前URL
@app.route('/api/current_url', methods=['GET'])
@api_error_handler
@log_api_request
def api_current_url():
    url = sync_get_current_url()
    return jsonify({'success': True, 'url': url})

# API: 获取页面上所有链接
@app.route('/api/links', methods=['GET'])
@api_error_handler
@log_api_request
def api_links():
    links = sync_get_all_links()
    return jsonify({'success': True, 'links': links})

def _legacy_visual_picker_removed():
    """旧版网页拾取 API 已收敛至统一元素捕获。"""
    return jsonify({
        'success': False,
        'error': '该接口已移除，请使用 /api/element-picker/start|stop|status',
        'deprecated': True,
    }), 410


# API: 启动可视化选择（已废弃）
@app.route('/api/start_visual_selection', methods=['POST'])
@api_error_handler
@log_api_request
def api_start_visual_selection():
    return _legacy_visual_picker_removed()


# API: 停止可视化选择（已废弃）
@app.route('/api/stop_visual_selection', methods=['POST'])
@api_error_handler
@log_api_request
def api_stop_visual_selection():
    return _legacy_visual_picker_removed()


# API: 检查选择的元素（已废弃）
@app.route('/api/check_selected_element', methods=['GET'])
@api_error_handler
@log_api_request
def api_check_selected_element():
    return _legacy_visual_picker_removed()

# API: 提取元素数据
@app.route('/api/extract_element_data', methods=['POST'])
@api_error_handler
@log_api_request
def api_extract_element_data():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', '')
    
    if not selector:
        return jsonify({'error': '选择器不能为空'}), 400
    
    element_data = sync_extract_element_data(selector)
    return jsonify({'success': True, 'data': element_data})

# API: 获取页面数据
@app.route('/api/page_data', methods=['GET'])
@api_error_handler
@log_api_request
def api_page_data():
    page_data = automation.get_page_data()
    return jsonify({'success': True, 'data': page_data})

# API: 分析页面内容
@app.route('/api/analyze_content', methods=['POST'])
@api_error_handler
@log_api_request
def api_analyze_content():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', 'body')
    
    analysis = automation.analyze_page_content(selector)
    return jsonify({'success': True, 'analysis': analysis})


@app.route('/api/ai/models', methods=['GET'])
@login_required
@api_error_handler
@log_api_request
def api_get_ai_models():
    cfg = _load_ai_model_config()
    ollama_models, ollama_error = local_ai_service.list_installed_models()
    profiles = [_mask_profile_for_api(p) for p in (cfg.get('profiles') or [])]
    local_names = [p.get('model_id') for p in (cfg.get('profiles') or []) if p.get('provider') == 'ollama' and p.get('model_id')]
    return jsonify({
        'success': True,
        'version': cfg.get('version') or 2,
        'active_profile_id': cfg.get('active_profile_id'),
        'profiles': profiles,
        'active_local_model': cfg.get('active_local_model'),
        'local_models': local_names or cfg.get('local_models', []),
        'provider_catalog': _load_ai_provider_catalog(),
        'ollama_base_url': local_ai_service.base_url,
        'ollama_models': ollama_models,
        'ollama_error': ollama_error,
    })


@app.route('/api/ai/models/ollama', methods=['GET'])
@login_required
@api_error_handler
@log_api_request
def api_get_ollama_models():
    ollama_models, ollama_error = local_ai_service.list_installed_models()
    return jsonify({
        'success': True,
        'ollama_base_url': local_ai_service.base_url,
        'ollama_models': ollama_models,
        'ollama_error': ollama_error,
    })


def _validate_profile_model_id(mid: str) -> tuple:
    m = (mid or '').strip()
    if not m:
        return False, 'model_id不能为空'
    if len(m) > 220:
        return False, 'model_id过长'
    if not _AI_PROFILE_MODEL_ID_RE.match(m):
        return False, 'model_id格式无效（勿含空白）'
    return True, m


@app.route('/api/ai/models', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_add_ai_model():
    data = request.get_json(silent=True) or {}
    if (data.get('provider') or '').strip() or (data.get('model_id') or '').strip():
        return _api_add_ai_profile(data)
    ok, model_name_or_err = _validate_ai_model_name(data.get('model_name') or '')
    if not ok:
        return jsonify({'success': False, 'error': model_name_or_err}), 400
    model_name = model_name_or_err
    verify_ollama = bool(data.get('verify_ollama'))
    if verify_ollama:
        installed, err = local_ai_service.list_installed_models()
        if err:
            return jsonify({
                'success': False,
                'error': f'无法校验 Ollama：{err}',
                'hint': '可关闭「添加前校验」后重试，或检查 LOCAL_LLM_BASE_URL 与 ollama serve',
            }), 503
        if model_name not in installed:
            return jsonify({
                'success': False,
                'error': f'Ollama 中未找到模型「{model_name}」，请先执行 ollama pull',
                'ollama_models': installed,
            }), 400
    cfg = _load_ai_model_config()
    profiles = list(cfg.get('profiles') or [])
    meta = _catalog_provider_meta('ollama')
    pid = str(uuid.uuid4())
    profiles.append({
        'id': pid,
        'provider': 'ollama',
        'api_style': meta.get('api_style') or 'ollama',
        'model_type': 'test_case_generation',
        'model_id': model_name,
        'label': model_name,
        'api_key': '',
        'base_url': '',
    })
    cfg['profiles'] = profiles
    cfg['version'] = 2
    cfg['active_profile_id'] = pid
    _save_ai_model_config(cfg)
    return jsonify({
        'success': True,
        'active_profile_id': cfg.get('active_profile_id'),
        'profiles': [_mask_profile_for_api(p) for p in profiles],
    })


def _api_add_ai_profile(data: dict):
    provider = (data.get('provider') or '').strip()
    if not provider:
        return jsonify({'success': False, 'error': 'provider不能为空'}), 400
    cmeta = _catalog_provider_meta(provider)
    if not cmeta:
        return jsonify({'success': False, 'error': f'未知提供商: {provider}'}), 400
    api_style = (data.get('api_style') or cmeta.get('api_style') or '').strip()
    if not api_style:
        return jsonify({'success': False, 'error': '无法解析 api_style'}), 400
    ok, model_id = _validate_profile_model_id(data.get('model_id') or '')
    if not ok:
        return jsonify({'success': False, 'error': model_id}), 400
    model_type = (data.get('model_type') or 'test_case_generation').strip()
    label = (data.get('label') or '').strip() or model_id
    api_key = data.get('api_key')
    api_key = api_key.strip() if isinstance(api_key, str) else ''
    base_url = (data.get('base_url') or '').strip() if isinstance(data.get('base_url'), str) else ''
    if not base_url:
        base_url = (cmeta.get('default_base_url') or '').strip()
    requires_key = bool(cmeta.get('requires_api_key'))
    if requires_key and provider != 'ollama' and not api_key:
        return jsonify({'success': False, 'error': '该提供商需要 API 密钥'}), 400
    verify_ollama = bool(data.get('verify_ollama'))
    if provider == 'ollama' and verify_ollama:
        installed, err = local_ai_service.list_installed_models()
        if err:
            return jsonify({
                'success': False,
                'error': f'无法校验 Ollama：{err}',
                'hint': '可关闭「添加前校验」后重试',
            }), 503
        if model_id not in installed:
            return jsonify({
                'success': False,
                'error': f'Ollama 中未找到模型「{model_id}」',
                'ollama_models': installed,
            }), 400
    cfg = _load_ai_model_config()
    profiles = list(cfg.get('profiles') or [])
    pid = str(uuid.uuid4())
    profiles.append({
        'id': pid,
        'provider': provider,
        'api_style': api_style,
        'model_type': model_type,
        'model_id': model_id,
        'label': label,
        'api_key': api_key,
        'base_url': base_url,
    })
    cfg['profiles'] = profiles
    cfg['version'] = 2
    cfg['active_profile_id'] = pid
    _save_ai_model_config(cfg)
    return jsonify({
        'success': True,
        'active_profile_id': cfg.get('active_profile_id'),
        'profiles': [_mask_profile_for_api(p) for p in profiles],
    })


@app.route('/api/ai/models/active', methods=['PUT'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_set_active_ai_model():
    data = request.get_json(silent=True) or {}
    profile_id = (data.get('profile_id') or '').strip()
    cfg = _load_ai_model_config()
    profiles = cfg.get('profiles') or []
    if profile_id:
        if not any(p.get('id') == profile_id for p in profiles):
            return jsonify({'success': False, 'error': '未找到该模型配置'}), 404
        cfg['active_profile_id'] = profile_id
        cfg['version'] = 2
        _save_ai_model_config(cfg)
        return jsonify({
            'success': True,
            'active_profile_id': profile_id,
            'profiles': [_mask_profile_for_api(p) for p in profiles],
        })
    ok, model_name_or_err = _validate_ai_model_name(data.get('model_name') or '')
    if not ok:
        return jsonify({'success': False, 'error': model_name_or_err}), 400
    model_name = model_name_or_err
    for p in profiles:
        if p.get('provider') == 'ollama' and p.get('model_id') == model_name:
            cfg['active_profile_id'] = p.get('id')
            cfg['version'] = 2
            _save_ai_model_config(cfg)
            return jsonify({
                'success': True,
                'active_profile_id': cfg.get('active_profile_id'),
                'profiles': [_mask_profile_for_api(x) for x in profiles],
            })
    return jsonify({'success': False, 'error': '请使用 profile_id 或已注册的 Ollama 模型名'}), 400


@app.route('/api/ai/models', methods=['DELETE'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_delete_ai_model():
    data = request.get_json(silent=True) or {}
    profile_id = (data.get('profile_id') or '').strip()
    cfg = _load_ai_model_config()
    profiles = list(cfg.get('profiles') or [])
    if profile_id:
        if not any(p.get('id') == profile_id for p in profiles):
            return jsonify({'success': False, 'error': '未找到该模型配置'}), 404
        profiles = [p for p in profiles if p.get('id') != profile_id]
        cfg['profiles'] = profiles
        cfg['version'] = 2
        if (cfg.get('active_profile_id') or '').strip() == profile_id:
            cfg['active_profile_id'] = profiles[0]['id'] if profiles else ''
        _save_ai_model_config(cfg)
        return jsonify({
            'success': True,
            'active_profile_id': cfg.get('active_profile_id'),
            'profiles': [_mask_profile_for_api(p) for p in profiles],
        })
    ok, model_name_or_err = _validate_ai_model_name(data.get('model_name') or '')
    if not ok:
        return jsonify({'success': False, 'error': model_name_or_err}), 400
    model_name = model_name_or_err
    removed = False
    new_profiles = []
    for p in profiles:
        if p.get('provider') == 'ollama' and p.get('model_id') == model_name:
            removed = True
            continue
        new_profiles.append(p)
    if not removed:
        return jsonify({'success': False, 'error': '该平台未注册此模型'}), 404
    cfg['profiles'] = new_profiles
    cfg['version'] = 2
    if (cfg.get('active_profile_id') or '').strip() and not any(
        p.get('id') == cfg.get('active_profile_id') for p in new_profiles
    ):
        cfg['active_profile_id'] = new_profiles[0]['id'] if new_profiles else ''
    _save_ai_model_config(cfg)
    return jsonify({
        'success': True,
        'active_profile_id': cfg.get('active_profile_id'),
        'profiles': [_mask_profile_for_api(p) for p in new_profiles],
    })


@app.route('/api/ai/models/profile', methods=['PUT', 'POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_update_ai_profile():
    """更新已有模型配置（换 Key、改 Base URL、同厂商下换模型名等），无需删除后重建。"""
    data = request.get_json(silent=True) or {}
    profile_id = (data.get('profile_id') or '').strip()
    if not profile_id:
        return jsonify({'success': False, 'error': '缺少 profile_id'}), 400
    cfg = _load_ai_model_config()
    profiles = list(cfg.get('profiles') or [])
    idx = next((i for i, p in enumerate(profiles) if p.get('id') == profile_id), -1)
    if idx < 0:
        return jsonify({'success': False, 'error': '未找到该模型配置'}), 404
    p = dict(profiles[idx])
    provider = (p.get('provider') or '').strip()
    cmeta = _catalog_provider_meta(provider)
    if not cmeta:
        return jsonify({'success': False, 'error': f'未知提供商: {provider}'}), 400

    if 'label' in data:
        lab = _ai_str(data.get('label'))
        if lab:
            p['label'] = lab

    if 'model_type' in data:
        mt = _ai_str(data.get('model_type'))
        if mt:
            p['model_type'] = mt

    if 'model_id' in data:
        ok, model_id = _validate_profile_model_id(data.get('model_id') or '')
        if not ok:
            return jsonify({'success': False, 'error': model_id}), 400
        p['model_id'] = model_id

    if 'base_url' in data:
        bu = data.get('base_url')
        bu = bu.strip() if isinstance(bu, str) else ''
        if not bu:
            bu = (cmeta.get('default_base_url') or '').strip()
        p['base_url'] = bu

    api_key_in = data.get('api_key')
    if isinstance(api_key_in, str) and api_key_in.strip():
        p['api_key'] = api_key_in.strip()
    elif bool(cmeta.get('requires_api_key')) and provider != 'ollama':
        existing = (p.get('api_key') or '').strip() if isinstance(p.get('api_key'), str) else ''
        if not existing:
            return jsonify({'success': False, 'error': '该提供商需要 API 密钥，请填写新的密钥'}), 400

    profiles[idx] = p
    cfg['profiles'] = profiles
    cfg['version'] = 2
    _save_ai_model_config(cfg)
    return jsonify({
        'success': True,
        'active_profile_id': cfg.get('active_profile_id'),
        'profiles': [_mask_profile_for_api(x) for x in profiles],
    })


# --- AI 后台任务：用户离开 AI 测试页后推理在服务端继续（适用于单进程/单 Gunicorn worker；多 worker 需外存队列） ---
_AI_BG_JOBS: dict = {}
_AI_BG_LOCK = threading.Lock()


def _ai_bg_prune_locked():
    now = time.time()
    dead = []
    for jid, rec in list(_AI_BG_JOBS.items()):
        st = rec.get('status')
        t_done = rec.get('t_done')
        if st in ('done', 'error', 'cancelled') and t_done and (now - t_done) > 3600:
            dead.append(jid)
        if len(dead) > 500:
            break
    for jid in dead:
        _AI_BG_JOBS.pop(jid, None)


def _ai_url_probe_disabled() -> bool:
    v = (os.environ.get("LOCAL_AI_SKIP_URL_PROBE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _ai_main_session_dom_pack(target_page_url: str, *, strict: bool):
    """
    在主 Playwright 会话中打开 URL 并抓取可交互结构，供本地模型绑定真实 DOM。
    strict=True：任一步失败则返回 (None,..., error)；strict=False：失败时记录日志并返回错误文案供告警（仍可继续生成）。
    """
    from ai_page_probe import probe_registry_from_interactive_snapshot

    url = (target_page_url or "").strip()
    if not url:
        e = "目标 URL 为空"
        return None, None, None, None, (e if strict else "")

    try:
        sync_start_browser(headless=True)
    except Exception:
        pass
    if not sync_automation_session_usable():
        e = "主浏览器未就绪，无法打开页面抓取结构。请确认 Playwright 已安装，或先在界面中启动主浏览器。"
        if strict:
            return None, None, None, None, e
        uat_logger.warning("LOCAL_AI url probe skipped: %s", e)
        return None, None, None, None, e

    try:
        sync_navigate_to(url, ai_probe=True)
    except Exception as ex:
        e = f"导航失败：{ex}"
        if strict:
            return None, None, None, None, e
        uat_logger.warning("LOCAL_AI url probe: %s", e)
        return None, None, None, None, e

    try:
        snap = sync_get_interactive_page_snapshot(150)
    except Exception as ex:
        e = f"获取页面可交互结构失败：{ex}"
        if strict:
            return None, None, None, None, e
        uat_logger.warning("LOCAL_AI url probe: %s", e)
        return None, None, None, None, e

    ps, pr, pu = probe_registry_from_interactive_snapshot(snap)
    probe_url = (pu or url).strip() or None
    return snap, ps or None, pr if pr else None, probe_url, ""


def _ai_embedded_run_script_steps_sequentially(user_id: int, embedded_sid: str, script_steps: list) -> list:
    """
    在远程画布会话中逐步调用网关 run-steps（每步单独请求），与 gateway-stream 一致，
    便于 CDP screencast 在步骤间隙刷新 JPEG，实现「边看边跑」。
    """
    results = []
    for st in script_steps:
        j, err = embedded_gateway_json(
            "POST",
            f"/internal/session/{embedded_sid}/run-steps",
            user_id=user_id,
            body={"steps": [st]},
            timeout_sec=180.0,
        )
        if err or not isinstance(j, dict) or not j.get("success"):
            results.append(
                {
                    "status": "error",
                    "step": st,
                    "error": str(err or (j or {}).get("detail") or "远程执行请求失败"),
                }
            )
            break
        rs0 = (j.get("results") or [{}])[0]
        if not rs0.get("ok"):
            results.append(
                {
                    "status": "error",
                    "step": st,
                    "error": str(rs0.get("error") or "远程步骤失败"),
                }
            )
            break
        results.append({"status": "success", "step": st})
    return results


def _merge_ai_locator_resolution(plan_dict, snap_data, norm_warnings):
    """有页面快照时：先做启发式选择器修复（默认开），再可选做 LLM 定位解析。"""
    if (
        not snap_data
        or not isinstance(plan_dict, dict)
        or not isinstance(plan_dict.get("steps"), list)
    ):
        return plan_dict, norm_warnings
    try:
        from ai_locator_resolution import ai_locator_resolve_enabled, resolve_plan_steps_locators_with_snapshot
        from ai_page_probe import heuristic_repair_plan_selectors_from_registry, probe_registry_from_interactive_snapshot

        _, registry, _ = probe_registry_from_interactive_snapshot(snap_data)
        if registry:
            repaired, hw = heuristic_repair_plan_selectors_from_registry(plan_dict["steps"], registry)
            plan_dict["steps"] = repaired
            if hw:
                norm_warnings = list(norm_warnings or []) + list(hw)

        if ai_locator_resolve_enabled():
            new_steps, lw = resolve_plan_steps_locators_with_snapshot(plan_dict["steps"], snap_data)
            plan_dict["steps"] = new_steps
            if lw:
                norm_warnings = list(norm_warnings or []) + list(lw)
    except Exception as e:
        uat_logger.warning("AI locator merge skipped: %s", e)
    return plan_dict, norm_warnings


def _execute_ai_task_plan(data: dict, user_id: int, username: str, remote_addr):
    """规划逻辑（供同步 API 与后台线程共用）。返回体可含 _http 表示建议 HTTP 状态码。"""
    from ai_page_probe import probe_registry_from_interactive_snapshot

    data = data or {}
    task_type = (data.get('task_type') or 'test_case_generation').strip()
    goal = (data.get('goal') or '').strip()
    project_name = (data.get('project_name') or '').strip()
    selected_model = (data.get('model') or '').strip() or _get_active_local_model()
    profile, legacy_model = _resolve_inference_profile(selected_model)

    if not goal:
        return {'success': False, 'error': 'goal不能为空', '_http': 400}

    route = _route_ai_model(task_type)
    if route['provider'] != 'local':
        return {
            'success': False,
            'error': '该任务需走云端分析接口，请调用 /api/ai/task/cloud-analyze',
            '_http': 400,
        }

    embedded_sid = (data.get('embedded_session_id') or data.get('remote_session_id') or '').strip()
    target_page_url = (data.get('target_page_url') or '').strip()
    execution_mode = (data.get('execution_mode') or '').strip().lower()
    run_execute = execution_mode in ('run', 'run_and_record', 'execute')

    page_snapshot = None
    probe_registry = None
    probe_url = (target_page_url or None) if not embedded_sid else None
    snap_data = None
    dom_probe_warning = ""

    if embedded_sid:
        if not embedded_gateway_enabled():
            return {'success': False, 'error': '远程 Chromium 网关未配置', '_http': 503}
        j, err = embedded_gateway_json(
            'GET',
            f'/internal/session/{embedded_sid}/inspect',
            user_id=user_id,
        )
        if not j or not j.get('success'):
            detail = (j or {}).get('detail')
            return {
                'success': False,
                'error': str(err or detail or '无法获取远程页结构，请确认远程画布已连接'),
                '_http': 502,
            }
        snap = j.get('data') or {}
        snap_data = snap
        ps, pr, pu = probe_registry_from_interactive_snapshot(snap)
        page_snapshot = ps or None
        probe_registry = pr if pr else None
        probe_url = (pu or target_page_url).strip() or None
    elif run_execute:
        if not target_page_url:
            return {
                'success': False,
                'error': '「运行」模式需要填写目标页面 URL，以便在主 Playwright 会话中打开页面并执行步骤。',
                '_http': 400,
            }
        snap_data, page_snapshot, probe_registry, probe_url, err = _ai_main_session_dom_pack(
            target_page_url, strict=True
        )
        if err:
            code = 400 if ("主浏览器未就绪" in err or "目标 URL 为空" in err) else 500
            return {'success': False, 'error': err, '_http': code}
    elif (target_page_url or "").strip() and not embedded_sid and not _ai_url_probe_disabled():
        # 「仅规划」也抓取 LIVE：只要填写了目标 URL，就为主会话打开该页并探测，避免模型凭空写选择器。
        snap_data, page_snapshot, probe_registry, probe_url, err = _ai_main_session_dom_pack(
            target_page_url, strict=False
        )
        if err:
            dom_probe_warning = (
                "未能抓取 LIVE 页面结构，选择器将未经页面探测约束："
                f"{err} "
                "（可配置 Playwright / 主浏览器，或使用「运行生成」强制探测。设置 LOCAL_AI_SKIP_URL_PROBE=1 可关闭自动打开浏览器。）"
            )

    dpack = _ai_build_dom_pack(snap_data, embed_remote=bool(embedded_sid)) if snap_data else ""
    mem_ctx = _ai_memory_context_block(
        user_id, goal, probe_url=probe_url or target_page_url or "", project_name=project_name
    )
    try:
        generated = local_ai_service.generate_case_and_steps(
            goal,
            project_name,
            model=legacy_model,
            profile=profile,
            page_snapshot=page_snapshot,
            probe_registry=probe_registry,
            probe_url=probe_url,
            memory_context=mem_ctx or None,
            dom_context_pack=dpack or None,
        )
    except ValueError as e:
        return {
            'success': False,
            'error': str(e),
            'hint': '可先执行: ollama serve，并确认模型已拉取。',
            '_http': 503,
        }
    except Exception as e:
        uat_logger.exception('ai plan execute failed')
        return {'success': False, 'error': str(e), '_http': 500}

    generated, norm_warnings = apply_step_normalization_to_plan(generated)
    generated, norm_warnings = _merge_ai_locator_resolution(generated, snap_data, norm_warnings)
    if dom_probe_warning:
        norm_warnings = [dom_probe_warning] + list(norm_warnings or [])
    log_ai_plan_to_audit(
        user_id,
        username,
        'AI_PLAN_GENERATE',
        generated,
        remote_addr,
    )
    out: dict = {
        'success': True,
        'provider': route['provider'],
        'model': generated.get('meta', {}).get('model') or route['model'],
        'plan': generated,
        'warnings': norm_warnings,
    }
    if run_execute:
        try:
            script_steps = ai_plan_steps_to_playwright_script_steps(generated.get('steps') or [])
            gate_url = (target_page_url or "").strip() or (generated.get("case_url") or "").strip()
            if (
                gate_url.startswith(("http://", "https://"))
                and script_steps
                and script_steps[0].get("action") != "navigate"
            ):
                script_steps = [
                    {"action": "navigate", "url": gate_url, "description": "（运行）先打开目标页"},
                ] + script_steps
            max_n = int(os.environ.get("AI_AGENT_GATEWAY_MAX_STEPS", "40") or 40)
            script_steps = script_steps[: max(1, max_n)]
            if not script_steps:
                out["execution"] = {
                    "ran": False,
                    "skipped_reason": "无可映射为脚本的原子步骤（请检查 navigate/选择器等）。",
                    "results": [],
                }
            elif embedded_sid:
                if not embedded_gateway_enabled():
                    out["execution"] = {
                        "ran": False,
                        "skipped_reason": "远程 Chromium 网关未配置，无法在内置画布会话中执行。",
                        "results": [],
                    }
                else:
                    exec_results = _ai_embedded_run_script_steps_sequentially(
                        user_id, embedded_sid, script_steps
                    )
                    out["execution"] = {"ran": True, "results": exec_results, "via": "embedded"}
            else:
                exec_results = sync_execute_script_steps(script_steps)
                out["execution"] = {"ran": True, "results": exec_results, "via": "main_playwright"}
        except Exception as e:
            uat_logger.exception("ai run-and-record execution")
            out["execution"] = {"ran": True, "error": str(e), "results": []}
    return out


def _execute_ai_task_chat(data: dict, user_id: int, username: str, remote_addr):
    """多轮优化逻辑（供同步 API 与后台线程共用）。"""
    from ai_page_probe import probe_registry_from_interactive_snapshot

    data = data or {}
    message = (data.get('message') or '').strip()
    project_name = (data.get('project_name') or '').strip()
    current_plan = data.get('current_plan') or {}
    history = data.get('history') or []
    selected_model = (data.get('model') or '').strip() or _get_active_local_model()
    profile, legacy_model = _resolve_inference_profile(selected_model)
    if not message:
        return {'success': False, 'error': 'message不能为空', '_http': 400}

    route = _route_ai_model('test_case_generation')
    from ai_chat_tool_loop import ai_chat_tools_enabled, profile_supports_ai_chat_tools

    _ai_chat_tools_on = ai_chat_tools_enabled() and profile_supports_ai_chat_tools(profile, legacy_model)
    if route['provider'] != 'local' and not _ai_chat_tools_on:
        return {'success': False, 'error': '当前仅支持本地模型对话', '_http': 400}

    embedded_sid = (data.get('embedded_session_id') or data.get('remote_session_id') or '').strip()
    target_page_url = (data.get('target_page_url') or '').strip()
    page_snapshot = None
    probe_registry = None
    probe_url = (target_page_url or None) if not embedded_sid else None
    snap_data = None
    chat_dom_probe_warning = ""

    if embedded_sid:
        if not embedded_gateway_enabled():
            return {'success': False, 'error': '远程 Chromium 网关未配置', '_http': 503}
        j, err = embedded_gateway_json(
            'GET',
            f'/internal/session/{embedded_sid}/inspect',
            user_id=user_id,
        )
        if not j or not j.get('success'):
            detail = (j or {}).get('detail')
            return {
                'success': False,
                'error': str(err or detail or '无法获取远程页结构'),
                '_http': 502,
            }
        snap = j.get('data') or {}
        snap_data = snap
        ps, pr, pu = probe_registry_from_interactive_snapshot(snap)
        page_snapshot = ps or None
        probe_registry = pr if pr else None
        probe_url = (pu or target_page_url).strip() or None

    url_for_probe = (target_page_url or "").strip()
    if not url_for_probe and isinstance(current_plan, dict):
        url_for_probe = (current_plan.get("case_url") or "").strip()
    if snap_data is None and url_for_probe and not embedded_sid and not _ai_url_probe_disabled():
        snap_data, page_snapshot, probe_registry, probe_url, err = _ai_main_session_dom_pack(
            url_for_probe, strict=False
        )
        if err:
            chat_dom_probe_warning = (
                "未能抓取 LIVE 页面结构（对话优化）："
                f"{err} "
                "（选择器约束可能较弱；请填写目标 URL 或确认主浏览器可用。）"
            )

    dpack = _ai_build_dom_pack(snap_data, embed_remote=bool(embedded_sid)) if snap_data else ""
    mem_ctx = _ai_memory_context_block(
        user_id, message, probe_url=probe_url or url_for_probe or target_page_url or "", project_name=project_name
    )
    interaction_context = None
    _ic: dict = {}
    if data.get('focus_step_index') is not None and str(data.get('focus_step_index', '')).strip() != '':
        _ic['focus_step_index'] = data.get('focus_step_index')
    _fi = data.get('focus_step_indices')
    if isinstance(_fi, list) and _fi:
        _ic['focus_step_indices'] = _fi
    _sel = (data.get('browser_selection_text') or data.get('selection_text') or '').strip()
    if _sel:
        _ic['browser_selection_text'] = _sel
    _kind = (data.get('action_kind') or data.get('intent') or '').strip()
    if _kind:
        _ic['action_kind'] = _kind
    if _ic:
        interaction_context = _ic

    tool_meta_extra = None
    try:
        if _ai_chat_tools_on:
            from ai_chat_tool_loop import ChatToolLoopParams, run_ai_chat_with_tools

            _ctp = ChatToolLoopParams(
                message=message,
                project_name=project_name,
                current_plan=current_plan if isinstance(current_plan, dict) else {},
                history=history,
                profile=profile,
                legacy_model=legacy_model,
                page_snapshot=page_snapshot,
                probe_registry=probe_registry,
                probe_url=probe_url,
                memory_context=mem_ctx or None,
                dom_context_pack=dpack or None,
                interaction_context=interaction_context,
                test_scope=(data.get('test_scope') or data.get('scope') or '').strip() or None,
            )
            try:
                generated, _, tool_meta_extra = run_ai_chat_with_tools(
                    local_ai_service=local_ai_service,
                    params=_ctp,
                )
            except (ValueError, TypeError, RuntimeError) as loop_err:
                uat_logger.warning('AI chat tool loop failed, fallback to refine: %s', loop_err)
                tool_meta_extra = {'fallback': 'refine_after_loop_error', 'error': str(loop_err)}
                generated = local_ai_service.refine_case_and_steps(
                    user_message=message,
                    project_name=project_name,
                    current_plan=current_plan if isinstance(current_plan, dict) else {},
                    history=history if isinstance(history, list) else [],
                    model=legacy_model,
                    profile=profile,
                    page_snapshot=page_snapshot,
                    probe_registry=probe_registry,
                    probe_url=probe_url,
                    memory_context=mem_ctx or None,
                    dom_context_pack=dpack or None,
                    interaction_context=interaction_context,
                )
        else:
            generated = local_ai_service.refine_case_and_steps(
                user_message=message,
                project_name=project_name,
                current_plan=current_plan if isinstance(current_plan, dict) else {},
                history=history if isinstance(history, list) else [],
                model=legacy_model,
                profile=profile,
                page_snapshot=page_snapshot,
                probe_registry=probe_registry,
                probe_url=probe_url,
                memory_context=mem_ctx or None,
                dom_context_pack=dpack or None,
                interaction_context=interaction_context,
            )
    except ValueError as e:
        msg = str(e)
        low = msg.lower()
        hint = (
            '可先执行 ollama serve，并确认 ollama pull 过所选模型。'
            '若仅在「改步骤」时报错而列表正常，多半是推理接口慢或模型不合适，而非「没启动」。'
        )
        if 'read timed out' in low or 'timed out' in low:
            hint = (
                '本次为推理超时：界面能列出模型仅代表 Ollama 在线；改写步骤会发送长文本，耗时可远大于列表。'
                '建议①在 /ai-test 选用纯文本 instruct 模型（尽量避免名称含 -vl 的视觉模型）；'
                '②终端执行 ollama run <模型名> 预热；③设置环境变量 LOCAL_LLM_TIMEOUT_CHAT（仅影响生成/对话 POST）'
                '或 LOCAL_LLM_TIMEOUT 后重启 HuFirst（未设置时 /api/chat 默认读超时 600 秒）；④确认机器算力与显存足够。'
            )
        return {
            'success': False,
            'error': msg,
            'hint': hint,
            '_http': 503,
        }
    except Exception as e:
        uat_logger.exception('ai chat execute failed')
        return {'success': False, 'error': str(e), '_http': 500}

    generated, norm_warnings = apply_step_normalization_to_plan(generated)
    generated, norm_warnings = _merge_ai_locator_resolution(generated, snap_data, norm_warnings)
    if chat_dom_probe_warning:
        norm_warnings = [chat_dom_probe_warning] + list(norm_warnings or [])
    log_ai_plan_to_audit(
        user_id,
        username,
        'AI_PLAN_REFINE',
        generated,
        remote_addr,
    )
    out = {
        'success': True,
        'provider': 'local',
        'model': generated.get('meta', {}).get('model') or route['model'],
        'plan': generated,
        'warnings': norm_warnings,
    }
    if tool_meta_extra:
        out['chat_tools'] = tool_meta_extra
    return out


def _start_ai_bg_job_thread(job_id: str, kind: str, data: dict, user_id: int, username: str, remote_addr):
    data_copy = dict(data)

    def _runner():
        out = {'success': False, 'error': 'unknown', '_http': 500}
        try:
            if kind == 'plan':
                out = _execute_ai_task_plan(data_copy, user_id, username, remote_addr)
            elif kind == 'chat':
                out = _execute_ai_task_chat(data_copy, user_id, username, remote_addr)
            else:
                out = {'success': False, 'error': 'unknown job kind', '_http': 400}
        except Exception as ex:
            uat_logger.exception('ai bg job %s', job_id)
            out = {'success': False, 'error': str(ex), '_http': 500}
        with _AI_BG_LOCK:
            rec = _AI_BG_JOBS.get(job_id)
            if not rec:
                return
            if rec.get('cancelled'):
                rec['status'] = 'cancelled'
                rec['t_done'] = time.time()
                return
            body = {k: v for k, v in out.items() if k != '_http'}
            rec['http_status'] = int(out.get('_http', 200))
            rec['result'] = body
            rec['status'] = 'done' if body.get('success') else 'error'
            if not body.get('success'):
                rec['error'] = body.get('error') or 'error'
            rec['t_done'] = time.time()

    threading.Thread(target=_runner, daemon=True, name='ai-bg-' + job_id[:8]).start()


@app.route('/api/ai/task/plan', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_ai_task_plan():
    """
    AI任务规划入口（本地模型真实推理）。
    """
    data = request.get_json(silent=True) or {}
    out = _execute_ai_task_plan(
        data,
        current_user.id,
        current_user.username,
        request.remote_addr,
    )
    code = int(out.get('_http', 200))
    body = {k: v for k, v in out.items() if k != '_http'}
    return jsonify(body), code


@app.route('/api/ai/task/cloud-analyze', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_ai_task_cloud_analyze():
    """
    云端高复杂任务入口（脚本修复/复杂报错分析）。
    铁律：任何上云内容必须先经过自动全量脱敏。
    """
    data = request.get_json(silent=True) or {}
    task_type = (data.get('task_type') or '').strip()
    payload = data.get('payload')

    if task_type not in {'script_repair', 'complex_error_analysis'}:
        return jsonify({'success': False, 'error': '仅支持 script_repair / complex_error_analysis'}), 400
    if payload is None:
        return jsonify({'success': False, 'error': 'payload不能为空'}), 400

    route = _route_ai_model(task_type)
    if route['provider'] != 'cloud':
        return jsonify({'success': False, 'error': '该任务不需要云端模型'}), 400

    gateway = _get_cloud_llm_gateway()
    if gateway is None:
        return jsonify({
            'success': False,
            'error': '云端模型未配置，请设置 CLOUD_LLM_ENDPOINT 和 CLOUD_LLM_API_KEY'
        }), 400

    result = gateway.call({
        'task_type': task_type,
        'payload': payload,
        'request_meta': {
            'requested_by': current_user.username,
            'requested_at': beijing_now_iso(),
        }
    })
    try:
        from ai_memory_store import ingest_repair_case, memory_enabled

        if memory_enabled():
            _dbm = Database()
            tidm = _dbm.get_user_tenant_id(current_user.id)
            ingest_repair_case(
                current_user.id,
                task_type,
                payload,
                cloud_result=result,
                tenant_id=tidm,
            )
    except Exception as e:
        uat_logger.debug("ai memory ingest skipped: %s", e)

    return jsonify({
        'success': True,
        'provider': route['provider'],
        'model': route['model'],
        'result': result
    })


@app.route('/api/ai/memory/ingest', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_ai_memory_ingest():
    """手工写入用户习惯/备注，进入本地向量记忆（需 LOCAL_MEMORY_ENABLE=1 且已拉取 LOCAL_EMBED_MODEL）。"""
    data = request.get_json(silent=True) or {}
    kind = (data.get('kind') or 'habit').strip()[:64]
    text = (data.get('text') or data.get('source_text') or '').strip()
    meta = data.get('meta')
    if not text or len(text) < 4:
        return jsonify({'success': False, 'error': 'text 过短或为空'}), 400
    try:
        from ai_memory_store import ingest, memory_enabled

        if not memory_enabled():
            return jsonify({'success': False, 'error': '未开启：设置 LOCAL_MEMORY_ENABLE=1'}), 400
        _db = Database()
        tid = _db.get_user_tenant_id(current_user.id)
        mid = ingest(
            current_user.id,
            kind,
            text,
            tenant_id=tid,
            meta=meta if isinstance(meta, dict) else None,
        )
        return jsonify({'success': True, 'id': mid})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 502


@app.route('/api/ai/scenarios/from-requirements', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_ai_structured_scenarios():
    """
    从需求说明书文本生成半结构化测试场景（无 UI 定位），便于评审后再生成自动化步骤。
    支持 JSON body：requirements_text / extra_context / model；
    或 multipart：字段 file=说明书（.txt/.md/.pdf/.docx 等）。
    """
    requirements_text = ''
    extra_context = ''
    selected_model = ''
    file_warns: List[str] = []

    if request.files and request.files.get('file'):
        f = request.files['file']
        raw = f.read() or b''
        fname = (f.filename or 'upload.txt').strip()
        from requirements_document_extract import extract_text_from_bytes

        requirements_text, file_warns = extract_text_from_bytes(fname, raw)
        requirements_text = (requirements_text or '').strip()
    else:
        data = request.get_json(silent=True) or {}
        requirements_text = (data.get('requirements_text') or data.get('text') or '').strip()
        extra_context = (data.get('extra_context') or '').strip()
        selected_model = (data.get('model') or '').strip()

    if not requirements_text:
        return jsonify({'success': False, 'error': 'requirements_text 为空或未上传可解析文件'}), 400

    mid = selected_model or _get_active_local_model()
    profile, _legacy = _resolve_inference_profile(mid)
    route = _route_ai_model('test_case_generation')
    try:
        from ai_structured_scenarios import (
            generate_structured_scenarios_from_requirements,
            generate_structured_scenarios_from_requirements_chunked,
            structured_scenarios_chunk_size,
        )

        if len(requirements_text) > int(structured_scenarios_chunk_size() * 1.15):
            doc, w0 = generate_structured_scenarios_from_requirements_chunked(
                requirements_text, profile, extra_context=extra_context
            )
        else:
            doc, w0 = generate_structured_scenarios_from_requirements(
                requirements_text, profile, extra_context=extra_context
            )
    except Exception as e:
        uat_logger.exception('structured scenarios failed')
        return jsonify({'success': False, 'error': str(e)}), 500

    warns = list(file_warns) + list(w0)
    return jsonify(
        {
            'success': True,
            'provider': route.get('provider'),
            'model': route.get('model'),
            'document': doc,
            'warnings': warns,
        }
    )


@app.route('/api/ai/locator/resolve-preview', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_ai_locator_resolve_preview():
    """打开 URL、抓取交互快照并对 steps 执行「执行前定位解析」（不写入数据库）。"""
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    steps = data.get('steps')
    if not url:
        return jsonify({'success': False, 'error': 'url 不能为空'}), 400
    if not isinstance(steps, list):
        return jsonify({'success': False, 'error': 'steps 须为数组'}), 400

    snap_data, _ps, _pr, _pu, err = _ai_main_session_dom_pack(url, strict=True)
    if err:
        code = 400 if ('目标 URL' in err or '主浏览器未就绪' in err) else 502
        return jsonify({'success': False, 'error': err}), code

    try:
        from ai_locator_resolution import resolve_plan_steps_locators_with_snapshot

        resolved, warns = resolve_plan_steps_locators_with_snapshot(steps, snap_data, force=True)
    except Exception as e:
        uat_logger.exception('locator resolve-preview failed')
        return jsonify({'success': False, 'error': str(e)}), 500

    meta = {}
    if isinstance(snap_data, dict):
        meta = {
            'title': snap_data.get('title'),
            'url': snap_data.get('url'),
            'item_count': len(snap_data.get('items') or []),
        }

    return jsonify({'success': True, 'steps': resolved, 'warnings': warns, 'snapshot_meta': meta})


def _normalize_locator_candidates_for_db(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(val)


@app.route('/api/cases/<int:case_id>/ai/locator-resolve-save', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_case_ai_locator_resolve_save(case_id: int):
    """
    对用例已有步骤：打开解析 URL、抓取 DOM 快照，将定位解析结果写回 test_steps（selector / locator_candidates）。
    """
    _db = Database()
    case = _db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'success': False, 'error': '测试用例不存在'}), 404
    if _app_case_type(case) != 'ui':
        return jsonify({'success': False, 'error': '仅支持 Web 用例'}), 400
    if case.get('project_id') and not _db.check_project_access(current_user.id, case['project_id'], 'editor'):
        return jsonify({'success': False, 'error': '无权限修改此用例'}), 403

    steps_db = _db.get_case_steps(case_id)
    steps_db = sorted(steps_db, key=lambda x: int(x.get('step_order') or 0))
    if not steps_db:
        return jsonify({'success': False, 'error': '该用例没有步骤'}), 400

    nav_url, _src = _resolve_case_navigation_url(case=case, steps=steps_db)
    if not nav_url:
        return jsonify({'success': False, 'error': '无法解析用于探测的 URL（请设置用例 URL 或步骤中的 navigate/url）'}), 400

    snap_data, _ps, _pr, _pu, err = _ai_main_session_dom_pack(nav_url, strict=True)
    if err:
        code = 400 if ('目标 URL' in err or '主浏览器未就绪' in err) else 502
        return jsonify({'success': False, 'error': err}), code

    plan = []
    for s in steps_db:
        plan.append(
            {
                'action': s.get('action'),
                'selector_type': s.get('selector_type'),
                'selector_value': s.get('selector_value'),
                'input_value': s.get('input_value'),
                'description': s.get('description'),
                'url': s.get('url'),
            }
        )

    try:
        from ai_locator_resolution import resolve_plan_steps_locators_with_snapshot

        resolved, warns = resolve_plan_steps_locators_with_snapshot(plan, snap_data, force=True)
    except Exception as e:
        uat_logger.exception('locator resolve-save failed')
        return jsonify({'success': False, 'error': str(e)}), 500

    updated = 0
    for i, s in enumerate(steps_db):
        if i >= len(resolved) or not isinstance(resolved[i], dict):
            break
        r = resolved[i]
        lc_new = _normalize_locator_candidates_for_db(r.get('locator_candidates'))
        lc_old = _normalize_locator_candidates_for_db(s.get('locator_candidates'))
        sv_new = (r.get('selector_value') or '').strip()
        sv_old = (s.get('selector_value') or '').strip()
        st_new = (r.get('selector_type') or 'css').strip()
        st_old = (s.get('selector_type') or 'css').strip()
        if sv_new == sv_old and st_new == st_old and (lc_new or '') == (lc_old or ''):
            continue
        kwargs = {'selector_type': st_new, 'selector_value': sv_new}
        if lc_new is not None:
            kwargs['locator_candidates'] = lc_new
        if _db.update_test_step(int(s['id']), **kwargs):
            updated += 1

    return jsonify({'success': True, 'case_id': case_id, 'updated_steps': updated, 'warnings': warns})


@app.route('/api/cases/<int:case_id>/steps/bulk-patch', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_case_steps_bulk_patch(case_id: int):
    """批量按 step id 更新字段（id 必须属于该 case_id）。"""
    data = request.get_json(silent=True) or {}
    items = data.get('steps')
    if not isinstance(items, list) or not items:
        return jsonify({'success': False, 'error': 'steps 须为非空数组'}), 400

    _db = Database()
    case = _db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'success': False, 'error': '测试用例不存在'}), 404
    if case.get('project_id') and not _db.check_project_access(current_user.id, case['project_id'], 'editor'):
        return jsonify({'success': False, 'error': '无权限修改此用例'}), 403

    allowed = {
        'action',
        'selector_type',
        'selector_value',
        'input_value',
        'description',
        'step_order',
        'enter_iframe',
        'iframe_selector',
        'compare_type',
        'locator_candidates',
        'click_repeat_count',
        'api_spec',
        'url',
    }

    patched = 0
    errors: List[str] = []
    for it in items[:500]:
        if not isinstance(it, dict):
            continue
        try:
            sid = int(it.get('id'))
        except (TypeError, ValueError):
            errors.append('跳过：缺少有效 id')
            continue
        row = _db.get_test_step(sid)
        if not row or int(row.get('case_id') or 0) != int(case_id):
            errors.append(f'步骤 {sid} 不属于该用例')
            continue
        kwargs: Dict[str, Any] = {}
        for k in allowed:
            if k not in it:
                continue
            v = it[k]
            if k == 'locator_candidates' and isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            if k == 'api_spec' and isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            kwargs[k] = v
        if not kwargs:
            continue
        if _db.update_test_step(sid, **kwargs):
            patched += 1

    return jsonify({'success': True, 'case_id': case_id, 'patched': patched, 'errors': errors})


@app.route('/api/ai/cases/from-scenarios-batch', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('CREATE_CASE', 'case')
def api_ai_cases_from_scenarios_batch():
    """
    将结构化场景列表批量转为 Web 用例并落库；用例 description 前缀 [REQ:场景id] 便于追溯。
    Body: project_id, document 或 scenarios, model?, project_name?, base_url? , max_scenarios? (默认20)
    """
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    selected_model = (data.get('model') or '').strip() or _get_active_local_model()
    profile, legacy_model = _resolve_inference_profile(selected_model)
    project_name = (data.get('project_name') or '').strip()
    base_url = (data.get('base_url') or '').strip()

    try:
        max_n = int(data.get('max_scenarios') or 20)
    except (TypeError, ValueError):
        max_n = 20
    max_n = max(1, min(max_n, 40))

    if not project_id:
        return jsonify({'success': False, 'error': 'project_id不能为空'}), 400

    doc = data.get('document') if isinstance(data.get('document'), dict) else {}
    scenarios = data.get('scenarios')
    if not isinstance(scenarios, list):
        scenarios = doc.get('scenarios') if isinstance(doc.get('scenarios'), list) else []
    if not scenarios:
        return jsonify({'success': False, 'error': 'scenarios 为空（请传入 document.scenarios 或顶层 scenarios）'}), 400

    _db = Database()
    if not _db.check_project_access(current_user.id, project_id, 'editor'):
        return jsonify({'success': False, 'error': '无权限在此项目创建用例'}), 403

    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    created_ids: List[int] = []
    all_warnings: List[str] = []

    for sc in scenarios[:max_n]:
        if not isinstance(sc, dict):
            continue
        sid = _ai_str(sc.get('id')) or f"auto_{len(created_ids)+1}"
        title = _ai_str(sc.get('title')) or f"场景 {sid}"
        pre = sc.get('preconditions') or []
        hs = sc.get('high_level_steps') or []
        er = sc.get('expected_results') or []
        if isinstance(pre, str):
            pre = [pre]
        if isinstance(hs, str):
            hs = [hs]
        if isinstance(er, str):
            er = [er]
        pre_t = "\n".join(str(x) for x in pre if str(x).strip())
        hs_t = "\n".join(f"- {x}" for x in hs if str(x).strip())
        er_t = "\n".join(str(x) for x in er if str(x).strip())
        goal = (
            f"测试场景：{title}\n"
            f"场景标识：{sid}\n"
            + (f"前置条件：\n{pre_t}\n\n" if pre_t else "")
            + (f"步骤概要：\n{hs_t}\n\n" if hs_t else "")
            + (f"期望：\n{er_t}" if er_t else "")
        ).strip()
        desc = f"[REQ:{sid}] {title}"
        if len(desc) > 3900:
            desc = desc[:3897] + "..."

        current_case_count = _db.get_project_case_count(project_id)
        if limits['max_cases_per_project'] != -1 and current_case_count >= limits['max_cases_per_project']:
            all_warnings.append(f"已达项目用例上限，停止在 {len(created_ids)} 条")
            break

        mem_ctx = _ai_memory_context_block(current_user.id, goal, probe_url=base_url, project_name=project_name)
        try:
            generated = local_ai_service.generate_case_and_steps(
                goal,
                project_name,
                model=legacy_model,
                profile=profile,
                memory_context=mem_ctx or None,
            )
        except ValueError as e:
            all_warnings.append(f"场景 {sid} 跳过：{e}")
            continue

        if license_info.license_type == LicenseType.FREE.value:
            _db.increment_created_cases(current_user.id)

        if base_url and not (generated.get('case_url') or '').strip():
            generated['case_url'] = base_url

        local_ai_service._fill_missing_step_payloads(
            generated.get('steps') or [],
            goal,
            _ai_str(generated.get('case_url')),
            None,
        )
        generated, norm_warns = apply_step_normalization_to_plan(generated)
        all_warnings.extend(norm_warns)

        case_id = _db.create_test_case_v2(
            project_id,
            (title[:200] if title else f"AI-{sid}")[:200],
            generated.get('case_url', ''),
            desc,
            pre_t[:2000] if pre_t else generated.get('precondition', ''),
            er_t[:2000] if er_t else generated.get('expected_result', ''),
        )
        steps = generated.get('steps') or []
        for idx, step in enumerate(steps, start=1):
            _db.create_test_step(**_ai_step_to_db_kwargs(step, case_id, idx))
        log_ai_plan_to_audit(
            current_user.id,
            current_user.username,
            'AI_PLAN_SCENARIO_BATCH',
            {**generated, 'scenario_id': sid},
            request.remote_addr,
        )
        created_ids.append(case_id)

    return jsonify(
        {
            'success': True,
            'created_case_ids': created_ids,
            'count': len(created_ids),
            'warnings': all_warnings,
        }
    )


@app.route('/api/ai/import/api-spec/preview', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_ai_import_api_spec_preview():
    """解析 OpenAPI / Postman JSON，返回可导入的请求列表预览。"""
    base_url = ''
    content = ''
    if request.files and request.files.get('file'):
        f = request.files['file']
        content = (f.read() or b'').decode('utf-8', errors='replace')
    else:
        data = request.get_json(silent=True) or {}
        content = (data.get('content') or data.get('text') or '').strip()
        base_url = (data.get('base_url') or data.get('server_url') or '').strip()

    from api_doc_import import detect_and_parse_api_doc

    kind, items, warns = detect_and_parse_api_doc(content, base_url_override=base_url)
    if kind == 'unknown':
        return jsonify({'success': False, 'error': (warns or ['无法解析'])[0], 'warnings': warns}), 400
    return jsonify({'success': True, 'kind': kind, 'items': items[:500], 'warnings': warns})


@app.route('/api/ai/import/api-spec/commit', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('CREATE_CASE', 'case')
def api_ai_import_api_spec_commit():
    """将预览项写入一个接口用例（多 api_request 步骤）。"""
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    case_name = (data.get('case_name') or '').strip() or '导入的接口用例'
    items = data.get('items')
    if not project_id:
        return jsonify({'success': False, 'error': 'project_id 不能为空'}), 400
    if not isinstance(items, list) or not items:
        return jsonify({'success': False, 'error': 'items 须为非空数组'}), 400

    _db = Database()
    if not _db.check_project_access(current_user.id, project_id, 'editor'):
        return jsonify({'success': False, 'error': '无权限在此项目创建用例'}), 403

    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    current_case_count = _db.get_project_case_count(project_id)
    if limits['max_cases_per_project'] != -1 and current_case_count >= limits['max_cases_per_project']:
        return jsonify({'success': False, 'error': '已达到项目用例数量限制', 'limit_reached': True}), 403
    if license_info.license_type == LicenseType.FREE.value:
        _db.increment_created_cases(current_user.id)

    desc = (data.get('description') or '').strip()[:4000]
    case_id = _db.create_test_case_v2(
        project_id,
        case_name[:200],
        '',
        desc,
        '',
        '',
        case_type='api',
    )
    n = 0
    for i, it in enumerate(items[:300], start=1):
        if not isinstance(it, dict):
            continue
        spec = it.get('api_spec')
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError:
                continue
        if not isinstance(spec, dict):
            continue
        api_json = json.dumps(spec, ensure_ascii=False)
        desc_step = (it.get('description') or it.get('name') or f"请求{i}")[:2000]
        _db.create_test_step(
            case_id,
            'api_request',
            '',
            '',
            '',
            desc_step,
            i,
            api_spec=api_json,
        )
        n += 1

    if n == 0:
        try:
            _db.delete_test_case(case_id)
        except Exception:
            pass
        return jsonify({'success': False, 'error': '没有可写入的有效 api_spec 项'}), 400

    return jsonify({'success': True, 'case_id': case_id, 'steps_created': n})


@app.route('/api/ai/cases/import-ui-plan', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_ai_cases_import_ui_plan():
    """
    将外部工具（如 OpenClaw / 工具循环）产出的步骤 JSON 写入用例：可选 replace 清空原步骤后写入。
    Body: case_id, steps[], replace? (默认 false 为追加)
    """
    data = request.get_json(silent=True) or {}
    case_id = data.get('case_id')
    steps = data.get('steps') or []
    replace = bool(data.get('replace'))
    if not case_id:
        return jsonify({'success': False, 'error': 'case_id 不能为空'}), 400
    if not isinstance(steps, list) or not steps:
        return jsonify({'success': False, 'error': 'steps 须为非空数组'}), 400

    case = db.get_test_case_v2(int(case_id))
    if not case:
        return jsonify({'success': False, 'error': '测试用例不存在'}), 404
    _db = Database()
    if case.get('project_id') and not _db.check_project_access(current_user.id, case['project_id'], 'editor'):
        return jsonify({'success': False, 'error': '无权限修改此用例'}), 403
    if _app_case_type(case) != 'ui':
        return jsonify({'success': False, 'error': '仅支持 Web 用例'}), 400

    goal_hint = _ai_str(data.get('goal')) or _ai_str(case.get('name')) or ''
    local_ai_service._fill_missing_step_payloads(
        steps,
        goal_hint,
        _ai_str(case.get('url')),
        None,
    )
    clean_steps, warnings = dedupe_and_validate_ai_steps(steps)
    clean_steps = [s for s in clean_steps if (s.get('action') or '').strip().lower() != 'api_request']
    if not clean_steps:
        return jsonify({'success': False, 'error': '没有可写入的 Web 步骤', 'warnings': warnings}), 400

    if replace:
        _db.delete_case_steps(int(case_id))
        start_order = 0
    else:
        old_steps, _t = _db.get_case_steps_paginated(int(case_id), 1, 2000)
        start_order = max([int(s.get('step_order') or 0) for s in old_steps] or [0])

    created = 0
    for idx, step in enumerate(clean_steps, start=1):
        _db.create_test_step(**_ai_step_to_db_kwargs(step, int(case_id), start_order + idx))
        created += 1

    return jsonify({'success': True, 'case_id': int(case_id), 'steps_created': created, 'warnings': warnings})


@app.route('/api/ai/diagnostics/failure-bundle', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_ai_failure_diag_bundle():
    """
    在当前主 Playwright 页面采集失败诊断包，并可选生成结构化缺陷草稿（LLM）。
    Body: { "failed_step": {...}, "exception_message": "..." }
    """
    data = request.get_json(silent=True) or {}
    failed_step = data.get('failed_step') if isinstance(data.get('failed_step'), dict) else {}
    exception_message = (data.get('exception_message') or data.get('error') or '').strip()

    try:
        from playwright_automation import sync_automation_session_usable, sync_gather_failure_signals

        if not sync_automation_session_usable():
            return jsonify({'success': False, 'error': '主浏览器未就绪，无法采集页面信号'}), 503
        signals = sync_gather_failure_signals()
    except Exception as e:
        uat_logger.warning('failure-bundle signals: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500

    try:
        from execution_diag_bundle import (
            build_failure_bundle,
            classify_failure_with_llm,
            merge_bundle_and_draft,
        )

        bundle = build_failure_bundle(failed_step, exception_message, signals)
        draft, dwarns = classify_failure_with_llm(bundle, force=False)
        out = merge_bundle_and_draft(bundle, draft)
        out['warnings'] = dwarns
        return jsonify({'success': True, 'bundle': out})
    except Exception as e:
        uat_logger.exception('failure-bundle compose failed')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai/cases/generate-and-save', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('CREATE_CASE', 'case')
def api_ai_generate_case_and_save():
    """
    使用本地模型生成测试用例与步骤，并直接保存到项目。
    """
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    goal = (data.get('goal') or '').strip()
    project_name = (data.get('project_name') or '').strip()
    case_name_override = (data.get('case_name_override') or data.get('session_title') or '').strip()
    selected_model = (data.get('model') or '').strip() or _get_active_local_model()
    client_plan = data.get('plan')
    profile, legacy_model = _resolve_inference_profile(selected_model)

    if not project_id:
        return jsonify({'success': False, 'error': 'project_id不能为空'}), 400

    _db = Database()
    if not _db.check_project_access(current_user.id, project_id, 'editor'):
        return jsonify({'success': False, 'error': '无权限在此项目创建用例'}), 403

    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    current_case_count = _db.get_project_case_count(project_id)
    if limits['max_cases_per_project'] != -1 and current_case_count >= limits['max_cases_per_project']:
        return jsonify({
            'success': False,
            'error': f'已达到项目用例数量限制（{limits["max_cases_per_project"]}个）。请升级至团队版或企业版以提升配额。',
            'limit_reached': True,
            'current_count': current_case_count,
            'limit': limits['max_cases_per_project'],
            'upgrade_url': '/license'
        }), 403
    if license_info.license_type == LicenseType.FREE.value:
        _db.increment_created_cases(current_user.id)

    if isinstance(client_plan, dict) and isinstance(client_plan.get('steps'), list) and client_plan.get('steps'):
        goal = goal or _ai_str(client_plan.get('case_name')) or 'AI用例'
        generated = {
            'case_name': client_plan.get('case_name') or goal,
            'case_url': client_plan.get('case_url') or '',
            'description': client_plan.get('description') or '',
            'precondition': client_plan.get('precondition') or '',
            'expected_result': client_plan.get('expected_result') or '',
            'steps': list(client_plan.get('steps') or []),
            'meta': client_plan.get('meta') or {},
        }
        if case_name_override:
            generated['case_name'] = case_name_override
    else:
        if not goal:
            return jsonify({'success': False, 'error': 'goal不能为空'}), 400
        mem_ctx = _ai_memory_context_block(current_user.id, goal, probe_url="", project_name=project_name)
        try:
            generated = local_ai_service.generate_case_and_steps(
                goal,
                project_name,
                model=legacy_model,
                profile=profile,
                memory_context=mem_ctx or None,
            )
        except ValueError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'hint': '可先执行: ollama serve，并确认模型已拉取。'
            }), 503
        if case_name_override:
            generated['case_name'] = case_name_override
    local_ai_service._fill_missing_step_payloads(
        generated.get('steps') or [],
        goal or '',
        _ai_str(generated.get('case_url')),
        None,
    )
    generated, warnings = apply_step_normalization_to_plan(generated)
    log_ai_plan_to_audit(
        current_user.id,
        current_user.username,
        'AI_PLAN_GENERATE',
        generated,
        request.remote_addr,
    )
    case_id = db.create_test_case_v2(
        project_id,
        generated.get('case_name', ''),
        generated.get('case_url', ''),
        generated.get('description', ''),
        generated.get('precondition', ''),
        generated.get('expected_result', ''),
    )

    created_steps = 0
    steps = generated.get('steps') or []
    for idx, step in enumerate(steps, start=1):
        db.create_test_step(**_ai_step_to_db_kwargs(step, case_id, idx))
        created_steps += 1

    return jsonify({
        'success': True,
        'case_id': case_id,
        'case_name': generated.get('case_name', ''),
        'steps_created': created_steps,
        'generated': generated,
        'warnings': warnings,
    })


@app.route('/api/ai/task/chat', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_ai_task_chat():
    """
    多轮AI对话生成/优化测试用例步骤（本地模型）。

    可选 body 字段（与左栏/浮层/右键菜单对接，全平台保持同一套语义）：
    - focus_step_index: 用户聚焦的步骤序号（1-based，与 current_plan.steps 顺序一致）
    - focus_step_indices: 多选步骤，如合并步骤、批量优化
    - browser_selection_text / selection_text: 内嵌浏览器划词，用于「断言见划词内容」
    - action_kind / intent: 如 optimize_step、merge_steps、assert_from_selection
    """
    data = request.get_json(silent=True) or {}
    out = _execute_ai_task_chat(
        data,
        current_user.id,
        current_user.username,
        request.remote_addr,
    )
    code = int(out.get('_http', 200))
    body = {k: v for k, v in out.items() if k != '_http'}
    return jsonify(body), code


@app.route('/api/ai/studio/steps-assist', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_ai_studio_steps_assist():
    """
    编排入口：若请求体未带 current_plan 但含 case_id，则从数据库装载步骤后再调用本地模型 chat；
    append_after_chat 为真且 chat 成功时，将返回的 plan.steps 追加到该用例末尾（权限与 /api/ai/cases/append-steps 一致）。
    """
    from assistant_gateway import merge_case_into_chat_payload

    data = request.get_json(silent=True) or {}
    err = merge_case_into_chat_payload(db, data)
    if err:
        return jsonify({
            'success': False,
            'error': err,
            'hint': '请提供有效的 case_id，或自行传入完整 current_plan（与步骤页 AI 浮层一致）。',
        }), 400
    out = _execute_ai_task_chat(
        data,
        current_user.id,
        current_user.username,
        request.remote_addr,
    )
    code = int(out.get('_http', 200))
    body = {k: v for k, v in out.items() if k != '_http'}
    if data.get('append_after_chat') and body.get('success'):
        plan = body.get('plan') or {}
        steps = plan.get('steps') or []
        cid = data.get('case_id')
        if isinstance(steps, list) and steps and cid is not None:
            try:
                case_id_int = int(cid)
            except (TypeError, ValueError):
                case_id_int = 0
            if case_id_int > 0:
                case = db.get_test_case_v2(case_id_int)
                if case:
                    _db = Database()
                    if case.get('project_id') and not _db.check_project_access(
                        current_user.id, case['project_id'], 'editor'
                    ):
                        body['append'] = {'success': False, 'error': '无权限修改此用例'}
                    else:
                        old_steps, _total = db.get_case_steps_paginated(case_id_int, 1, 1000)
                        max_order = 0
                        if old_steps:
                            max_order = max(int(s.get('step_order') or 0) for s in old_steps)
                        goal_hint = _ai_str(data.get('goal')) or _ai_str(case.get('name')) or _ai_str(
                            case.get('description')
                        )
                        local_ai_service._fill_missing_step_payloads(
                            steps,
                            goal_hint,
                            _ai_str(case.get('url')),
                            None,
                        )
                        clean_steps, append_warnings = dedupe_and_validate_ai_steps(steps)
                        created_steps = 0
                        for idx, step in enumerate(clean_steps, start=1):
                            db.create_test_step(
                                **_ai_step_to_db_kwargs(step, case_id_int, max_order + idx)
                            )
                            created_steps += 1
                        body['append'] = {
                            'success': True,
                            'steps_created': created_steps,
                            'warnings': append_warnings,
                        }
    return jsonify(body), code


def _agent_gateway_sse_line(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.route('/api/ai/agent/gateway-stream', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_ai_agent_gateway_stream():
    """
    对话式网关（MVP）：解析左栏自然语言中的「安全子集」意图，通过 text/event-stream 推送进度。
    若请求携带有效的 embedded_session_id 且内嵌网关已配置，则在 **远程画布同一会话** 的 Chromium 中逐步执行（与 WebSocket 画面一致）；
    否则在 HuFirst **后台 Playwright 自动化会话**中执行（与「打开」、/api/navigate、左侧 iframe 同步）。
    若预览首步不是 navigate 但请求携带了目标 URL（或 plan.case_url），网关会自动插入一步导航再执行。
    复杂规划仍请用「生成用例 / 优化」或 /api/ai/task/chat。
    """
    from agent_intent import parse_agent_intent

    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    plan = data.get('plan') if isinstance(data.get('plan'), dict) else {}
    steps = plan.get('steps') if isinstance(plan.get('steps'), list) else []
    has_steps = len(steps) > 0
    gate_url = (data.get('target_page_url') or '').strip()
    if not gate_url:
        gate_url = (plan.get('case_url') or '').strip()
    embedded_sid = (data.get('embedded_session_id') or data.get('remote_session_id') or '').strip()
    use_embedded = bool(embedded_sid and embedded_gateway_enabled())
    exec_uid = int(current_user.id)

    if (not message) and bool(data.get('force_execute')) and has_steps:
        kind, meta = 'execute_plan', {}
    else:
        kind, meta = parse_agent_intent(message, has_steps)

    def generate():
        yield _agent_gateway_sse_line({"t": "intent", "kind": kind, "meta": meta})
        if kind == "none":
            yield _agent_gateway_sse_line(
                {
                    "t": "error",
                    "message": "未识别为可执行命令。示例：「执行当前预览」「打开 https://example.com」",
                }
            )
            yield _agent_gateway_sse_line({"t": "end"})
            return

        embed_exec = use_embedded and kind == "execute_plan"
        embed_nav = use_embedded and kind == "navigate_url"
        need_main_playwright = not (embed_exec or embed_nav)

        if need_main_playwright:
            try:
                headless = resolve_playwright_headless(True)
                sync_start_browser(headless=headless)
            except Exception as e:
                yield _agent_gateway_sse_line({"t": "error", "message": f"启动浏览器失败: {e}"})
                yield _agent_gateway_sse_line({"t": "end"})
                return
            if not sync_automation_session_usable():
                yield _agent_gateway_sse_line(
                    {
                        "t": "error",
                        "message": (
                            "后台 **Playwright 自动化会话** 未就绪。请先在 AI 测试页点击「打开」同步页面，"
                            "或在部署环境完成 Playwright 内核安装（如：python -m playwright install）。"
                            "（未连接远程画布时，识别并执行依赖此会话。）"
                        ),
                    }
                )
                yield _agent_gateway_sse_line({"t": "end"})
                return

        if embed_exec:
            yield _agent_gateway_sse_line(
                {
                    "t": "log",
                    "message": "已在远程画布会话中执行步骤（与当前画布 WebSocket 为同一 Chromium）。",
                }
            )

        if kind == "navigate_url":
            url = (meta.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                yield _agent_gateway_sse_line({"t": "error", "message": "仅支持 http(s) URL"})
                yield _agent_gateway_sse_line({"t": "end"})
                return
            yield _agent_gateway_sse_line({"t": "log", "message": f"导航 → {url}"})
            try:
                if embed_nav:
                    j, err = embedded_gateway_json(
                        "POST",
                        f"/internal/session/{embedded_sid}/run-steps",
                        user_id=exec_uid,
                        body={"steps": [{"action": "navigate", "url": url, "description": "gateway navigate"}]},
                        timeout_sec=120.0,
                    )
                    if err or not isinstance(j, dict) or not j.get("success"):
                        yield _agent_gateway_sse_line(
                            {"t": "error", "message": str(err or (j or {}).get("detail") or "远程导航失败")}
                        )
                    else:
                        rs = (j.get("results") or [{}])[0]
                        if not rs.get("ok"):
                            yield _agent_gateway_sse_line(
                                {"t": "error", "message": str(rs.get("error") or "远程导航失败")}
                            )
                        else:
                            yield _agent_gateway_sse_line({"t": "done", "message": "导航完成", "url": url})
                else:
                    sync_navigate_to(url, ai_probe=True)
                    yield _agent_gateway_sse_line({"t": "done", "message": "导航完成", "url": url})
            except Exception as e:
                yield _agent_gateway_sse_line({"t": "error", "message": str(e)})
            yield _agent_gateway_sse_line({"t": "end"})
            return

        # execute_plan
        script_steps = ai_plan_steps_to_playwright_script_steps(steps)
        if not script_steps:
            msg = '当前预览没有可映射为脚本的原子步骤'
            if has_steps:
                msg += '（常见原因：click/input/verify 缺少 selector，或 navigate 缺少 URL）。请检查左栏预览或重新生成。'
            else:
                msg += '（预览步骤为空）。'
            yield _agent_gateway_sse_line({"t": "error", "message": msg})
            yield _agent_gateway_sse_line({"t": "end"})
            return
        if (
            gate_url.startswith(('http://', 'https://'))
            and script_steps
            and script_steps[0].get('action') != 'navigate'
        ):
            script_steps = [
                {'action': 'navigate', 'url': gate_url, 'description': '（网关）先打开目标页'},
            ] + script_steps
            yield _agent_gateway_sse_line(
                {'t': 'log', 'message': f'预览首步非 navigate，已根据目标 URL 插入导航：{gate_url}'}
            )
        max_n = int(os.environ.get('AI_AGENT_GATEWAY_MAX_STEPS', '40') or 40)
        script_steps = script_steps[: max(1, max_n)]
        total = len(script_steps)
        yield _agent_gateway_sse_line({"t": "log", "message": f"共 {total} 步，开始逐步执行…"})
        for i, st in enumerate(script_steps, start=1):
            yield _agent_gateway_sse_line(
                {"t": "step_begin", "index": i, "total": total, "action": st.get("action"), "step": st}
            )
            try:
                if embed_exec:
                    j, err = embedded_gateway_json(
                        "POST",
                        f"/internal/session/{embedded_sid}/run-steps",
                        user_id=exec_uid,
                        body={"steps": [st]},
                        timeout_sec=180.0,
                    )
                    if err or not isinstance(j, dict) or not j.get("success"):
                        raise RuntimeError(str(err or (j or {}).get("detail") or "远程执行请求失败"))
                    rs0 = (j.get("results") or [{}])[0]
                    if not rs0.get("ok"):
                        raise RuntimeError(str(rs0.get("error") or "远程步骤失败"))
                else:
                    sync_execute_script_steps([st])
                yield _agent_gateway_sse_line({"t": "step_ok", "index": i, "total": total})
            except Exception as e:
                yield _agent_gateway_sse_line({"t": "step_err", "index": i, "total": total, "error": str(e)})
                break
        yield _agent_gateway_sse_line({"t": "done", "message": "执行序列结束"})
        yield _agent_gateway_sse_line({"t": "end"})

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers=headers,
    )


@app.route('/api/ai/task/plan-async', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_ai_task_plan_async():
    """提交规划任务，立即返回 job_id；推理在后台线程继续（见 GET /api/ai/task/job/<id>）。"""
    data = request.get_json(silent=True) or {}
    goal = (data.get('goal') or '').strip()
    if not goal:
        return jsonify({'success': False, 'error': 'goal不能为空'}), 400
    task_type = (data.get('task_type') or 'test_case_generation').strip()
    route = _route_ai_model(task_type)
    if route['provider'] != 'local':
        return jsonify({
            'success': False,
            'error': '该任务需走云端分析接口，请调用 /api/ai/task/cloud-analyze',
        }), 400
    job_id = str(uuid.uuid4())
    with _AI_BG_LOCK:
        _ai_bg_prune_locked()
        _AI_BG_JOBS[job_id] = {
            'user_id': current_user.id,
            'kind': 'plan',
            'status': 'running',
            't0': time.time(),
            'cancelled': False,
        }
    _start_ai_bg_job_thread(
        job_id,
        'plan',
        data,
        current_user.id,
        current_user.username,
        request.remote_addr,
    )
    return jsonify({'success': True, 'job_id': job_id}), 202


@app.route('/api/ai/task/chat-async', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_ai_task_chat_async():
    """提交优化任务，立即返回 job_id；推理在后台线程继续。"""
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'success': False, 'error': 'message不能为空'}), 400
    route = _route_ai_model('test_case_generation')
    if route['provider'] != 'local':
        return jsonify({'success': False, 'error': '当前仅支持本地模型对话'}), 400
    job_id = str(uuid.uuid4())
    with _AI_BG_LOCK:
        _ai_bg_prune_locked()
        _AI_BG_JOBS[job_id] = {
            'user_id': current_user.id,
            'kind': 'chat',
            'status': 'running',
            't0': time.time(),
            'cancelled': False,
        }
    _start_ai_bg_job_thread(
        job_id,
        'chat',
        data,
        current_user.id,
        current_user.username,
        request.remote_addr,
    )
    return jsonify({'success': True, 'job_id': job_id}), 202


@app.route('/api/ai/task/job/<job_id>', methods=['GET'])
@login_required
@api_error_handler
def api_ai_task_job_status(job_id):
    """查询后台 AI 任务状态；完成后 result 与同步接口 JSON 一致。"""
    with _AI_BG_LOCK:
        rec = _AI_BG_JOBS.get(job_id)
    if not rec or rec.get('user_id') != current_user.id:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    st = rec.get('status', 'running')
    if st == 'running':
        return jsonify({'success': True, 'status': 'running'})
    if st == 'cancelled':
        return jsonify({'success': True, 'status': 'cancelled'})
    if st == 'error':
        return jsonify({
            'success': True,
            'status': 'error',
            'error': rec.get('error'),
            'result': rec.get('result'),
        })
    return jsonify({'success': True, 'status': 'done', 'result': rec.get('result')})


@app.route('/api/ai/task/job/<job_id>/cancel', methods=['POST'])
@login_required
@api_error_handler
def api_ai_task_job_cancel(job_id):
    """标记取消：后台线程结束后不写入结果（无法中断已在进行的模型推理）。"""
    with _AI_BG_LOCK:
        rec = _AI_BG_JOBS.get(job_id)
        if not rec or rec.get('user_id') != current_user.id:
            return jsonify({'success': False, 'error': '任务不存在'}), 404
        rec['cancelled'] = True
    return jsonify({'success': True})


@app.route('/api/ai/cases/append-steps', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_ai_append_steps_to_case():
    """
    将 AI 生成步骤追加到现有用例，不新建用例。
    可选 case_name_override / session_title：同时重命名该用例（需编辑权限）。
    """
    data = request.get_json(silent=True) or {}
    case_id = data.get('case_id')
    steps = data.get('steps') or []
    case_name_override = (data.get('case_name_override') or data.get('session_title') or '').strip()
    if not case_id:
        return jsonify({'success': False, 'error': 'case_id不能为空'}), 400
    if not isinstance(steps, list) or not steps:
        return jsonify({'success': False, 'error': 'steps不能为空'}), 400

    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'success': False, 'error': '测试用例不存在'}), 404

    _db = Database()
    if case.get('project_id') and not _db.check_project_access(current_user.id, case['project_id'], 'editor'):
        return jsonify({'success': False, 'error': '无权限修改此用例'}), 403

    old_steps, _total = db.get_case_steps_paginated(case_id, 1, 1000)
    max_order = 0
    if old_steps:
        max_order = max([int(s.get('step_order') or 0) for s in old_steps] or [0])

    goal_hint = _ai_str(data.get('goal')) or _ai_str(case.get('name')) or _ai_str(case.get('description'))
    local_ai_service._fill_missing_step_payloads(
        steps,
        goal_hint,
        _ai_str(case.get('url')),
        None,
    )
    clean_steps, warnings = dedupe_and_validate_ai_steps(steps)
    ct_ai = _app_case_type(case)
    if ct_ai == "ui":
        clean_steps = [s for s in clean_steps if (s.get("action") or "").strip().lower() != "api_request"]
    if not clean_steps:
        return jsonify(
            {
                "success": False,
                "error": "没有可写入的步骤（Web 用例已忽略接口类步骤）",
                "warnings": warnings,
            }
        ), 400

    created_steps = 0
    for idx, step in enumerate(clean_steps, start=1):
        db.create_test_step(**_ai_step_to_db_kwargs(step, case_id, max_order + idx))
        created_steps += 1

    final_name = case.get('name') or ''
    if case_name_override:
        db.update_test_case_v2(case_id, name=case_name_override)
        final_name = case_name_override

    return jsonify({
        'success': True,
        'case_id': case_id,
        'case_name': final_name,
        'steps_created': created_steps,
        'warnings': warnings,
    })

# API: 悬停在元素上
@app.route('/api/hover_element', methods=['POST'])
@api_error_handler
@log_api_request
def api_hover_element():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', '')
    selector_type = data.get('selector_type', 'css')
    iframe_selector = data.get('iframe_selector', '')
    
    if not selector:
        return jsonify({'error': '选择器不能为空'}), 400
    
    sync_hover_element(selector, selector_type, iframe_selector=iframe_selector)
    return jsonify({'success': True})

# API: 双击元素
@app.route('/api/double_click', methods=['POST'])
@api_error_handler
@log_api_request
def api_double_click():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', '')
    selector_type = data.get('selector_type', 'css')
    iframe_selector = data.get('iframe_selector', '')
    
    if not selector:
        return jsonify({'error': '选择器不能为空'}), 400
    
    sync_double_click_element(selector, selector_type, iframe_selector=iframe_selector)
    return jsonify({'success': True})

# API: 点击元素
@app.route('/api/click_element', methods=['POST'])
@api_error_handler
@log_api_request
def api_click_element():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', '')
    
    if not selector:
        return jsonify({'error': '选择器不能为空'}), 400
    
    sync_click_element(selector)
    return jsonify({'success': True})



# API: 右键点击元素
@app.route('/api/right_click', methods=['POST'])
@api_error_handler
@log_api_request
def api_right_click():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', '')
    selector_type = data.get('selector_type', 'css')
    iframe_selector = data.get('iframe_selector', '')
    
    if not selector:
        return jsonify({'error': '选择器不能为空'}), 400
    
    sync_right_click_element(selector, selector_type, iframe_selector=iframe_selector)
    return jsonify({'success': True})

# API: 等待元素出现
@app.route('/api/wait_for_selector', methods=['POST'])
@api_error_handler
@log_api_request
def api_wait_for_selector():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', '')
    timeout = data.get('timeout', 30000)
    
    if not selector:
        return jsonify({'error': '选择器不能为空'}), 400
    
    sync_wait_for_selector(selector, timeout)
    return jsonify({'success': True})

# API: 等待元素可见
@app.route('/api/wait_for_element_visible', methods=['POST'])
@api_error_handler
@log_api_request
def api_wait_for_element_visible():
    data = request.get_json(silent=True) or {}
    selector = data.get('selector', '')
    timeout = data.get('timeout', 30000)
    
    if not selector:
        return jsonify({'error': '选择器不能为空'}), 400
    
    sync_wait_for_element_visible(selector, timeout)
    return jsonify({'success': True})

# API: 获取页面元素
@app.route('/api/page_elements', methods=['GET'])
@api_error_handler
@log_api_request
def api_page_elements():
    elements = sync_get_page_elements()
    return jsonify({'success': True, 'elements': elements})

# API: 检查是否存在测试用例
@app.route('/api/has_test_cases', methods=['GET'])
@api_error_handler
@log_api_request
def api_has_test_cases():
    cases = db.get_all_test_cases()
    has_cases = len(cases) > 0
    return jsonify({'success': True, 'has_cases': has_cases})

# API: 获取页面截图
@app.route('/api/screenshot', methods=['GET'])
@api_error_handler
@log_api_request
def api_screenshot():
    try:
        from flask import send_file

        # 生成截图文件名
        timestamp = int(time.time())
        filename = f"screenshot_{timestamp}.png"
        filepath = os.path.join(os.getcwd(), filename)
        
        # 保存截图
        sync_take_screenshot(filepath)
        
        # 返回截图文件
        return send_file(filepath, as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# —— 内置浏览器（与 AI 测试工作台共用 Playwright 会话，PNG 直出供侧栏预览）——

@app.route('/api/browser/frame', methods=['GET'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_frame():
    from flask import send_file

    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动，请先「打开页面」'}), 400
    data = sync_take_screenshot_bytes()
    return send_file(
        io.BytesIO(data),
        mimetype='image/png',
        max_age=0,
        conditional=False,
    )


@app.route('/api/browser/status', methods=['GET'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_status():
    try:
        if not sync_automation_session_usable():
            return jsonify({'success': True, 'ready': False, 'url': '', 'title': ''})
        url = sync_get_current_url()
        title = sync_get_page_title()
        return jsonify({'success': True, 'ready': True, 'url': url, 'title': title})
    except Exception as e:
        uat_logger.debug(f"api_browser_status: {e}")
        return jsonify({'success': True, 'ready': False, 'url': '', 'title': ''})


@app.route('/api/browser/go-back', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_go_back():
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    ok = sync_browser_go_back()
    return jsonify({'success': True, 'moved': ok})


@app.route('/api/browser/go-forward', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_go_forward():
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    ok = sync_browser_go_forward()
    return jsonify({'success': True, 'moved': ok})


@app.route('/api/browser/reload', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_reload():
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    sync_browser_reload()
    return jsonify({'success': True})


@app.route('/api/browser/diagnostics', methods=['GET'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_diagnostics():
    """页面/性能轻量诊断（开发者面板）。"""
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    payload = sync_get_page_diagnostics()
    return jsonify({'success': True, 'data': payload})


@app.route('/api/browser/inspect', methods=['GET'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_inspect():
    """内置浏览器「页面结构」：可交互元素列表 + 建议定位（非完整 F12）。"""
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动，请先打开页面'}), 400
    payload = sync_get_interactive_page_snapshot(150)
    return jsonify({'success': True, 'data': payload})


@app.route('/api/desktop/verify-element', methods=['POST'])
@app.route('/api/desktop/verify_element', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_desktop_verify_element():
    """校验桌面元素是否可解析（UIA 精准链，不依赖遮挡/Z 序）。"""
    data = request.get_json(silent=True) or {}
    spec, err = _parse_desktop_spec_body(data)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    try:
        from desktop_automation import sync_desktop_verify_element

        result = sync_desktop_verify_element(
            data.get('selector_type') or '',
            data.get('selector_value') or '',
            spec or {},
            data.get('locator_candidates'),
        )
        if result.get('success'):
            return jsonify({'success': True, **result})
        fail_body = {'success': False, 'error': result.get('error') or '校验失败'}
        if result.get('need_relearn'):
            fail_body['need_relearn'] = True
            fail_body['best_score'] = float(result.get('best_score') or 0.0)
        return jsonify(fail_body), 400
    except Exception as e:
        uat_logger.log_exception('api_desktop_verify_element', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/desktop/inspect', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_desktop_inspect():
    """UIA 探测已移除，请使用 visual 框选录制。"""
    return jsonify({
        'success': False,
        'error': '桌面 UIA 探测已移除，请使用框选录制',
    }), 410


@app.route('/api/desktop/pick', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_desktop_pick():
    """根据屏幕坐标反查控件（兼容旧接口；推荐使用 /api/desktop/picker/* 悬浮拾取）。"""
    data = request.get_json(silent=True) or {}
    try:
        x = int(data.get('x'))
        y = int(data.get('y'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '请提供整数坐标 x, y'}), 400
    return jsonify({
        'success': False,
        'error': '坐标/UIA 拾取已废弃，请使用框选录制（/api/desktop/picker/start）',
    }), 410


def _parse_desktop_spec_body(data: dict) -> tuple:
    spec = data.get('desktop_spec') or {}
    if isinstance(spec, str):
        try:
            spec = json.loads(spec) if spec.strip() else {}
        except json.JSONDecodeError:
            return None, 'desktop_spec JSON 无效'
    if not isinstance(spec, dict):
        return None, 'desktop_spec 格式无效'
    return spec, None


@app.route('/api/desktop/picker/start', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_desktop_picker_start():
    """启动桌面悬浮拾取/录制器（点选控件自动写入定位，无需手写）。"""
    data = request.get_json(silent=True) or {}
    spec, err = _parse_desktop_spec_body(data)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    try:
        from desktop_picker import desktop_picker_available, sync_start_desktop_picker

        if not desktop_picker_available():
            return jsonify({
                'success': False,
                'error': '桌面框选录制需 Windows + opencv-python + mss',
            }), 400
        record_mode = _parse_api_bool(data.get('record_mode'), default=False)
        if not record_mode:
            mode_s = data.get('mode')
            if isinstance(mode_s, bool):
                record_mode = mode_s
            else:
                record_mode = str(mode_s or '').strip().lower() in (
                    '1',
                    'true',
                    'yes',
                    'record',
                    'recording',
                )
        record_action = (data.get('record_action') or data.get('action') or 'click').strip().lower()
        if record_action not in ('click', 'double_click', 'input', 'verify'):
            record_action = 'click'
        result = sync_start_desktop_picker(
            spec or {},
            record_mode=record_mode,
            record_action=record_action,
            input_value=(data.get('input_value') or '').strip(),
            verify_type=(data.get('verify_type') or 'auto').strip().lower() or 'auto',
        )
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        uat_logger.log_exception('api_desktop_picker_start', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/desktop/picker/stop', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_desktop_picker_stop():
    """关闭桌面拾取/录制悬浮窗。"""
    try:
        from desktop_picker import sync_stop_desktop_picker

        return jsonify(sync_stop_desktop_picker())
    except Exception as e:
        uat_logger.log_exception('api_desktop_picker_stop', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/desktop/picker/status', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_desktop_picker_status():
    """轮询拾取结果或录制新增步骤（与 Web check_selected_element 类似）。"""
    try:
        from desktop_picker import sync_get_desktop_picker_status

        consume = (request.args.get('consume') or '').strip().lower() in ('1', 'true', 'yes')
        return jsonify(sync_get_desktop_picker_status(consume_last_pick=consume))
    except Exception as e:
        uat_logger.log_exception('api_desktop_picker_status', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/desktop/visual/relearn', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_desktop_visual_relearn():
    """匹配失败自学习：用户点击屏幕位置，更新步骤 visual 模板。"""
    data = request.get_json(silent=True) or {}
    step_id = data.get('step_id')
    selector_value = (data.get('selector_value') or '').strip()
    try:
        click_x = int(data.get('x'))
        click_y = int(data.get('y'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '请提供整数坐标 x, y'}), 400
    if not selector_value and step_id:
        step = db.get_test_step(int(step_id))
        if not step:
            return jsonify({'success': False, 'error': '步骤不存在'}), 404
        selector_value = (step.get('selector_value') or '').strip()
    if not selector_value:
        return jsonify({'success': False, 'error': '缺少 selector_value 或 step_id'}), 400
    try:
        from desktop_visual_engine import update_visual_template_at_click

        new_json = update_visual_template_at_click(
            selector_value, click_x, click_y, half_size=int(data.get('half_size') or 24)
        )
        if step_id:
            db.update_test_step(
                int(step_id),
                selector_type='visual',
                selector_value=new_json,
            )
        return jsonify({'success': True, 'selector_value': new_json})
    except Exception as e:
        uat_logger.log_exception('api_desktop_visual_relearn', e)
        return jsonify({'success': False, 'error': str(e)}), 500


def _enrich_desktop_spec_from_case(case_id, spec: dict) -> dict:
    """元素捕获未传 desktop_spec 时，从用例已有桌面步骤推断附着窗口。"""
    if not isinstance(spec, dict):
        spec = {}
    if spec.get('hwnd') or spec.get('process') or spec.get('path') or spec.get('window_title'):
        return spec
    if not case_id:
        return spec
    try:
        cid = int(case_id)
    except (TypeError, ValueError):
        return spec
    for st in db.get_case_steps(cid) or []:
        if (st.get('automation_layer') or 'web').strip().lower() != 'desktop':
            continue
        raw = st.get('desktop_spec')
        if not raw:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
    return spec


def _resolve_element_picker_web_url(data: dict) -> tuple:
    """解析 Web 拾取导航 URL（与 start_visual_selection 一致）。"""
    case_id = data.get('case_id')
    target_url = (data.get('url') or '').strip()
    fixed_url = None
    if target_url:
        fixed_url, url_err = _validate_and_fix_url(target_url)
        if url_err:
            uat_logger.warning(f"元素捕获传入 URL 无效，将尝试按用例解析: {url_err}")
    if not fixed_url and case_id:
        try:
            parsed_case_id = int(case_id)
        except Exception:
            parsed_case_id = None
        if parsed_case_id:
            case_info = db.get_test_case_v2(parsed_case_id)
            case_steps = db.get_case_steps(parsed_case_id)
            resolved_url, _source = _resolve_case_navigation_url(
                case=case_info, case_id=parsed_case_id, steps=case_steps
            )
            if resolved_url:
                fixed_url = resolved_url
    return fixed_url, None


def _persist_desktop_picker_steps_to_case(case_id: int, steps: list) -> list:
    """将捕获器 session 中的桌面步骤直接写入用例（status 轮询时调用，避免前端 POST 丢失）。"""
    if not case_id or not steps:
        return []
    case_row = db.get_test_case_v2(int(case_id))
    if not case_row:
        return []
    pid = case_row.get('project_id')
    if pid and not db.check_project_access(current_user.id, int(pid), 'editor'):
        return []
    saved_ids = []
    for st in steps:
        if not isinstance(st, dict):
            continue
        action = (st.get('action') or 'click').strip()
        layer = 'desktop'
        step_err = _validate_step_action_for_case(case_row, action, layer)
        if step_err:
            continue
        ds = st.get('desktop_spec') or {}
        if not isinstance(ds, str):
            ds = json.dumps(ds, ensure_ascii=False)
        step_id = db.create_test_step(
            int(case_id),
            action,
            st.get('selector_type') or 'automation_id',
            st.get('selector_value') or '',
            st.get('input_value') or '',
            st.get('description') or '',
            None,
            '',
            '',
            '',
            '',
            False,
            '',
            st.get('compare_type') or 'equals',
            st.get('locator_candidates') or '',
            1,
            '',
            automation_layer=layer,
            desktop_spec=ds or '',
        )
        if step_id:
            saved_ids.append(step_id)
    return saved_ids


@app.route('/api/element-picker/start', methods=['POST'])
@app.route('/api/element_picker/start', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_element_picker_start():
    """统一元素捕获：Windows 桌面 + Web 浏览器。"""
    data = request.get_json(silent=True) or {}
    spec, err = _parse_desktop_spec_body(data)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    spec = _enrich_desktop_spec_from_case(data.get('case_id'), spec or {})
    record_mode = _parse_api_bool(data.get('record_mode'), default=False)
    if not record_mode:
        mode_s = (data.get('mode') or '').strip().lower()
        record_mode = mode_s in ('1', 'true', 'yes', 'record', 'recording')
    web_navigate = _parse_api_bool(data.get('web_navigate'), default=False)
    web_attach_existing = _parse_api_bool(
        data.get('web_attach_existing'), default=False
    )
    capture_channel = (
        (data.get('capture_channel') or data.get('channel') or 'desktop')
        .strip()
        .lower()
    )
    if capture_channel not in ('desktop', 'web'):
        capture_channel = 'desktop'
    enable_web = _parse_api_bool(
        data.get('enable_web'),
        default=capture_channel == 'web' or bool(web_navigate or web_attach_existing),
    )
    web_url = ''
    web_fallback_url = ''
    resolved_url, _ = _resolve_element_picker_web_url(data)
    if capture_channel == 'web':
        web_url = ''
        web_fallback_url = ''
        web_navigate = False
        web_attach_existing = True
    elif web_navigate:
        web_url = resolved_url or ''
    elif enable_web:
        web_fallback_url = resolved_url or ''
    try:
        case_id_raw = data.get('case_id')
        try:
            picker_case_id = int(case_id_raw) if case_id_raw not in (None, '') else None
        except (TypeError, ValueError):
            picker_case_id = None
        from element_picker import sync_start_element_picker

        if capture_channel == 'desktop':
            from desktop_runtime import (
                desktop_runtime_available,
                desktop_runtime_unavailable_reason,
            )

            if sys.platform == "win32" and not desktop_runtime_available():
                reason = desktop_runtime_unavailable_reason()
                if reason:
                    return jsonify(
                        {
                            "success": False,
                            "error": reason,
                            "python_executable": sys.executable,
                        }
                    ), 400

        platform_origin = (request.host_url or '').rstrip('/')
        web_capture_mode = (
            (data.get('web_capture_mode') or data.get('mode') or 'cdp').strip().lower()
        )
        if web_capture_mode not in ('cdp', 'extension', 'legacy_inject'):
            web_capture_mode = 'cdp'
        browser = (data.get('browser') or 'edge').strip().lower()
        start_url = (data.get('start_url') or data.get('url') or resolved_url or '').strip()
        result = sync_start_element_picker(
            desktop_spec=spec or {},
            record_mode=record_mode,
            capture_channel=capture_channel,
            web_url=web_url or '',
            web_fallback_url=web_fallback_url or '',
            enable_web=enable_web,
            web_navigate=web_navigate,
            web_attach_existing=web_attach_existing,
            case_id=picker_case_id,
            platform_origin=platform_origin,
            web_capture_mode=web_capture_mode,
            browser=browser,
            start_url=start_url,
        )
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        uat_logger.log_exception('api_element_picker_start', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/element-picker/stop', methods=['POST'])
@app.route('/api/element_picker/stop', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_element_picker_stop():
    """停止统一元素捕获。"""
    try:
        from element_picker import sync_get_element_picker_status, sync_stop_element_picker

        pre = sync_get_element_picker_status(consume_last_pick=False)
        case_id = int(
            pre.get('case_id')
            or (pre.get('desktop') or {}).get('case_id')
            or 0
        )
        new_steps = list(pre.get('new_recorded_steps') or [])
        if not new_steps:
            desk = pre.get('desktop') or {}
            rec = list(desk.get('recorded_steps') or [])
            sent = int(desk.get('_sent_count') or 0)
            if len(rec) > sent:
                new_steps = rec[sent:]
        result = sync_stop_element_picker()
        if case_id and new_steps:
            saved_ids = _persist_desktop_picker_steps_to_case(case_id, new_steps)
            if saved_ids:
                result['saved_step_ids'] = saved_ids
        return jsonify(result)
    except Exception as e:
        uat_logger.log_exception('api_element_picker_stop', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/element-picker/status', methods=['GET'])
@app.route('/api/element_picker/status', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_element_picker_status():
    """轮询统一捕获状态（桌面 + Web）。"""
    try:
        from element_picker import sync_get_element_picker_status

        consume = (request.args.get('consume') or '').strip().lower() in ('1', 'true', 'yes')
        result = sync_get_element_picker_status(consume_last_pick=consume)
        case_id = int(
            result.get('case_id')
            or (result.get('desktop') or {}).get('case_id')
            or 0
        )
        new_steps = list(result.get('new_recorded_steps') or [])
        if not new_steps:
            desk = result.get('desktop') or {}
            new_steps = list(desk.get('new_recorded_steps') or [])
        if case_id and new_steps:
            saved_ids = _persist_desktop_picker_steps_to_case(case_id, new_steps)
            if saved_ids:
                result['saved_step_ids'] = saved_ids
        if case_id and result.get('picker_closed') and not result.get('saved_step_ids'):
            desk = result.get('desktop') or {}
            flush_all = list(result.get('recorded_steps') or desk.get('recorded_steps') or [])
            sent = int(desk.get('_sent_count') or result.get('_sent_count') or 0)
            if len(flush_all) > sent:
                saved_ids = _persist_desktop_picker_steps_to_case(case_id, flush_all[sent:])
                if saved_ids:
                    result['saved_step_ids'] = saved_ids
        return jsonify(result)
    except Exception as e:
        uat_logger.log_exception('api_element_picker_status', e)
        return jsonify({'success': False, 'error': str(e)}), 500


def _web_dom_picker_cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp


# --- 网页捕获（CDP / 扩展，与 desktop API 分离）---


@app.route('/api/web-capture/session/start', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_session_start():
    data = request.get_json(silent=True) or {}
    from web_capture.session import start_session

    mode = (data.get('mode') or data.get('web_capture_mode') or 'cdp').strip().lower()
    result = start_session(
        mode=mode,
        record_mode=_parse_api_bool(data.get('record_mode'), default=False),
        case_id=int(data.get('case_id') or 0) or None,
        platform_origin=(request.host_url or '').rstrip('/'),
        browser=(data.get('browser') or 'edge').strip().lower(),
        start_url=(data.get('start_url') or data.get('url') or '').strip(),
    )
    return jsonify(result), (200 if result.get('success') else 400)


@app.route('/api/web-capture/browser/launch', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_browser_launch():
    data = request.get_json(silent=True) or {}
    from web_capture import cdp_browser

    result = cdp_browser.launch_debug_browser(
        browser=(data.get('browser') or 'edge').strip().lower(),
        port=data.get('port'),
        url=(data.get('url') or '').strip(),
    )
    if result.get('success'):
        cdp_browser.connect_playwright_over_cdp(result.get('debug_port'))
    return jsonify(result), (200 if result.get('success') else 400)


@app.route('/api/web-capture/browser/pages', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_browser_pages():
    from web_capture import cdp_browser

    return jsonify(cdp_browser.list_pages())


@app.route('/api/web-capture/browser/attach', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_browser_attach():
    data = request.get_json(silent=True) or {}
    from web_capture import cdp_browser

    result = cdp_browser.attach_page(
        page_index=int(data.get('page_index') or 0),
        target_id=(data.get('target_id') or '').strip(),
    )
    return jsonify(result), (200 if result.get('success') else 400)


@app.route('/api/web-capture/browser/chrome-capture', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_browser_chrome_capture():
    data = request.get_json(silent=True) or {}
    from web_capture.session import capture_browser_chrome, validate_session_id

    session_id = (data.get('session_id') or '').strip()
    if not validate_session_id(session_id):
        return jsonify({'success': False, 'error': '捕获会话无效或已结束'}), 404
    target = (data.get('target') or '').strip().lower()
    result = capture_browser_chrome(session_id, target)
    return jsonify(result), (200 if result.get('success') else 400)


@app.route('/api/web-capture/pick', methods=['POST', 'OPTIONS'])
def api_web_capture_pick():
    if request.method == 'OPTIONS':
        return _web_dom_picker_cors(Response('', status=204))
    from web_capture.session import report_pick

    data = request.get_json(silent=True) or {}
    session_id = (
        data.get('session_id') or data.get('session') or request.args.get('session') or ''
    ).strip()
    payload = data.get('payload') if isinstance(data.get('payload'), dict) else data
    result = report_pick(session_id, payload or {})
    return _web_dom_picker_cors(jsonify(result)), (200 if result.get('success') else 400)


@app.route('/api/web-capture/locator/test', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_locator_test():
    import asyncio

    data = request.get_json(silent=True) or {}
    from web_capture import cdp_browser
    from web_capture.validator import evaluate_locator_async

    page = cdp_browser.get_active_page()
    if not page:
        conn = cdp_browser.connect_playwright_over_cdp()
        if not conn.get('success'):
            return jsonify({'success': False, 'error': conn.get('error')}), 400
        page = cdp_browser.get_active_page()
    result = asyncio.run(
        evaluate_locator_async(
            page,
            (data.get('selector_type') or 'css').strip(),
            (data.get('selector_value') or '').strip(),
        )
    )
    return jsonify(result)


@app.route('/api/web-capture/verify', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_verify():
    import asyncio

    data = request.get_json(silent=True) or {}
    from web_capture import cdp_browser
    from web_capture.validator import verify_element_async

    page = cdp_browser.get_active_page()
    if not page:
        conn = cdp_browser.connect_playwright_over_cdp()
        if not conn.get('success'):
            return jsonify({'success': False, 'error': conn.get('error')}), 400
        page = cdp_browser.get_active_page()
    result = asyncio.run(
        verify_element_async(
            page,
            (data.get('selector_type') or 'css').strip(),
            (data.get('selector_value') or '').strip(),
        )
    )
    return jsonify(result)


@app.route('/api/web-capture/similar', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_similar():
    import asyncio

    data = request.get_json(silent=True) or {}
    from web_capture import cdp_browser
    from web_capture.similar import find_similar_elements_async

    page = cdp_browser.get_active_page()
    if not page:
        return jsonify({'success': False, 'error': '无 CDP 页面'}), 400
    result = asyncio.run(
        find_similar_elements_async(
            page,
            (data.get('selector_type') or 'css').strip(),
            (data.get('selector_value') or '').strip(),
        )
    )
    return jsonify(result)


@app.route('/api/web-capture/highlight', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_highlight():
    data = request.get_json(silent=True) or {}
    from web_capture import cdp_browser
    from web_capture.cdp_picker import arm_picker, inject_all_frames

    page = cdp_browser.get_active_page()
    if not page:
        return jsonify({'success': False, 'error': '无 CDP 页面'}), 400
    sid = (data.get('session_id') or '').strip()
    api_base = (request.host_url or '').rstrip('/')
    inject_all_frames(page, api_base=api_base, session_id=sid)
    return jsonify(arm_picker(page))


@app.route('/api/web-capture/extension/status', methods=['GET'])
@login_required
@api_error_handler
def api_web_capture_extension_status():
    from web_capture.extension_bridge import get_extension_status

    return jsonify(get_extension_status())


@app.route('/api/web-capture/extension/install', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_web_capture_extension_install():
    import subprocess
    import sys

    root = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(root, 'tools', 'install_web_extension.py')
    if not os.path.isfile(script):
        return jsonify({'success': False, 'error': '安装脚本不存在'}), 404
    try:
        proc = subprocess.run(
            [sys.executable, script, '--prepare'],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
        )
        return jsonify(
            {
                'success': proc.returncode == 0,
                'stdout': proc.stdout[-2000:] if proc.stdout else '',
                'stderr': proc.stderr[-2000:] if proc.stderr else '',
            }
        )
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/web-capture/highlight.js', methods=['GET'])
def api_web_capture_highlight_js():
    from web_capture.cdp_picker import get_highlight_js
    from web_capture.session import validate_session_id

    session_id = (request.args.get('session') or '').strip()
    if not validate_session_id(session_id):
        return _web_dom_picker_cors(
            Response('// invalid session\n', status=404, mimetype='application/javascript')
        )
    api_base = (request.host_url or '').rstrip('/')
    page_only = (request.args.get('page_only') or '').strip() in ('1', 'true', 'yes')
    body = get_highlight_js(api_base, session_id, page_only=page_only)
    return _web_dom_picker_cors(
        Response(body, mimetype='application/javascript; charset=utf-8')
    )


@app.route('/api/web-capture/toolbar.js', methods=['GET'])
def api_web_capture_toolbar_js():
    from web_capture.cdp_picker import get_browser_toolbar_js
    from web_capture.session import validate_session_id

    session_id = (request.args.get('session') or '').strip()
    if not validate_session_id(session_id):
        return _web_dom_picker_cors(
            Response('// invalid session\n', status=404, mimetype='application/javascript')
        )
    api_base = (request.host_url or '').rstrip('/')
    body = get_browser_toolbar_js(api_base, session_id)
    return _web_dom_picker_cors(
        Response(body, mimetype='application/javascript; charset=utf-8')
    )


@app.route('/api/web-capture/arm', methods=['POST', 'OPTIONS'])
def api_web_capture_arm():
    if request.method == 'OPTIONS':
        return _web_dom_picker_cors(Response('', status=204))
    data = request.get_json(silent=True) or {}
    from web_capture.session import arm_session, validate_session_id

    session_id = (data.get('session_id') or '').strip()
    if not validate_session_id(session_id):
        return _web_dom_picker_cors(jsonify({'success': False, 'error': '捕获会话无效或已结束'})), 404
    api_base = (request.host_url or '').rstrip('/')
    browser = (data.get('browser') or '').strip().lower()
    result = arm_session(session_id, api_base=api_base, browser=browser)
    return _web_dom_picker_cors(jsonify(result)), (200 if result.get('success') else 400)


@app.route('/api/web-capture/disarm', methods=['POST', 'OPTIONS'])
def api_web_capture_disarm():
    if request.method == 'OPTIONS':
        return _web_dom_picker_cors(Response('', status=204))
    data = request.get_json(silent=True) or {}
    from web_capture.session import disarm_session, validate_session_id

    session_id = (data.get('session_id') or '').strip()
    if not validate_session_id(session_id):
        return _web_dom_picker_cors(jsonify({'success': False, 'error': '捕获会话无效或已结束'})), 404
    result = disarm_session(session_id)
    return _web_dom_picker_cors(jsonify(result)), (200 if result.get('success') else 400)


@app.route('/api/web-capture/stop', methods=['POST', 'OPTIONS'])
def api_web_capture_stop():
    if request.method == 'OPTIONS':
        return _web_dom_picker_cors(Response('', status=204))
    data = request.get_json(silent=True) or {}
    from web_capture.session import stop_session, validate_session_id

    session_id = (data.get('session_id') or '').strip()
    if not validate_session_id(session_id):
        return _web_dom_picker_cors(jsonify({'success': False, 'error': '捕获会话无效或已结束'})), 404
    stop_session(fast=True)
    try:
        from element_picker import sync_stop_element_picker

        sync_stop_element_picker()
    except Exception:
        pass
    return _web_dom_picker_cors(jsonify({'success': True, 'message': '网页捕获已结束'}))


@app.route('/api/web-capture/arm-status', methods=['GET'])
def api_web_capture_arm_status():
    from web_capture.session import validate_session_id, get_session_debug_snapshot

    session_id = (request.args.get('session') or '').strip()
    if not validate_session_id(session_id):
        return _web_dom_picker_cors(jsonify({'success': False, 'armed': False})), 404
    snap = get_session_debug_snapshot()
    return _web_dom_picker_cors(jsonify({'success': True, 'armed': bool(snap.get('armed'))}))


@app.route('/api/web-dom-picker/inject.js', methods=['GET'])
def api_web_dom_picker_inject_js():
    """目标页加载的捕获脚本（凭 session 校验，无需登录 Cookie）。"""
    from pathlib import Path

    from web_dom_picker import validate_session_id

    session_id = (request.args.get('session') or '').strip()
    if not validate_session_id(session_id):
        return _web_dom_picker_cors(
            Response('// invalid or expired web dom picker session\n', status=404, mimetype='application/javascript')
        )
    js_path = Path(__file__).resolve().parent / 'static' / 'js' / 'web_dom_picker_inject.js'
    try:
        raw = js_path.read_text(encoding='utf-8')
    except OSError:
        return _web_dom_picker_cors(
            Response('// inject script missing\n', status=500, mimetype='application/javascript')
        )
    api_base = (request.host_url or '').rstrip('/')
    body = raw.replace('__API_BASE__', api_base).replace('__SESSION__', session_id)
    return _web_dom_picker_cors(
        Response(body, mimetype='application/javascript; charset=utf-8')
    )


@app.route('/api/web-dom-picker/pick', methods=['POST', 'OPTIONS'])
def api_web_dom_picker_pick():
    if request.method == 'OPTIONS':
        return _web_dom_picker_cors(Response('', status=204))
    from web_dom_picker import sync_report_web_dom_pick

    data = request.get_json(silent=True) or {}
    session_id = (
        data.get('session_id') or data.get('session') or request.args.get('session') or ''
    ).strip()
    payload = data.get('payload') if isinstance(data.get('payload'), dict) else data
    result = sync_report_web_dom_pick(session_id, payload or {})
    return _web_dom_picker_cors(jsonify(result)), (200 if result.get('success') else 400)


@app.route('/api/web-dom-picker/close', methods=['POST', 'OPTIONS'])
def api_web_dom_picker_close():
    if request.method == 'OPTIONS':
        return _web_dom_picker_cors(Response('', status=204))
    from web_dom_picker import close_web_dom_picker_session

    data = request.get_json(silent=True) or {}
    session_id = (data.get('session') or '').strip()
    result = close_web_dom_picker_session(session_id)
    return _web_dom_picker_cors(jsonify(result)), (200 if result.get('success') else 400)


def _web_capture_proxy_fetch_html(target_url: str, session_id: str, api_base: str) -> tuple:
    """拉取待测页 HTML 并注入捕获脚本（同源代理，供预览 iframe 内高亮拾取）。"""
    import re
    from html import escape as html_escape
    from urllib.parse import urlparse
    from urllib.request import Request, urlopen

    parsed = urlparse(target_url)
    if parsed.scheme not in ('http', 'https'):
        return None, '仅支持 http/https 地址'
    req = Request(
        target_url,
        headers={'User-Agent': 'UAT-WebCapture/1.0', 'Accept': 'text/html,*/*'},
    )
    try:
        with urlopen(req, timeout=25) as resp:
            raw = resp.read()
            ctype = (resp.headers.get('Content-Type') or '').lower()
    except Exception as exc:
        return None, f'无法加载页面: {exc}'

    charset = 'utf-8'
    m = re.search(r'charset=([^\s;]+)', ctype)
    if m:
        charset = m.group(1).strip('\'"')
    try:
        html = raw.decode(charset, errors='replace')
    except LookupError:
        html = raw.decode('utf-8', errors='replace')

    base_href = html_escape(target_url, quote=True)
    api_base = (api_base or '').rstrip('/')
    inject = (
        f'<base href="{base_href}">\n'
        f'<script src="{api_base}/api/web-dom-picker/inject.js?session={html_escape(session_id, quote=True)}"></script>\n'
    )
    low = html.lower()
    if '</head>' in low:
        html = re.sub(r'</head>', inject + '</head>', html, count=1, flags=re.IGNORECASE)
    elif '</body>' in low:
        html = re.sub(r'</body>', inject + '</body>', html, count=1, flags=re.IGNORECASE)
    else:
        html = inject + html
    return html, None


@app.route('/web-capture/workspace')
@login_required
def web_capture_workspace():
    """网页捕获器工作区（独立窗口，内含预览与 DOM 拾取）。"""
    from web_dom_picker import validate_session_id

    session_id = (request.args.get('session') or '').strip()
    if not validate_session_id(session_id):
        return render_template(
            'web_capture_workspace.html',
            error='捕获会话无效或已结束。请返回步骤页重新点击「网页捕获」。',
        )
    initial_url = (request.args.get('url') or '').strip()
    case_id = request.args.get('case_id')
    if not initial_url and case_id:
        try:
            initial_url, _ = _resolve_element_picker_web_url({'case_id': case_id})
        except Exception:
            initial_url = ''
    return render_template(
        'web_capture_workspace.html',
        error='',
        session_id=session_id,
        api_base=(request.host_url or '').rstrip('/'),
        initial_url=initial_url or '',
    )


@app.route('/web-capture/shell')
def web_capture_shell():
    """捕获浏览器启动页（独立配置无平台 Cookie，凭 session 校验）。"""
    from web_capture.session import validate_session_id

    session_id = (request.args.get('session') or '').strip()
    api_base = (request.host_url or '').rstrip('/')
    if not validate_session_id(session_id):
        return (
            '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:24px;">'
            '<p>捕获会话无效或已结束，请返回步骤页重新点击「网页捕获」。</p></body></html>',
            404,
        )
    return render_template(
        'web_capture_shell.html',
        session_id=session_id,
        api_base=api_base,
    )


@app.route('/web-capture/toolbar')
@login_required
def web_capture_toolbar():
    """网页元素捕获器（唯一控制窗口，对齐桌面 Tk 工具条）。"""
    from web_capture.session import validate_session_id, get_session_debug_snapshot, get_session_status

    session_id = (request.args.get('session') or '').strip()
    api_base = (request.host_url or '').rstrip('/')
    if not validate_session_id(session_id):
        return render_template(
            'web_capture_toolbar.html',
            error='捕获会话无效或已结束。请返回步骤页重新点击「网页捕获」。',
            session_id='',
            api_base=api_base,
            bookmarklet='',
        )
    snap = get_session_debug_snapshot()
    status = get_session_status()
    return render_template(
        'web_capture_toolbar.html',
        error='',
        session_id=session_id,
        api_base=api_base,
        bookmarklet=status.get('bookmarklet') or '',
        mode=snap.get('mode') or 'extension',
    )


@app.route('/web-capture/workspace-v2')
@login_required
def web_capture_workspace_v2():
    """CDP 网页捕获工作区（调试浏览器 + 状态面板）。"""
    from web_capture.session import validate_session_id

    session_id = (request.args.get('session') or '').strip()
    if not validate_session_id(session_id):
        return render_template(
            'web_capture_workspace_v2.html',
            error='捕获会话无效或已结束。请返回步骤页重新点击「网页捕获」。',
            session_id='',
            api_base='',
            cdp_port=0,
        )
    from web_capture.session import get_session_debug_snapshot

    snap = get_session_debug_snapshot()
    return render_template(
        'web_capture_workspace_v2.html',
        error='',
        session_id=session_id,
        api_base=(request.host_url or '').rstrip('/'),
        cdp_port=int(snap.get('cdp_port') or 0),
        mode=snap.get('mode') or 'cdp',
    )


@app.route('/web-capture/proxy')
def web_capture_proxy():
    """捕获预览代理：注入拾取脚本，使 iframe 内可悬停高亮。"""
    from web_dom_picker import validate_session_id

    session_id = (request.args.get('session') or '').strip()
    target_url = (request.args.get('url') or '').strip()
    if not validate_session_id(session_id):
        return Response('捕获会话无效', status=403)
    if not target_url:
        return Response('缺少 url 参数', status=400)
    fixed, err = _validate_and_fix_url(target_url)
    if err or not fixed:
        return Response(err or 'URL 无效', status=400)
    api_base = (request.host_url or '').rstrip('/')
    html, fetch_err = _web_capture_proxy_fetch_html(fixed, session_id, api_base)
    if fetch_err:
        return Response(fetch_err, status=502, mimetype='text/plain; charset=utf-8')
    return Response(html, mimetype='text/html; charset=utf-8')


@app.route('/api/desktop/config', methods=['GET'])
@login_required
@api_error_handler
def api_desktop_config():
    """返回 .env 中的桌面默认配置，供步骤编辑页预填与提示。"""
    try:
        from desktop_env_config import public_config

        return jsonify({'success': True, **public_config()})
    except ImportError:
        return jsonify({'success': False, 'error': '桌面模块未安装'}), 500


@app.route('/api/desktop/windows', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_desktop_windows():
    """枚举本机当前可见顶层窗口（零配置附着窗口）。"""
    from desktop_discovery import discovery_available, list_visible_windows

    if not discovery_available():
        return jsonify({
            'success': False,
            'error': '窗口枚举仅支持 Windows',
        }), 400
    return jsonify({'success': True, 'windows': list_visible_windows()})


@app.route('/api/desktop/processes', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_desktop_processes():
    """枚举本机当前运行进程。"""
    from desktop_discovery import discovery_available, list_running_processes

    if not discovery_available():
        return jsonify({'success': False, 'error': '仅支持 Windows'}), 400
    return jsonify({'success': True, 'processes': list_running_processes()})


@app.route('/api/desktop/snapshot', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_desktop_snapshot():
    """运行中窗口 + 进程 + 开始菜单应用目录（供步骤编辑点选）。"""
    from desktop_discovery import desktop_runtime_snapshot, discovery_available

    if not discovery_available():
        return jsonify({'success': False, 'error': '仅支持 Windows'}), 400
    refresh = (request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes')
    if refresh:
        try:
            from desktop_app_catalog import ensure_catalog_built

            ensure_catalog_built(force=True)
        except Exception:
            pass
    snap = desktop_runtime_snapshot(include_catalog=True)
    return jsonify({'success': True, **snap})


@app.route('/api/desktop/catalog', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_desktop_catalog():
    """本机应用目录（开始菜单扫描，含自动别名）。"""
    from desktop_discovery import discovery_available

    if not discovery_available():
        return jsonify({'success': False, 'error': '仅支持 Windows'}), 400
    try:
        from desktop_app_catalog import ensure_catalog_built, list_catalog_apps

        data = ensure_catalog_built(
            force=(request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes')
        )
        return jsonify({
            'success': True,
            'apps': list_catalog_apps(),
            'built_at': data.get('built_at'),
            'app_count': len(data.get('apps') or []),
        })
    except Exception as e:
        uat_logger.log_exception('api_desktop_catalog', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/desktop/catalog/refresh', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_desktop_catalog_refresh():
    """强制重新扫描开始菜单并更新别名库。"""
    from desktop_discovery import discovery_available

    if not discovery_available():
        return jsonify({'success': False, 'error': '仅支持 Windows'}), 400
    try:
        from desktop_app_catalog import ensure_catalog_built
        from desktop_discovery import invalidate_discovery_cache

        invalidate_discovery_cache()
        data = ensure_catalog_built(force=True)
        return jsonify({
            'success': True,
            'app_count': len(data.get('apps') or []),
            'built_at': data.get('built_at'),
        })
    except Exception as e:
        uat_logger.log_exception('api_desktop_catalog_refresh', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/desktop/window-spec', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_desktop_window_spec():
    """根据 hwnd 生成 attach_window 用的 desktop_spec（写入步骤，不依赖 .env）。"""
    data = request.get_json(silent=True) or {}
    try:
        hwnd = int(data.get('hwnd'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '请提供整数 hwnd'}), 400
    try:
        from desktop_discovery import attachment_spec_for_window, discovery_available

        if not discovery_available():
            return jsonify({'success': False, 'error': '仅支持 Windows'}), 400
        spec, title = attachment_spec_for_window(hwnd)
        return jsonify({
            'success': True,
            'desktop_spec': spec,
            'suggested_input': title,
            'process': spec.get('process') or '',
        })
    except Exception as e:
        uat_logger.log_exception('api_desktop_window_spec', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/desktop/resolve-app', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
def api_desktop_resolve_app():
    """按程序名解析本机 exe 路径（PATH / App Paths）。"""
    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': '请提供 name 参数'}), 400
    try:
        from desktop_discovery import (
            discovery_available,
            format_resolve_error,
            resolve_executable_with_meta,
        )

        if not discovery_available():
            return jsonify({'success': False, 'error': '仅支持 Windows'}), 400
        meta = resolve_executable_with_meta(name)
        if not meta.found:
            return jsonify({
                'success': False,
                'error': format_resolve_error(meta),
                'tried': meta.tried,
                'suggestions': meta.suggestions,
            }), 404
        return jsonify({
            'success': True,
            'path': meta.path,
            'name': name,
            'method': meta.method,
            'tried': meta.tried,
        })
    except Exception as e:
        uat_logger.log_exception('api_desktop_resolve_app', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/desktop/machines', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_desktop_machines():
    """远程桌面 Agent 测试机注册（Phase 3）；本地版 DEPLOYMENT_PROFILE=local 默认禁用。"""
    try:
        from desktop_env_config import is_local_deployment

        if is_local_deployment():
            return jsonify({
                'success': False,
                'error': '本地部署模式不提供远程测试机注册；请在用户本机执行桌面步骤（inprocess）。',
                'deployment_profile': 'local',
            }), 403
    except ImportError:
        pass
    if request.method == 'GET':
        machines = db.list_test_machines()
        return jsonify({'success': True, 'machines': machines})
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    agent_url = (data.get('agent_url') or '').strip().rstrip('/')
    if not name or not agent_url:
        return jsonify({'success': False, 'error': 'name 与 agent_url 必填'}), 400
    mid = db.create_test_machine(
        name,
        agent_url,
        (data.get('os_version') or '').strip(),
        (data.get('secret') or '').strip(),
    )
    return jsonify({'success': True, 'machine_id': mid})


_PLAYWRIGHT_BROWSER_OPTIONS = [
    {"id": "chromium", "label_zh": "Chromium（Playwright 内置）", "label_en": "Chromium (bundled)"},
    {"id": "chrome", "label_zh": "Google Chrome", "label_en": "Google Chrome"},
    {"id": "edge", "label_zh": "Microsoft Edge", "label_en": "Microsoft Edge"},
    {"id": "firefox", "label_zh": "Firefox", "label_en": "Firefox"},
    {"id": "webkit", "label_zh": "WebKit", "label_en": "WebKit"},
]


@app.route('/api/playwright/browser', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_playwright_browser():
    """
    主自动化会话使用的 Playwright 浏览器（Chromium / Chrome / Edge 等）。
    偏好保存在 Flask session；未保存时遵循环境变量 PLAYWRIGHT_BROWSER。
    使用系统 Chrome/Edge 前请在部署环境执行: playwright install chrome / playwright install msedge
    """
    if request.method == 'GET':
        raw = session.get('playwright_browser_engine') or os.environ.get('PLAYWRIGHT_BROWSER') or 'chromium'
        cur = normalize_playwright_browser_name(str(raw))
        return jsonify({
            'success': True,
            'current': cur,
            'options': _PLAYWRIGHT_BROWSER_OPTIONS,
            'env_default': (os.environ.get('PLAYWRIGHT_BROWSER') or '').strip() or 'chromium',
            'running_engine': automation.get_browser_engine(),
        })
    data = request.get_json(silent=True) or {}
    b = (data.get('browser') or data.get('engine') or '').strip()
    if not b:
        return jsonify({'success': False, 'error': 'browser 不能为空'}), 400
    norm = normalize_playwright_browser_name(b)
    session['playwright_browser_engine'] = norm
    session.modified = True
    automation.set_browser_engine(norm)
    return jsonify({
        'success': True,
        'current': norm,
        'hint': '已保存。若自动化浏览器已在运行，下次打开/导航时会切换引擎；也可关闭浏览器窗口后重试。',
    })


@app.route('/api/embedded-browser/status', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_embedded_browser_status():
    """远程 Chromium 网关是否可用（供 AI 测试页展示「远程画布」按钮）。"""
    _, _, pub = embedded_gateway_config()
    return jsonify({
        'success': True,
        'enabled': embedded_gateway_enabled(),
        'ws_base_configured': bool(pub),
    })


@app.route('/api/embedded-browser/session', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_embedded_browser_session_create():
    """
    在独立网关中创建 Playwright Chromium 会话，返回 WebSocket URL（CDP 画面 + 输入）。
    需配置：EMBEDDED_BROWSER_GATEWAY_URL、EMBEDDED_BROWSER_GATEWAY_SECRET、
    EMBEDDED_BROWSER_PUBLIC_WS_BASE（浏览器可达的 ws:// 或 wss:// 前缀）。
    """
    if not embedded_gateway_enabled():
        return jsonify({
            'success': False,
            'error': '内嵌网关未配置：请设置 EMBEDDED_BROWSER_GATEWAY_URL 与 EMBEDDED_BROWSER_GATEWAY_SECRET',
        }), 503
    _, _, pub = embedded_gateway_config()
    if not pub:
        return jsonify({
            'success': False,
            'error': '请配置 EMBEDDED_BROWSER_PUBLIC_WS_BASE（例如 ws://127.0.0.1:8765）',
        }), 503
    data = request.get_json(silent=True) or {}
    initial_url = (data.get('initial_url') or '').strip()
    gw_body = {'user_id': current_user.id, 'initial_url': initial_url}
    br = (data.get('browser') or data.get('engine') or '').strip()
    gw_body['browser'] = normalize_playwright_browser_name(br) if br else automation.get_browser_engine()
    j, err = embedded_gateway_json(
        'POST',
        '/internal/session',
        user_id=current_user.id,
        body=gw_body,
    )
    if j is None:
        return jsonify({'success': False, 'error': err or '网关不可用'}), 502
    sid = j.get('session_id')
    tok = j.get('ws_token')
    if not sid or not tok:
        detail = j.get('detail')
        return jsonify({'success': False, 'error': str(detail or err or '网关返回无效')}), 502
    ws_url = f"{pub}/ws/{sid}?token={tok}"
    cdp_ws = (j.get("cdp_browser_ws") or "").strip() or None
    if cdp_ws:
        uat_logger.info(f"embedded session {sid} cdp_ws prefix={cdp_ws[:48]}…")
    return jsonify(
        {
            "success": True,
            "session_id": sid,
            "ws_url": ws_url,
            "cdp_ws_url": cdp_ws,
        }
    )


@app.route("/api/embedded-browser/session/<session_id>/navigate", methods=["POST"])
@login_required
@role_required("admin", "tester", "project_manager", "test_lead")
@api_error_handler
@log_api_request
def api_embedded_browser_session_navigate(session_id: str):
    """在已有网关会话中导航（供 AI 测试页「打开」走远程 Chromium，无需 iframe / 画布 WS）。"""
    if not embedded_gateway_enabled():
        return jsonify({"success": False, "error": "内嵌网关未配置"}), 503
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"success": False, "error": "url 需为 http(s) 完整地址"}), 400
    j, err = embedded_gateway_json(
        "POST",
        f"/internal/session/{session_id}/run-steps",
        user_id=current_user.id,
        body={
            "steps": [
                {"action": "navigate", "url": url, "description": "AI test page open"},
            ],
        },
        timeout_sec=120.0,
    )
    if err or not isinstance(j, dict) or not j.get("success"):
        return jsonify(
            {
                "success": False,
                "error": str(err or (j or {}).get("detail") or "远程导航失败"),
            }
        ), 502
    rs0 = (j.get("results") or [{}])[0]
    if not rs0.get("ok"):
        return jsonify(
            {"success": False, "error": str(rs0.get("error") or "远程导航步骤失败")}
        ), 502
    return jsonify({"success": True})


@app.route('/api/embedded-browser/session/<session_id>', methods=['DELETE'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_embedded_browser_session_delete(session_id: str):
    if not embedded_gateway_enabled():
        return jsonify({'success': False, 'error': '内嵌网关未配置'}), 503
    j, err = embedded_gateway_json(
        'DELETE',
        f'/internal/session/{session_id}',
        user_id=current_user.id,
    )
    if err and not (j and j.get('success')):
        return jsonify({'success': False, 'error': err or '删除失败'}), 502
    return jsonify({'success': True})


@app.route('/api/embedded-browser/session/<session_id>/inspect', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_embedded_browser_session_inspect(session_id: str):
    """从远程 Chromium 会话拉取可交互结构（与 /api/browser/inspect 字段风格一致）。"""
    if not embedded_gateway_enabled():
        return jsonify({'success': False, 'error': '内嵌网关未配置'}), 503
    j, err = embedded_gateway_json(
        'GET',
        f'/internal/session/{session_id}/inspect',
        user_id=current_user.id,
    )
    if j is None or not j.get('success'):
        return jsonify({'success': False, 'error': err or (j or {}).get('detail', 'inspect 失败')}), 502
    return jsonify({'success': True, 'data': j.get('data')})


@app.route('/api/embedded-browser/session/<session_id>/diagnostics', methods=['GET'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_embedded_browser_session_diagnostics(session_id: str):
    if not embedded_gateway_enabled():
        return jsonify({'success': False, 'error': '内嵌网关未配置'}), 503
    j, err = embedded_gateway_json(
        'GET',
        f'/internal/session/{session_id}/diagnostics',
        user_id=current_user.id,
    )
    if j is None or not j.get('success'):
        return jsonify({'success': False, 'error': err or 'diagnostics 失败'}), 502
    return jsonify({'success': True, 'data': j.get('data')})


@app.route('/api/browser/viewport', methods=['GET'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_viewport():
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    v = sync_get_viewport_size()
    return jsonify({'success': True, 'viewport': v})


@app.route('/api/browser/click', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_click():
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    data = request.get_json(silent=True) or {}
    try:
        x = float(data.get('x'))
        y = float(data.get('y'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '需要有效的 x, y（视口坐标，与截图像素一致）'}), 400
    btn = (data.get('button') or 'left')
    dbl = bool(data.get('double'))
    try:
        sync_browser_mouse_click(x, y, button=btn, click_count=2 if dbl else 1)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/browser/wheel', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_wheel():
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    data = request.get_json(silent=True) or {}
    try:
        sync_browser_mouse_wheel(float(data.get('delta_x', 0)), float(data.get('delta_y', 0)))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/browser/type', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_type_text():
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    data = request.get_json(silent=True) or {}
    t = (data.get('text') or '')
    try:
        sync_browser_keyboard_type(t)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/browser/key', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_key_press():
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    data = request.get_json(silent=True) or {}
    k = (data.get('key') or 'Enter')
    try:
        sync_browser_keyboard_press(k)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/browser/pick', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
def api_browser_pick_element():
    if not sync_automation_session_usable():
        return jsonify({'success': False, 'error': '浏览器未启动'}), 400
    data = request.get_json(silent=True) or {}
    try:
        x = float(data.get('x'))
        y = float(data.get('y'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '需要有效的 x, y'}), 400
    try:
        el = sync_element_info_at_point(x, y)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'element': el})


@app.route('/api/browser/ai-analyze', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_browser_ai_analyze():
    """根据当前已打开页的可交互结构生成用例（写入侧栏/工作台预览由前端处理）。"""
    data = request.get_json(silent=True) or {}
    embedded_sid = (data.get('embedded_session_id') or data.get('remote_session_id') or '').strip()
    hint = (data.get('hint') or '').strip()
    project_name = (data.get('project_name') or '').strip()
    selected_model = (data.get('model') or '').strip() or _get_active_local_model()
    profile, legacy_model = _resolve_inference_profile(selected_model)

    if embedded_sid:
        if not embedded_gateway_enabled():
            return jsonify({'success': False, 'error': '远程 Chromium 网关未配置'}), 503
        j, err = embedded_gateway_json(
            'GET',
            f'/internal/session/{embedded_sid}/inspect',
            user_id=current_user.id,
        )
        if not j or not j.get('success'):
            detail = (j or {}).get('detail')
            return jsonify({
                'success': False,
                'error': str(err or detail or '远程会话无效或已过期，请重新连接「远程画布」'),
            }), 502
        snap = j.get('data') or {}
        items = snap.get('items') or []
        page_body = {
            'url': snap.get('url', ''),
            'title': snap.get('title', ''),
            'viewport': snap.get('viewport', {}),
            'items': items,
            'source': snap.get('source') or 'embedded_gateway',
        }
    else:
        if not sync_automation_session_usable():
            return jsonify({'success': False, 'error': '浏览器未启动，请先打开页面或使用远程画布'}), 400
        try:
            snap = sync_get_interactive_page_snapshot(120)
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
        items = snap.get('items') or []
        page_body = {
            'url': snap.get('url', ''),
            'title': snap.get('title', ''),
            'viewport': snap.get('viewport', {}),
            'items': items,
        }

    ctx_label = (
        '用户已在远程 Chromium（服务端嵌入式网关）中打开的当前页面'
        if embedded_sid
        else '用户已在内置浏览器/主 Playwright 会话中打开的当前页面'
    )
    from ai_page_probe import probe_registry_from_interactive_snapshot

    page_snapshot_txt, probe_registry, probe_pu = probe_registry_from_interactive_snapshot(snap)
    goal_lines = [
        f'请根据以下「{ctx_label}」生成可执行 Web 用例与步骤。',
        f"页面标题: {page_body['title']}",
        f"URL: {page_body['url']}",
    ]
    if hint:
        goal_lines.append('用户补充的测试目标: ' + hint)
    goal = '\n'.join(goal_lines)
    probe_url = (probe_pu or page_body.get('url') or '').strip() or None
    dpack = _ai_build_dom_pack(snap, embed_remote=bool(embedded_sid))
    mem_ctx = _ai_memory_context_block(
        current_user.id, goal, probe_url=probe_url or page_body.get('url') or '', project_name=project_name
    )
    try:
        generated = local_ai_service.generate_case_and_steps(
            goal,
            project_name,
            model=legacy_model,
            profile=profile,
            page_snapshot=page_snapshot_txt or None,
            probe_registry=probe_registry if probe_registry else None,
            probe_url=probe_url,
            memory_context=mem_ctx or None,
            dom_context_pack=dpack or None,
        )
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'hint': '可先执行: ollama serve，并确认模型已拉取。',
        }), 503
    generated, norm_warnings = apply_step_normalization_to_plan(generated)
    generated, norm_warnings = _merge_ai_locator_resolution(generated, snap, norm_warnings)
    log_ai_plan_to_audit(
        current_user.id,
        current_user.username,
        'AI_PLAN_PAGE_GENERATE',
        generated,
        request.remote_addr,
    )
    return jsonify({
        'success': True,
        'plan': generated,
        'page_snapshot': page_body,
        'warnings': norm_warnings,
    })


# API: 启用元素选择模式（已废弃，转发提示）
@app.route('/api/enable_element_selection', methods=['POST'])
@api_error_handler
@log_api_request
def api_enable_element_selection():
    return _legacy_visual_picker_removed()

# API: 禁用元素选择模式（已废弃）
@app.route('/api/disable_element_selection', methods=['POST'])
@api_error_handler
@log_api_request
def api_disable_element_selection():
    return _legacy_visual_picker_removed()

# API: 获取选中的元素信息（已废弃）
@app.route('/api/get_selected_element', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_selected_element():
    return _legacy_visual_picker_removed()

# API: 从选定元素提取JSON数据
@app.route('/api/extract_json_from_selected_element', methods=['GET'])
@api_error_handler
@log_api_request
def api_extract_json_from_selected_element():
    try:
        json_data = sync_extract_json_from_selected_element()
        return jsonify({'success': True, 'json_data': json_data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== 项目管理API ====================

# API: 创建项目
@app.route('/api/projects', methods=['POST'])
@login_required
@role_required('admin', 'tester')
@api_error_handler
@log_api_request
@audit_log('CREATE_PROJECT', 'project')
def api_create_project():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '')
    description = data.get('description', '')

    if not name:
        return jsonify({'error': '项目名称不能为空'}), 400

    # 检查项目数量限制
    _db = Database()
    user_projects = _db.get_user_projects(current_user.id)
    limits = license_manager.get_limits()
    if limits['max_projects'] != -1 and len(user_projects) >= limits['max_projects']:
        return jsonify({
            'success': False,
            'error': f'已达到项目数量限制（{limits["max_projects"]}个）。请升级到企业版。'
        }), 403

    project_id = db.create_project(name, description)

    # 将创建者添加为项目所有者
    _db.add_project_member(project_id, current_user.id, role='owner')

    return jsonify({'success': True, 'project_id': project_id})

# API: 获取所有项目
@app.route('/api/projects', methods=['GET'])
@login_required
@api_error_handler
@log_api_request
def api_get_projects():
    # 只返回用户有权限的项目
    _db = Database()
    projects = _db.get_user_projects(current_user.id)
    return jsonify({'projects': projects})

# API: 获取单个项目
@app.route('/api/projects/<int:project_id>', methods=['GET'])
@login_required
@project_access_required(min_role='viewer')
@api_error_handler
@log_api_request
def api_get_project(project_id):
    project = db.get_project(project_id)
    if not project:
        return jsonify({'error': '项目不存在'}), 404
    return jsonify({'project': project})

# API: 更新项目
@app.route('/api/projects/<int:project_id>', methods=['PUT'])
@login_required
@project_access_required(min_role='editor')
@api_error_handler
@log_api_request
@audit_log('UPDATE_PROJECT', 'project')
def api_update_project(project_id):
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    description = data.get('description')

    success = db.update_project(project_id, name, description)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '更新项目失败'}), 400

# API: 删除项目
@app.route('/api/projects/<int:project_id>', methods=['DELETE'])
@login_required
@project_access_required(min_role='owner')
@api_error_handler
@log_api_request
@audit_log('DELETE_PROJECT', 'project')
def api_delete_project(project_id):
    success = db.delete_project(project_id)

    if success:
        try:
            db.prune_orphan_run_history()
        except Exception:
            pass
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '删除项目失败'}), 400

def _app_case_type(case_dict) -> str:
    if not case_dict:
        return "ui"
    t = (case_dict.get("case_type") or "ui").strip().lower()
    return "api" if t == "api" else "ui"


def _validate_step_action_for_case(case_dict, action: str, automation_layer: str = "web"):
    """服务端步骤写入校验：Web 用例禁止 api_request；接口用例仅允许 api_request；按层校验动作。"""
    if not case_dict:
        return "用例不存在"
    act = (action or "").strip()
    ct = _app_case_type(case_dict)
    if ct == "ui" and act == "api_request":
        return "Web 用例不允许添加接口步骤，请在「接口测试」模块中维护接口用例"
    if ct == "api" and act != "api_request":
        return "接口用例仅允许「接口请求」步骤（api_request）"
    if ct == "ui":
        try:
            from desktop_automation import validate_step_for_layer

            layer = (automation_layer or "web").strip().lower()
            layer_err = validate_step_for_layer(act, layer)
            if layer_err:
                return layer_err
        except ImportError:
            pass
    return None


# API: 获取项目下的所有测试用例
@app.route('/api/projects/<int:project_id>/cases', methods=['GET'])
@login_required
@project_access_required(min_role='viewer')
@api_error_handler
@log_api_request
def api_get_project_cases(project_id):
    raw_ct = (request.args.get("case_type") or "ui").strip().lower()
    case_type = "api" if raw_ct == "api" else "ui"
    cases = db.get_project_cases(project_id, case_type=case_type)
    return jsonify({'cases': cases})

# ==================== 测试用例管理API（新版本） ====================

# API: 创建测试用例（关联到项目）
@app.route('/api/cases', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('CREATE_CASE', 'case')
def api_create_case_v2():
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    name = data.get('name', '')
    url = data.get('url', '')
    description = data.get('description', '')
    precondition = data.get('precondition', '')
    expected_result = data.get('expected_result', '')
    case_type_raw = (data.get('case_type') or 'ui').strip().lower()
    case_type = 'api' if case_type_raw == 'api' else 'ui'
    
    if not project_id:
        return jsonify({'error': '项目ID不能为空'}), 400
    if not name:
        return jsonify({'error': '用例名称不能为空'}), 400
    
    # 检查项目访问权限
    _db = Database()
    if not _db.check_project_access(current_user.id, project_id, 'editor'):
        return jsonify({'success': False, 'error': '无权限在此项目创建用例'}), 403
    
    # ===== 用例数量限制检查 =====
    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    
    # 获取项目当前用例数量（仅 COUNT，避免全量拉取用例与步骤 JOIN）
    current_case_count = _db.get_project_case_count(project_id)
    
    if limits['max_cases_per_project'] != -1 and current_case_count >= limits['max_cases_per_project']:
        return jsonify({
            'success': False,
            'error': f'已达到项目用例数量限制（{limits["max_cases_per_project"]}个）。请升级至团队版或企业版以提升配额。',
            'limit_reached': True,
            'current_count': current_case_count,
            'limit': limits['max_cases_per_project'],
            'upgrade_url': '/license'
        }), 403
    
    # 记录创建用例计数（仅免费版）
    if license_info.license_type == LicenseType.FREE.value:
        _db.increment_created_cases(current_user.id)
    
    case_id = db.create_test_case_v2(
        project_id, name, url, description, precondition, expected_result, case_type=case_type
    )
    return jsonify({'success': True, 'case_id': case_id})

# API: 获取测试用例详情（新版本）
@app.route('/api/cases/<int:case_id>', methods=['GET'])
@login_required
@api_error_handler
@log_api_request
def api_get_case_v2(case_id):
    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'error': '测试用例不存在'}), 404
    
    # 检查项目访问权限
    _db = Database()
    if case.get('project_id') and not _db.check_project_access(current_user.id, case['project_id'], 'viewer'):
        return jsonify({'success': False, 'error': '无权限访问此用例'}), 403
    
    return jsonify({'test_case': case})

# API: 更新测试用例（新版本）
@app.route('/api/cases/<int:case_id>', methods=['PUT'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('UPDATE_CASE', 'case')
def api_update_case_v2(case_id):
    # 检查用例是否存在并获取所属项目
    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'error': '测试用例不存在'}), 404
    
    # 检查项目访问权限
    _db = Database()
    if case.get('project_id') and not _db.check_project_access(current_user.id, case['project_id'], 'editor'):
        return jsonify({'success': False, 'error': '无权限修改此用例'}), 403
    
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    url = data.get('url')
    description = data.get('description')
    precondition = data.get('precondition')
    expected_result = data.get('expected_result')
    
    success = db.update_test_case_v2(case_id, name, url, description, precondition, expected_result)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '更新测试用例失败'}), 400

# API: 删除测试用例（新版本）
@app.route('/api/cases/<int:case_id>', methods=['DELETE'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('DELETE_CASE', 'case')
def api_delete_case_v2(case_id):
    # 检查用例是否存在并获取所属项目
    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'error': '测试用例不存在'}), 404
    
    # 检查项目访问权限（删除需要editor权限）
    _db = Database()
    if case.get('project_id') and not _db.check_project_access(current_user.id, case['project_id'], 'editor'):
        return jsonify({'success': False, 'error': '无权限删除此用例'}), 403
    
    success = db.delete_test_case_v2(case_id)
    
    if success:
        try:
            db.prune_orphan_run_history()
        except Exception:
            pass
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '删除测试用例失败'}), 400

# ==================== 测试步骤管理API ====================

# API: 获取测试用例的所有步骤
@app.route('/api/cases/<int:case_id>/steps', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_case_steps(case_id):
    # 获取分页参数
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 10, type=int)
    
    # 确保参数有效
    page = max(1, page)
    page_size = max(1, min(page_size, 100))  # 限制最大页面大小为100
    
    steps, total = db.get_case_steps_paginated(case_id, page, page_size)
    
    return jsonify({
        'steps': steps,
        'total': total,
        'page': page,
        'page_size': page_size
    })

# API: 获取单个测试步骤详情
@app.route('/api/steps/<int:step_id>', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_step(step_id):
    step = db.get_test_step(step_id)
    if not step:
        return jsonify({'error': '测试步骤不存在'}), 404
    return jsonify({'step': step})

# API: 创建测试步骤
@app.route('/api/steps', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_create_step():
    data = request.get_json(silent=True) or {}
    case_id = data.get('case_id')
    action = data.get('action', '')
    selector_type = data.get('selector_type', '')
    selector_value = data.get('selector_value', '')
    input_value = data.get('input_value', '')
    description = data.get('description', '')
    step_order = data.get('step_order')  # 不设置默认值，让它为None
    page_name = data.get('page_name', '')
    swipe_x = data.get('swipe_x', '')
    swipe_y = data.get('swipe_y', '')
    url = data.get('url', '')
    enter_iframe = data.get('enter_iframe', False)
    iframe_selector = data.get('iframe_selector', '')
    compare_type = data.get('compare_type', 'equals')
    locator_candidates = data.get('locator_candidates', '')
    if locator_candidates is not None and not isinstance(locator_candidates, str):
        locator_candidates = json.dumps(locator_candidates, ensure_ascii=False)
    click_repeat_count = _norm_click_repeat_count(data.get('click_repeat_count')) if action == 'click' else 1
    api_spec = data.get('api_spec', '')
    if api_spec is not None and not isinstance(api_spec, str):
        api_spec = json.dumps(api_spec, ensure_ascii=False)
    automation_layer = (data.get('automation_layer') or 'web').strip().lower()
    desktop_spec = data.get('desktop_spec', '')
    if desktop_spec is not None and not isinstance(desktop_spec, str):
        desktop_spec = json.dumps(desktop_spec, ensure_ascii=False)
    
    if not case_id:
        return jsonify({'success': False, 'error': '用例ID不能为空'}), 400
    if not action:
        return jsonify({'success': False, 'error': '操作类型不能为空'}), 400

    case_row = db.get_test_case_v2(int(case_id))
    if not case_row:
        return jsonify({'success': False, 'error': '用例不存在'}), 404
    pid = case_row.get('project_id')
    if pid and not db.check_project_access(current_user.id, int(pid), 'editor'):
        return jsonify({'success': False, 'error': '无权限修改此用例'}), 403
    step_err = _validate_step_action_for_case(case_row, action, automation_layer)
    if step_err:
        return jsonify({'success': False, 'error': step_err}), 422
    
    step_id = db.create_test_step(case_id, action, selector_type, selector_value, 
                                  input_value, description, step_order, page_name,
                                  swipe_x, swipe_y, url, enter_iframe, iframe_selector, compare_type,
                                  locator_candidates or '', click_repeat_count=click_repeat_count,
                                  api_spec=api_spec or '',
                                  automation_layer=automation_layer,
                                  desktop_spec=desktop_spec or '')
    return jsonify({'success': True, 'step_id': step_id})

# API: 更新测试步骤
@app.route('/api/steps/<int:step_id>', methods=['PUT'])
@api_error_handler
@log_api_request
def api_update_step(step_id):
    data = request.get_json(silent=True) or {}
    step_row = db.get_test_step(step_id)
    if not step_row:
        return jsonify({'error': '测试步骤不存在'}), 404
    case_row = db.get_test_case_v2(int(step_row['case_id']))
    action = data.get('action')
    eff_action = action if action is not None else step_row.get('action')
    eff_layer = data.get('automation_layer')
    if eff_layer is None:
        eff_layer = step_row.get('automation_layer') or 'web'
    step_err = _validate_step_action_for_case(case_row, eff_action, eff_layer)
    if step_err:
        return jsonify({'error': step_err}), 422
    selector_type = data.get('selector_type')
    selector_value = data.get('selector_value')
    input_value = data.get('input_value')
    description = data.get('description')
    step_order = data.get('step_order')
    enter_iframe = data.get('enter_iframe')
    iframe_selector = data.get('iframe_selector')
    compare_type = data.get('compare_type')
    locator_candidates = data.get('locator_candidates')
    if locator_candidates is not None and not isinstance(locator_candidates, str):
        locator_candidates = json.dumps(locator_candidates, ensure_ascii=False)
    if action is not None:
        if action == 'click':
            click_repeat_count = _norm_click_repeat_count(data.get('click_repeat_count', 1))
        else:
            click_repeat_count = 1
    elif 'click_repeat_count' in data:
        click_repeat_count = _norm_click_repeat_count(data.get('click_repeat_count'))
    else:
        click_repeat_count = None

    api_spec = data.get('api_spec') if 'api_spec' in data else None
    if api_spec is not None and not isinstance(api_spec, str):
        api_spec = json.dumps(api_spec, ensure_ascii=False)
    automation_layer = data.get('automation_layer') if 'automation_layer' in data else None
    desktop_spec = data.get('desktop_spec') if 'desktop_spec' in data else None
    if desktop_spec is not None and not isinstance(desktop_spec, str):
        desktop_spec = json.dumps(desktop_spec, ensure_ascii=False)
    _sel_eff = (
        (selector_type if selector_type is not None else step_row.get('selector_type') or '')
        .strip()
        .lower()
    )
    if _sel_eff == 'visual':
        automation_layer = 'desktop'

    success = db.update_test_step(step_id, action, selector_type, selector_value,
                                   input_value, description, step_order, enter_iframe, iframe_selector, compare_type,
                                   locator_candidates, click_repeat_count, api_spec=api_spec,
                                   automation_layer=automation_layer, desktop_spec=desktop_spec)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '更新测试步骤失败'}), 400

# API: 删除测试步骤
@app.route('/api/steps/<int:step_id>', methods=['DELETE'])
@login_required
@api_error_handler
@log_api_request
def api_delete_step(step_id):
    step_row = db.get_test_step(step_id)
    if not step_row:
        return jsonify({'success': False, 'error': '测试步骤不存在'}), 404
    case_row = db.get_test_case_v2(int(step_row['case_id']))
    if not case_row:
        return jsonify({'success': False, 'error': '用例不存在'}), 404
    pid = case_row.get('project_id')
    if pid and not db.check_project_access(current_user.id, int(pid), 'editor'):
        return jsonify({'success': False, 'error': '无权限删除此步骤'}), 403
    success = db.delete_test_step(step_id)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '删除测试步骤失败'}), 400

# API: 删除测试用例的所有步骤
@app.route('/api/cases/<int:case_id>/steps', methods=['DELETE'])
@api_error_handler
@log_api_request
def api_delete_case_steps(case_id):
    success = db.delete_case_steps(case_id)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '删除测试用例步骤失败'}), 400

# API: 更新测试步骤顺序
@app.route('/api/cases/<int:case_id>/steps/order', methods=['PUT'])
@api_error_handler
@log_api_request
def api_update_step_order(case_id):
    data = request.get_json(silent=True) or {}
    steps = data.get('steps', [])
    
    success = db.update_step_order(case_id, steps)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '更新步骤顺序失败'}), 400


@app.route('/api-testing')
@login_required
def api_testing_page():
    return render_template('api_testing.html')


@app.route('/api-testing/case/<int:case_id>')
@login_required
def api_case_detail_page(case_id):
    case = db.get_test_case_v2(case_id)
    if not case:
        return redirect(url_for('api_testing_page', err='case_not_found'))
    if _app_case_type(case) != 'api':
        return redirect(url_for('api_testing_page', err='not_api_case'))
    pid = case.get('project_id')
    _db = Database()
    if pid and not _db.check_project_access(current_user.id, int(pid), 'viewer'):
        return redirect(url_for('api_testing_page', err='no_project_access'))
    return render_template(
        'api_case_detail.html',
        case_id=case_id,
        project_id=int(pid) if pid else 0,
        case_name=(case.get('name') or '').strip() or ('#' + str(case_id)),
    )


@app.route('/api/cases/<int:case_id>/migrate-api-steps', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_migrate_api_steps(case_id):
    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'success': False, 'error': '用例不存在'}), 404
    _db = Database()
    pid = case.get('project_id')
    if pid and not _db.check_project_access(current_user.id, int(pid), 'editor'):
        return jsonify({'success': False, 'error': '无权限修改此用例'}), 403
    if _app_case_type(case) != 'ui':
        return jsonify({'success': False, 'error': '仅可从 Web 用例迁移接口步骤'}), 400
    data = request.get_json(silent=True) or {}
    tid = data.get('target_api_case_id')
    if tid is not None:
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            tid = None
    tname = (data.get('target_api_case_name') or '').strip() or None
    r = db.migrate_api_steps_from_ui_case(case_id, target_api_case_id=tid, target_api_case_name=tname)
    if r.get('success'):
        return jsonify(r)
    return jsonify(r), 400


@app.route('/api/api-cases/from-ui-case', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_create_api_case_from_ui_case():
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    source_ui_case_id = data.get('source_ui_case_id')
    migrate_steps = bool(data.get('migrate_api_steps'))
    name = (data.get('name') or '').strip()
    if not project_id:
        return jsonify({'success': False, 'error': 'project_id 不能为空'}), 400
    _db = Database()
    if not _db.check_project_access(current_user.id, int(project_id), 'editor'):
        return jsonify({'success': False, 'error': '无权限在此项目创建用例'}), 403

    limits = license_manager.get_limits()
    lic = license_manager.get_current_license()
    cur_cnt = _db.get_project_case_count(int(project_id))
    if limits['max_cases_per_project'] != -1 and cur_cnt >= limits['max_cases_per_project']:
        return jsonify({
            'success': False,
            'error': f'已达到项目用例数量限制（{limits["max_cases_per_project"]}个）。',
            'limit_reached': True,
        }), 403

    def _bump_free_created():
        if lic.license_type == LicenseType.FREE.value:
            _db.increment_created_cases(current_user.id)

    if not source_ui_case_id:
        if not name:
            return jsonify({'success': False, 'error': '新建接口用例时 name 不能为空'}), 400
        case_id = _db.create_test_case_v2(
            int(project_id),
            name,
            data.get('url', ''),
            data.get('description', ''),
            data.get('precondition', ''),
            data.get('expected_result', ''),
            case_type='api',
        )
        _bump_free_created()
        return jsonify({'success': True, 'case_id': case_id})
    src = _db.get_test_case_v2(int(source_ui_case_id))
    if not src:
        return jsonify({'success': False, 'error': '源 Web 用例不存在'}), 404
    if int(src.get('project_id') or 0) != int(project_id):
        return jsonify({'success': False, 'error': '源用例必须属于所选项目'}), 400
    if _app_case_type(src) != 'ui':
        return jsonify({'success': False, 'error': '源用例必须是 Web 用例'}), 400
    new_name = name or f"{src.get('name') or '用例'} (接口)"
    new_id = _db.create_test_case_v2(
        int(project_id),
        new_name,
        src.get('url') or '',
        src.get('description') or '',
        src.get('precondition') or '',
        src.get('expected_result') or '',
        case_type='api',
    )
    _bump_free_created()
    if migrate_steps:
        m = _db.migrate_api_steps_from_ui_case(int(source_ui_case_id), target_api_case_id=new_id)
        if not m.get('success'):
            try:
                _db.delete_test_case_v2(new_id)
            except Exception:
                pass
            return jsonify({'success': False, 'error': m.get('error', '迁移失败')}), 400
        return jsonify({'success': True, 'case_id': m.get('target_api_case_id', new_id), 'migrated': m})
    return jsonify({'success': True, 'case_id': new_id})


@app.route('/api/api-cases/<int:case_id>/duplicate', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_duplicate_api_case(case_id):
    src = db.get_test_case_v2(case_id)
    if not src:
        return jsonify({'success': False, 'error': '用例不存在'}), 404
    pid = src.get('project_id')
    if not pid:
        return jsonify({'success': False, 'error': '用例无归属项目'}), 400
    _db = Database()
    if not _db.check_project_access(current_user.id, int(pid), 'editor'):
        return jsonify({'success': False, 'error': '无权限'}), 403
    if _app_case_type(src) != 'api':
        return jsonify({'success': False, 'error': '仅可复制接口用例'}), 400
    limits = license_manager.get_limits()
    lic = license_manager.get_current_license()
    cur_cnt = _db.get_project_case_count(int(pid))
    if limits['max_cases_per_project'] != -1 and cur_cnt >= limits['max_cases_per_project']:
        return jsonify({
            'success': False,
            'error': f'已达到项目用例数量限制（{limits["max_cases_per_project"]}个）。',
            'limit_reached': True,
        }), 403
    data = request.get_json(silent=True) or {}
    new_name = (data.get('name') or '').strip() or f"{src.get('name') or '接口用例'} 副本"
    nid = _db.duplicate_test_case_with_steps(case_id, new_name, case_type='api')
    if not nid:
        return jsonify({'success': False, 'error': '复制失败'}), 500
    if lic.license_type == LicenseType.FREE.value:
        _db.increment_created_cases(current_user.id)
    return jsonify({'success': True, 'case_id': nid})


@app.route('/api/api-cases/<int:case_id>/run', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_run_api_case(case_id):
    start_time = time.time()
    _db = Database()
    user_id = current_user.id
    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    is_free_user = license_info.license_type == LicenseType.FREE.value
    today_stats = _db.get_user_usage_stats(user_id)
    current_count = today_stats.get('execution_count', 0) if today_stats else 0
    daily_limit = limits.get('max_executions_per_day', -1)
    if is_free_user:
        DAILY_LIMIT = 10
        if current_count >= DAILY_LIMIT:
            return jsonify({
                'success': False,
                'error': f'今日执行次数已达上限（{DAILY_LIMIT}次）。请升级至团队版解除限制。',
                'limit_reached': True,
            }), 403
    elif daily_limit > 0 and current_count >= daily_limit:
        return jsonify({
            'success': False,
            'error': f'今日执行次数已达上限（{daily_limit}次）。',
            'limit_reached': True,
        }), 403
    _db.increment_execution_count(user_id)
    case = _db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'error': '测试用例不存在'}), 404
    if case.get('project_id') and not _db.check_project_access(user_id, int(case['project_id']), 'viewer'):
        return jsonify({'success': False, 'error': '无权限执行此用例'}), 403
    if _app_case_type(case) != 'api':
        return jsonify({
            'success': False,
            'error': '该用例不是接口用例，请使用 Web 用例的「运行」按钮执行浏览器自动化。',
        }), 400
    body = request.get_json(silent=True) or {}
    raw_step_ids = body.get('step_ids')
    step_ids_param = None
    if raw_step_ids is not None:
        if not isinstance(raw_step_ids, list):
            return jsonify({'success': False, 'error': 'step_ids 须为非空整数数组'}), 400
        try:
            step_ids_param = [int(x) for x in raw_step_ids]
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'step_ids 须为整数'}), 400
        if not step_ids_param:
            return jsonify({'success': False, 'error': '请至少选择一个要执行的接口步骤'}), 400
        all_steps = _db.get_case_steps(case_id, page=1, page_size=9999)
        valid = {int(s['id']) for s in (all_steps or []) if s.get('id') is not None}
        step_ids_param = list(dict.fromkeys([sid for sid in step_ids_param if sid in valid]))
        if not step_ids_param:
            return jsonify({
                'success': False,
                'error': '所选步骤不属于本用例或不存在，请刷新后重试。',
            }), 400
    payload = sync_run_api_case_for_batch(
        case_id, _db, execution_context=None, step_ids=step_ids_param
    )
    duration = round(time.time() - start_time, 2)
    out = {
        'success': payload.get('status') == 'success',
        'status': payload.get('status'),
        'duration': duration,
        'case_id': case_id,
        'run_history_id': payload.get('run_history_id'),
        'step_results': payload.get('step_results'),
    }
    if payload.get('status') == 'warning':
        out['warning'] = payload.get('warning')
    if payload.get('status') == 'error':
        out['error'] = payload.get('error') or '接口用例执行失败'
    return jsonify(out)


@app.route('/api/api-cases/dry-run-request', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_dry_run_api_request():
    """试发当前表单中的单条 HTTP 规格（不写库），用于弹窗内查看响应与断言结果。"""
    data = request.get_json(silent=True) or {}
    case_id = data.get('case_id')
    if case_id is None:
        return jsonify({'success': False, 'error': 'case_id 不能为空'}), 400
    try:
        case_id = int(case_id)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'case_id 无效'}), 400

    _db = Database()
    case = _db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'success': False, 'error': '用例不存在'}), 404
    if case.get('project_id') and not _db.check_project_access(
        current_user.id, int(case['project_id']), 'viewer'
    ):
        return jsonify({'success': False, 'error': '无权限'}), 403
    if _app_case_type(case) != 'api':
        return jsonify({'success': False, 'error': '仅支持接口用例'}), 400

    spec = data.get('api_spec')
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError:
            return jsonify({'success': False, 'error': 'api_spec JSON 无效'}), 400
    if not isinstance(spec, dict):
        return jsonify({'success': False, 'error': 'api_spec 须为对象'}), 400

    project_id = case.get('project_id')

    out = run_api_spec_pipeline(
        spec,
        _db,
        project_id,
        case_id,
        browser_cookie_jar=None,
        persist_extracts=False,
        collect_script_logs=True,
    )

    rt = out.get('response_text') or ''
    if len(rt) > 48000:
        rt = rt[:48000] + '\n…(已截断)'
    # 未拿到 HTTP 响应（连接失败、URL 无效、前置链在发出主请求前失败等）时 success=False，便于前端明确提示
    hard_fail = out.get('status_code') is None and bool(out.get('error'))
    payload = {
        'success': not hard_fail,
        'status_code': out.get('status_code'),
        'response_text': rt,
        'response_json': out.get('response_json'),
        'ok_assert': out.get('ok_assert'),
        'assert_message': out.get('assert_message'),
        'error': out.get('error'),
    }
    if out.get('script_logs'):
        payload['script_logs'] = out.get('script_logs')
    return jsonify(payload)


def _effective_step_iframe_selector(automation, db, step, project_id, case_id, row_resolve_fn=None):
    """
    本步操作使用的 iframe 定位串（与 frame_locator 一致）。
    「进入 iframe / 跳出 iframe」为隐式上下文：执行 enter_iframe 步骤后，只要未 exit_iframe，
    且当前步骤未单独勾选「在 iframe 内」+ 选择器列，则默认沿用 automation.current_iframe。
    若步骤单独配置了 iframe 列，则该步优先使用步骤配置（支持 {{变量}}）。
    row_resolve_fn: 数据驱动等对解析结果再套一层 {{row.field}}。
    """
    if bool(step.get('enter_iframe')):
        raw = (step.get('iframe_selector') or '').strip()
        if raw:
            resolved = db.resolve_variables(raw, project_id=project_id, case_id=case_id)
            if row_resolve_fn and resolved:
                resolved = row_resolve_fn(resolved)
            return (resolved or '').strip() or None
    ci = getattr(automation, 'current_iframe', None) or {}
    sel = ci.get('selector')
    return sel if sel else None


def _run_db_step_scroll(input_value: str, iframe_selector=None):
    """按平台存储的 up/down/left/right 像素执行滚动；无有效值时回退为向下 500px。"""
    v = parse_platform_scroll_input_value(input_value)
    dx = v["right"] - v["left"]
    dy = v["down"] - v["up"]
    if dx != 0 or dy != 0:
        sync_scroll_by_delta(dx, dy, iframe_selector=iframe_selector)
    else:
        sync_scroll_page("down", 500, iframe_selector=iframe_selector)


def _run_assert_automation_step(
    step: dict,
    selector_value: str,
    input_value: str,
    selector_type: str,
    iframe_sel,
):
    """执行断言步骤（与单用例运行一致）。文本类断言返回应写入 extracted 的片段，否则返回 None。"""
    assert_type = step.get('compare_type', 'text_equals')
    expected_value = input_value
    uat_logger.info(
        f"执行断言操作: 类型={assert_type}, 选择器={selector_value}, 预期={expected_value}"
    )
    extracted_fragment = None
    try:
        if assert_type in ['text_equals', 'text_contains', 'text_regex']:
            actual_text = sync_extract_element_text(
                selector_value,
                selector_type,
                iframe_selector=iframe_sel,
                locator_candidates=step.get('locator_candidates') or None,
            )
            if assert_type == 'text_equals':
                if actual_text != expected_value:
                    raise Exception(
                        f"断言失败: 实际文本 '{actual_text}' 不等于预期 '{expected_value}'"
                    )
            elif assert_type == 'text_contains':
                if expected_value not in actual_text:
                    raise Exception(
                        f"断言失败: 实际文本 '{actual_text}' 不包含预期 '{expected_value}'"
                    )
            elif assert_type == 'text_regex':
                import re

                if not re.search(expected_value, actual_text):
                    raise Exception(
                        f"断言失败: 实际文本 '{actual_text}' 不匹配正则 '{expected_value}'"
                    )
            extracted_fragment = actual_text
            uat_logger.info(f"断言成功: {assert_type}")
        elif assert_type in ['element_exists', 'element_visible']:
            if assert_type == 'element_exists':
                sync_wait_for_selector(selector_value, timeout=5000)
                uat_logger.info(f"断言成功: 元素存在 {selector_value}")
            else:
                sync_wait_for_element_visible(selector_value, timeout=5000)
                uat_logger.info(f"断言成功: 元素可见 {selector_value}")
        elif assert_type == 'element_count':
            actual_count = sync_get_element_count(selector_value, selector_type)
            expected_count = int(expected_value) if expected_value else 0
            operator = step.get('swipe_x', 'equals')
            success = False
            if operator == 'equals':
                success = actual_count == expected_count
            elif operator == 'gt':
                success = actual_count > expected_count
            elif operator == 'lt':
                success = actual_count < expected_count
            elif operator == 'gte':
                success = actual_count >= expected_count
            elif operator == 'lte':
                success = actual_count <= expected_count
            if not success:
                raise Exception(
                    f"断言失败: 实际数量 {actual_count} 不符合预期 {operator} {expected_count}"
                )
            uat_logger.info("断言成功: 元素数量符合预期")
        elif assert_type in ['url_equals', 'url_contains']:
            actual_url = sync_get_current_url()
            if assert_type == 'url_equals':
                if not _url_assert_matches_pa(actual_url, expected_value, 'url_equals'):
                    raise Exception(
                        f"断言失败: 实际URL '{actual_url}' 不等于预期 '{expected_value}'"
                    )
            else:
                if expected_value and not _url_assert_matches_pa(actual_url, expected_value, 'url_contains'):
                    raise Exception(
                        f"断言失败: 实际URL '{actual_url}' 不包含预期 '{expected_value}'"
                    )
            uat_logger.info(f"断言成功: {assert_type}")
        elif assert_type == 'element_attr':
            attr_name = step.get('page_name', '')

            async def get_attr():
                page = await automation.get_page()
                element = await page.query_selector(selector_value)
                if element:
                    return await element.get_attribute(attr_name)
                return None

            actual_attr = worker.execute(get_attr)
            if actual_attr != expected_value:
                raise Exception(
                    f"断言失败: 属性 {attr_name} 实际值 '{actual_attr}' 不等于预期 '{expected_value}'"
                )
            uat_logger.info(f"断言成功: 属性 {attr_name} = {actual_attr}")
        else:
            uat_logger.warning(f"未知的断言类型: {assert_type}")
    except Exception as assert_error:
        uat_logger.error(f"断言失败: {assert_error}")
        raise
    sync_wait_for_timeout(500)
    return extracted_fragment


def _run_extract_text_automation_step(
    action: str,
    step: dict,
    selector_value: str,
    input_value: str,
    description: str,
    selector_type: str,
    iframe_sel,
    locator_candidates=None,
):
    """extract_text / text_compare 与单用例运行一致。返回 (extracted_text, expected_text)。"""
    current_expected = input_value or description
    expected_text = current_expected
    verify_type = step.get('compare_type', step.get('verify_type', 'equals'))
    extracted_text = ""

    if selector_value:
        try:
            current_extracted = sync_extract_element_text(
                selector_value,
                selector_type,
                iframe_selector=iframe_sel,
                locator_candidates=locator_candidates,
            )
            uat_logger.info(f"提取到文本: {current_extracted[:100]}...")
            extracted_text = current_extracted
        except Exception as extract_error:
            uat_logger.error(f"提取文本失败: {extract_error}")
            raise Exception(f"提取文本失败: {extract_error}") from extract_error

        if expected_text:
            if extracted_text:
                uat_logger.info(
                    f"验证文本 - 提取: {extracted_text[:100]}..., 预期: {expected_text[:100]}..., 验证方式: {verify_type}"
                )
                if verify_type == 'equals':
                    if extracted_text != expected_text:
                        uat_logger.error("文本验证失败: 提取的文本与预期结果不相等")
                        raise Exception("文本验证失败: 提取的文本与预期结果不相等")
                elif verify_type == 'not_equals':
                    if extracted_text == expected_text:
                        uat_logger.error("文本验证失败: 提取的文本与预期结果相等")
                        raise Exception("文本验证失败: 提取的文本与预期结果相等")
                elif verify_type == 'contains':
                    if expected_text not in extracted_text:
                        uat_logger.error("文本验证失败: 提取的文本不包含预期内容")
                        raise Exception("文本验证失败: 提取的文本不包含预期内容")
                elif verify_type == 'partial':
                    if expected_text not in extracted_text:
                        uat_logger.error(
                            "文本验证失败: 提取的文本不包含预期的部分内容"
                        )
                        raise Exception(
                            "文本验证失败: 提取的文本不包含预期的部分内容"
                        )
                uat_logger.info("文本验证成功")
            else:
                if action == 'text_compare':
                    uat_logger.warning("未提取到文本，跳过文本验证")
                else:
                    uat_logger.info("提取文本操作完成（未提取到文本）")
    else:
        try:
            current_extracted = sync_get_page_text()
            uat_logger.info(f"提取到页面文本: {current_extracted[:100]}...")
            extracted_text = current_extracted
        except Exception as extract_error:
            uat_logger.error(f"提取页面文本失败: {extract_error}")
            raise Exception(f"提取页面文本失败: {extract_error}") from extract_error

        if expected_text:
            if extracted_text:
                uat_logger.info(
                    f"验证页面文本 - 提取: {extracted_text[:100]}..., 预期: {expected_text[:100]}..., 验证方式: {verify_type}"
                )
                if verify_type == 'equals':
                    if extracted_text != expected_text:
                        uat_logger.error("页面文本验证失败: 提取的文本与预期结果不相等")
                        raise Exception(
                            "页面文本验证失败: 提取的文本与预期结果不相等"
                        )
                elif verify_type == 'not_equals':
                    if extracted_text == expected_text:
                        uat_logger.error("页面文本验证失败: 提取的文本与预期结果相等")
                        raise Exception(
                            "页面文本验证失败: 提取的文本与预期结果相等"
                        )
                elif verify_type == 'contains':
                    if expected_text not in extracted_text:
                        uat_logger.error(
                            "页面文本验证失败: 提取的文本不包含预期内容"
                        )
                        raise Exception(
                            "页面文本验证失败: 提取的文本不包含预期内容"
                        )
                elif verify_type == 'partial':
                    if expected_text not in extracted_text:
                        uat_logger.error(
                            "页面文本验证失败: 提取的文本不包含预期的部分内容"
                        )
                        raise Exception(
                            "页面文本验证失败: 提取的文本不包含预期的部分内容"
                        )
                uat_logger.info("页面文本验证成功")
            else:
                if action == 'text_compare':
                    uat_logger.warning("未提取到页面文本，跳过文本验证")
                else:
                    uat_logger.error("提取页面文本操作失败：未提取到文本")
                    raise Exception("提取页面文本操作失败：未提取到文本")

    sync_wait_for_timeout(1000)
    return extracted_text, expected_text


# ==================== Playwright Codegen 录制：粘贴导入 ====================


def _playwright_codegen_command(nav_url: str) -> tuple:
    """
    返回 (argv, engine_label)。
    优先使用仓库内 playwright-xpath-fork 构建产物（XPath 优先录制定位），否则回退 pip playwright。
    环境变量 PLAYWRIGHT_XPATH_CODEGEN_CLI：可执行或「node path/to/cli.js ...」前缀，后自动拼接 codegen 参数。
    """
    target_args = ["codegen", nav_url, "--target", "python"]
    app_dir = os.path.dirname(os.path.abspath(__file__))
    fork_cli = os.path.join(
        app_dir,
        "playwright-xpath-fork",
        "packages",
        "playwright-core",
        "cli.js",
    )
    fork_bundle = os.path.join(
        app_dir,
        "playwright-xpath-fork",
        "packages",
        "playwright-core",
        "lib",
        "coreBundle.js",
    )
    # Codegen/Inspector 的静态界面，仅完整 `npm run build` 后才有；缺它会出现 ENOENT index.html
    fork_recorder_index = os.path.join(
        app_dir,
        "playwright-xpath-fork",
        "packages",
        "playwright-core",
        "lib",
        "vite",
        "recorder",
        "index.html",
    )

    env_override = (os.environ.get("PLAYWRIGHT_XPATH_CODEGEN_CLI") or "").strip()
    if env_override:
        try:
            base = shlex.split(env_override, posix=(os.name != "nt"))
        except ValueError:
            base = [env_override]
        return base + target_args, "custom"

    node = shutil.which("node")
    if (
        node
        and os.path.isfile(fork_cli)
        and os.path.isfile(fork_bundle)
        and os.path.isfile(fork_recorder_index)
    ):
        return [node, fork_cli] + target_args, "xpath_fork"

    return [sys.executable, "-m", "playwright"] + target_args, "pypi"


@app.route("/api/cases/<int:case_id>/start-playwright-codegen", methods=["POST"])
@login_required
@api_error_handler
@log_api_request
def api_case_start_playwright_codegen(case_id):
    """在本机新开控制台启动官方 playwright codegen（不写入服务端文件；录制完成后请复制代码使用「从 Codegen 导入」）。"""
    data = request.get_json(silent=True) or {}
    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({"success": False, "error": "测试用例不存在"}), 404

    _db = Database()
    if case.get("project_id") and not _db.check_project_access(
        current_user.id, case["project_id"], "editor"
    ):
        return jsonify({"success": False, "error": "无权限修改此用例"}), 403

    override_url = (data.get("url") or "").strip()
    steps_preview = db.get_case_steps(case_id)
    nav_url, nav_src = _resolve_case_navigation_url(
        case=case,
        case_id=case_id,
        steps=steps_preview,
        fallback_url=override_url or None,
    )
    if not nav_url:
        return jsonify(
            {
                "success": False,
                "error": "无法确定起始地址：请在本用例填写测试 URL，或在请求体中传入 url 字段",
            }
        ), 400

    app_dir = os.path.dirname(os.path.abspath(__file__))
    cmd, engine = _playwright_codegen_command(nav_url)
    try:
        if sys.platform == "win32":
            cf = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            if not cf:
                cf = 0x00000010
            subprocess.Popen(cmd, creationflags=cf, cwd=app_dir if engine == "xpath_fork" else None)
        else:
            subprocess.Popen(cmd, start_new_session=True, cwd=app_dir if engine == "xpath_fork" else None)
    except Exception as e:
        uat_logger.log_exception("api_case_start_playwright_codegen", e)
        return jsonify(
            {"success": False, "error": f"无法启动 playwright codegen：{e}"}
        ), 500
    uat_logger.info(
        f"Playwright codegen 已启动 engine={engine} case_id={case_id} user={current_user.id} url={nav_url}"
    )
    if engine == "xpath_fork":
        hint = (
            "已使用本仓库 **XPath 优先** 的 Playwright 分叉启动 Codegen（生成代码会尽量用 page.locator('xpath=…') 等）。"
            " 完成后请复制代码，用「从 Codegen 导入」写入用例。"
        )
    elif engine == "pypi":
        hint = (
            "当前使用 pip 安装的 **官方** Playwright，Codegen 仍优先 getByRole 等。"
            " 若要 XPath 优先录制：在本机进入仓库目录 `playwright-xpath-fork` 执行 `npm ci` 与 `npm run build` 后，"
            "再点「启动 Playwright Codegen」；详见「录制教程」。"
            " 完成后复制代码，用「从 Codegen 导入」。"
        )
    else:
        hint = "Codegen 已启动。完成后请复制代码，用「从 Codegen 导入」写入用例。"

    return jsonify(
        {
            "success": True,
            "navigated_to": nav_url,
            "nav_source": nav_src,
            "codegen_engine": engine,
            "hint": hint,
        }
    )


@app.route('/recording-tutorial')
@login_required
def recording_tutorial_page():
    """Playwright Codegen 与 Selenium IDE（.side）粘贴导入说明。"""
    return render_template('recording_tutorial.html')


@app.route('/api/cases/<int:case_id>/import-playwright-codegen', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_import_playwright_codegen(case_id):
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip()
    replace_existing = bool(data.get('replace_existing'))
    if not code:
        return jsonify({'success': False, 'error': '请粘贴 Playwright Codegen 生成的代码'}), 400

    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'success': False, 'error': '测试用例不存在'}), 404
    if _app_case_type(case) == 'api':
        return jsonify({'success': False, 'error': '接口用例不支持 Codegen 导入，请在接口测试页维护 HTTP 步骤。'}), 400

    _db = Database()
    steps = enrich_steps_with_xpath_priority(steps)
    if not steps:
        return jsonify({
            'success': False,
            'error': '未能解析出有效步骤，请确认粘贴的是 Codegen 窗口中的 Python 或 JS 片段（含 page.goto / click / fill 等）',
            'warnings': warnings,
        }), 400

    if replace_existing:
        db.delete_case_steps(case_id)

    if not db.batch_insert_steps(case_id, steps):
        return jsonify({'success': False, 'error': '写入步骤失败'}), 500

    return jsonify({'success': True, 'imported': len(steps), 'warnings': warnings})


@app.route('/api/cases/<int:case_id>/import-selenium-ide', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_import_selenium_ide(case_id):
    """粘贴 Selenium IDE 保存的 .side 项目 JSON（或其它可解析结构）。"""
    data = request.get_json(silent=True) or {}
    raw = data.get('json') or data.get('side') or data.get('payload')
    replace_existing = bool(data.get('replace_existing'))

    if raw is None:
        return jsonify({'success': False, 'error': '请在请求体中提供 json 字段（.side 文件全文）'}), 400
    if isinstance(raw, dict):
        payload = raw
    elif isinstance(raw, str):
        payload = raw.strip()
        if not payload:
            return jsonify({'success': False, 'error': 'JSON 内容为空'}), 400
    else:
        return jsonify({'success': False, 'error': 'json 字段类型无效'}), 400

    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'success': False, 'error': '测试用例不存在'}), 404
    if _app_case_type(case) == 'api':
        return jsonify({'success': False, 'error': '接口用例不支持 Selenium IDE 导入。'}), 400

    _db = Database()
    if not steps:
        return jsonify({
            'success': False,
            'error': '未能解析出有效步骤。请粘贴完整的 .side 项目（含 tests[].commands），'
                     '教程见「录制教程」页中的 Selenium IDE 一节。',
            'warnings': warnings,
        }), 400

    if replace_existing:
        db.delete_case_steps(case_id)

    if not db.batch_insert_steps(case_id, steps):
        return jsonify({'success': False, 'error': '写入步骤失败'}), 500

    return jsonify({'success': True, 'imported': len(steps), 'warnings': warnings})


# API: 运行测试用例
@app.route('/api/cases/<int:case_id>/run', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_run_case(case_id):
    # 记录开始时间
    start_time = time.time()

    # 初始化数据库连接（修复变量作用域问题）
    db = Database()

    # ===== 执行次数限制检查 =====
    user_id = current_user.id

    # 获取当前 License 信息
    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    is_free_user = license_info.license_type == LicenseType.FREE.value

    # 获取今日已执行次数
    today_stats = db.get_user_usage_stats(user_id)
    current_count = today_stats.get('execution_count', 0) if today_stats else 0

    # 获取每日执行限制（-1表示无限制）
    daily_limit = limits.get('max_executions_per_day', -1)

    # 免费版每日限制10次
    if is_free_user:
        DAILY_LIMIT = 10

        if current_count >= DAILY_LIMIT:
            return jsonify({
                'success': False,
                'error': f'今日执行次数已达上限（{DAILY_LIMIT}次）。请升级至团队版解除限制。',
                'limit_reached': True,
                'current_count': current_count,
                'daily_limit': DAILY_LIMIT,
                'upgrade_url': '/upgrade'
            }), 403
    elif daily_limit > 0 and current_count >= daily_limit:
        # 非免费版但有每日限制的情况
        return jsonify({
            'success': False,
            'error': f'今日执行次数已达上限（{daily_limit}次）。请联系管理员。',
            'limit_reached': True,
            'current_count': current_count,
            'daily_limit': daily_limit
        }), 403

    # 记录执行次数（所有版本都记录，用于统计和显示）
    new_count = db.increment_execution_count(user_id)
    uat_logger.info(f"📊 [LICENSE] 用户 {user_id} 今日执行次数: {new_count}")

    # 获取测试用例信息
    case, steps = load_case_and_steps(case_id, db)
    if not case:
        return jsonify({'error': '测试用例不存在'}), 404

    if should_delegate_execution_to_clients() and not getattr(
        __import__('flask').g, 'force_local_run', False
    ):
        job_resp = patch_run_case_for_server(db, case_id, user_id)
        return jsonify(job_resp), 202

    if _app_case_type(case) == 'api':
        return jsonify({
            'success': False,
            'error': '这是接口用例，请在「接口测试」模块中执行，或调用 POST /api/api-cases/<用例ID>/run。',
        }), 400

    # steps 已由 load_case_and_steps 加载
    if any((s.get('action') or '').strip() == 'api_request' for s in steps):
        return jsonify({
            'success': False,
            'error': '该 Web 用例仍包含接口测试步骤，请先使用「一键迁移」迁移到接口用例后再运行 AI 自动化测试。',
        }), 400

    if not steps:
        # 用例无步骤，保存一条失败历史并友好提示
        run_id = db.create_run_history(case_id, 'error', 0, '该用例没有步骤，无法执行', '', '')
        uat_logger.warning(f"测试用例 #{case_id} 没有步骤，无法执行")
        return jsonify({'success': False, 'status': 'error', 'duration': 0,
                        'error': '该用例尚未添加任何步骤，请先编辑用例添加步骤后再执行。'})

    try:
        from execution_factory import get_executor_factory

        env_msg = get_executor_factory().validate_case_environment(steps)
        if env_msg and ("不支持" in env_msg or "需 Windows" in env_msg or "缺少依赖" in env_msg):
            from desktop_runtime import desktop_runtime_unavailable_reason

            detail = desktop_runtime_unavailable_reason()
            return jsonify({
                'success': False,
                'error': env_msg,
                'error_detail': detail or env_msg,
                'python_executable': sys.executable,
                'platform': sys.platform,
            }), 400
        if env_msg:
            uat_logger.warning("混排运行环境提示: %s", env_msg)
    except ImportError:
        pass

    machine_lock_acquired = False
    try:
        from execution_lock import ExecutionLockError, acquire as acquire_machine_lock

        machine_lock_acquired = acquire_machine_lock(
            owner=f"case_run:{case_id}:user:{user_id}", timeout_sec=120
        )
        if not machine_lock_acquired:
            return jsonify({
                'success': False,
                'error': '本机已有自动化任务在执行，请稍后再试。',
                'lock': 'busy',
            }), 409
    except ExecutionLockError as lock_exc:
        return jsonify({'success': False, 'error': str(lock_exc), 'lock': 'busy'}), 409
    except ImportError:
        pass

    uat_logger.info(f"开始运行测试用例 #{case_id}: {case['name']}")
    uat_logger.info(f"测试用例共有 {len(steps)} 个步骤")
    with _case_run_lock:
        _case_run_jobs[user_id] = {
            'active': True,
            'cancel_requested': False,
            'case_id': case_id,
            'case_name': case.get('name', ''),
            'total_steps': len(steps),
            'completed_steps': 0,
            'current_step_order': 0,
            'current_action': '',
            'message': '准备执行...',
            'started_at': time.time(),
        }
    
    # 提取的文本
    extracted_text = ""
    # 预期结果
    expected_text = ""
    # 截图列表（失败截图路径）
    screenshots = []
    # 步骤结果列表（用于步骤级记录）
    step_results_list = []
    # 浏览器状态标记
    browser_closed_manually = False
    browser_started = False
    
    try:
        # 若用户仍处于元素/桌面拾取会话，执行前关闭拾取 UI，避免全屏遮罩与钩子干扰
        try:
            from element_picker import sync_stop_element_picker

            picker_stopped = sync_stop_element_picker()
            if (picker_stopped.get("desktop") or {}).get("was_active"):
                uat_logger.info("检测到元素捕获仍在运行，执行前已自动关闭")
        except Exception:
            pass
        try:
            from web_dom_picker import get_web_dom_picker_status, sync_stop_web_dom_picker

            if get_web_dom_picker_status().get('active'):
                uat_logger.info("检测到网页 DOM 捕获会话，执行前自动关闭")
                sync_stop_web_dom_picker()
        except Exception:
            pass
        if bool(getattr(automation, '_selection_mode_active', False)):
            uat_logger.info("检测到旧版 Web 拾取器会话仍在，执行前自动关闭")
            try:
                sync_disable_element_selection()
            except Exception:
                pass
            try:
                automation._selection_mode_active = False
            except Exception:
                pass

        # 🔥 增强浏览器断连检测和自动恢复逻辑
        # 启动浏览器前先检查状态：如果浏览器已断连，先强制重置所有状态
        browser_disconnected = False
        try:
            if automation.browser is not None:
                try:
                    browser_disconnected = not automation.browser.is_connected()
                except Exception:
                    # is_connected() 抛出异常说明浏览器对象已失效
                    browser_disconnected = True
            else:
                # browser 为 None 说明浏览器未启动或已被清理，这是正常状态
                browser_disconnected = False
        except Exception as e:
            uat_logger.warning(f"⚠️ [浏览器检测] 检测浏览器状态时出错: {e}，假定已断连")
            browser_disconnected = True
        
        if browser_disconnected:
            uat_logger.warning("⚠️ [浏览器恢复] 检测到浏览器已断连，执行前强制重置所有状态")
            force_reset_execution_state()
            uat_logger.info("✅ [浏览器恢复] 状态已重置")

        # 纯桌面用例不启动 Playwright，避免先弹出 about:blank 空浏览器窗口
        from step_executor import case_steps_include_web, is_desktop_step

        def _ensure_browser_for_web_step() -> None:
            nonlocal browser_started
            if browser_started:
                return
            sync_start_browser()
            browser_started = True
            initial_nav_url, nav_source = _resolve_case_navigation_url(
                case=case, case_id=case_id, steps=steps
            )
            if initial_nav_url:
                uat_logger.log_automation_step(
                    "navigate", initial_nav_url, f"首次 Web 步骤前导航({nav_source})"
                )
                sync_navigate_to(initial_nav_url)

        if not case_steps_include_web(steps):
            uat_logger.info("纯桌面用例，跳过 Playwright 浏览器启动")

        # 执行测试步骤
        try:
            total_step_count = len(steps)
            for step_index, step in enumerate(steps, start=1):
                if _case_run_cancelled(user_id):
                    raise Exception("用户已停止执行")

                action = step.get('action', '')
                selector_type = step.get('selector_type', 'css')
                # 变量替换：支持 {{变量名}} 语法
                selector_value = db.resolve_variables(step.get('selector_value', ''), project_id=case.get('project_id'), case_id=case_id)
                input_value = db.resolve_variables(step.get('input_value', ''), project_id=case.get('project_id'), case_id=case_id)
                description = step.get('description', '')
                # 添加iframe相关字段
                enter_iframe = step.get('enter_iframe', False)
                iframe_selector = step.get('iframe_selector', '')
                iframe_for_step = _effective_step_iframe_selector(
                    automation, db, step, case.get('project_id'), case_id
                )
                locator_candidates = step.get('locator_candidates') or None

                step_start_time = time.time()
                # 🔥 修复：初始化为 error，只有执行成功才改为 success
                step_status = 'error'
                step_error = ''
                step_screenshot = ''
                
                uat_logger.log_automation_step(action, selector_value or input_value, description)
                _case_job_update(
                    user_id,
                    current_step_order=step_index,
                    current_action=action,
                    message=f"正在执行步骤 {step_index}/{total_step_count}: {action}",
                )
                                                        
                # 详细的调试日志，跟踪 action 值和执行的方法
                uat_logger.debug(
                    f"执行步骤：ID={step.get('id')}, Action={action}, SelectorType={selector_type}, "
                    f"SelectorValue={selector_value}, InputValue={input_value}, EnterIframe={enter_iframe}, "
                    f"IframeSelector={iframe_selector}, IframeEffective={iframe_for_step}"
                )

                try:
                    from execution_factory import get_executor_factory
                    from step_executor import enrich_execution_step, is_desktop_step

                    factory = get_executor_factory()
                    exec_step = enrich_execution_step(step)
                    if is_desktop_step(exec_step):
                        try:
                            desk_step = dict(exec_step)
                            desk_step["_case_name"] = (case.get("name") or "").strip()
                            desk_step["selector_value"] = selector_value
                            desk_step["input_value"] = input_value
                            desk_result = factory.execute_desktop_step(
                                desk_step,
                                selector_value=selector_value,
                                input_value=input_value,
                            )
                        except Exception as desk_exc:
                            if _case_run_cancelled(user_id):
                                raise Exception("用户已停止执行")
                            from desktop_visual_engine import VisualMatchFailed

                            step_duration = round(time.time() - step_start_time, 3)
                            step_screenshot = getattr(desk_exc, "failure_screenshot", "") or ""
                            step_results_list.append({
                                'step_id': step.get('id'), 'step_order': step.get('step_order', 0),
                                'action': action, 'selector_value': selector_value,
                                'input_value': input_value, 'description': description,
                                'status': 'error', 'error': str(desk_exc),
                                'screenshot': step_screenshot, 'duration': step_duration,
                                'automation_layer': 'desktop',
                            })
                            if isinstance(desk_exc, VisualMatchFailed):
                                desk_exc.step_id = step.get('id')  # type: ignore[attr-defined]
                            raise
                        from step_executor import validate_desktop_step_result

                        validate_desktop_step_result(desk_result, action)
                        step_status = 'success'
                        step_error = ''
                        step_screenshot = (desk_result or {}).get('screenshot') or ''
                        if (desk_result or {}).get("resolved_via"):
                            uat_logger.info(
                                "桌面步骤 #%s 定位方式: %s",
                                step.get("id"),
                                desk_result.get("resolved_via"),
                            )
                        step_duration = round(time.time() - step_start_time, 3)
                        step_results_list.append({
                            'step_id': step.get('id'), 'step_order': step.get('step_order', 0),
                            'action': action, 'selector_value': selector_value,
                            'input_value': input_value, 'description': description,
                            'status': step_status, 'error': step_error,
                            'screenshot': step_screenshot, 'duration': step_duration,
                            'automation_layer': 'desktop',
                        })
                        _case_job_update(
                            user_id,
                            completed_steps=len(step_results_list),
                            message=f"已完成 {len(step_results_list)}/{len(steps)} 步",
                        )
                        continue
                except ImportError:
                    pass

                from desktop_automation import normalize_automation_layer as _norm_layer

                if _norm_layer(step) == "desktop":
                    raise Exception(
                        f"桌面步骤未进入桌面执行器（action={action}，"
                        f"automation_layer={step.get('automation_layer')!r}）。"
                        f"请确认步骤自动化层为「桌面」后重试。"
                    )

                _ensure_browser_for_web_step()
                
                if action == 'navigate':
                    # 获取URL并进行有效性检查
                    raw_url = step.get('url') or step.get('input_value') or ''
                    fixed_url, url_err = _validate_and_fix_url(raw_url)
                    if url_err:
                        uat_logger.error(f"导航步骤URL无效: {url_err}")
                        raise Exception(url_err)
                    elif fixed_url:
                        uat_logger.log_automation_step("navigate", fixed_url, "导航到URL")
                        sync_navigate_to(fixed_url)
                    else:
                        uat_logger.warning("导航步骤URL为空，跳过")
                elif action == 'click':
                            if not selector_value:
                                raise Exception("点击步骤缺少选择器")
                            try:
                                _repeat = _norm_click_repeat_count(step.get('click_repeat_count'))
                                for _r in range(_repeat):
                                    with _case_run_lock:
                                        if bool(_case_run_jobs.get(user_id, {}).get('cancel_requested')):
                                            raise Exception("用户已停止执行")
                                    sync_click_element(
                                        selector_value,
                                        selector_type,
                                        iframe_selector=iframe_for_step,
                                        locator_candidates=locator_candidates,
                                    )
                            except Exception as click_error:
                                uat_logger.error(f"执行点击操作时出错: {click_error}")
                                raise
                elif action == 'input':
                    if selector_value:
                        try:
                            # 严格模式：未配置输入值时直接失败，避免误清空
                            if input_value is None or str(input_value) == '':
                                raise Exception(f"输入步骤缺少有效输入值: step_id={step.get('id', 'unknown')}")
                            safe_input_value = input_value
                            uat_logger.info(f"🔍 准备执行输入操作: 步骤ID={step.get('id', 'unknown')}, 选择器类型={selector_type}, 选择器值={selector_value}, 输入值={input_value}")
                            
                            # 🔥 添加详细的诊断信息
                            if len(selector_value) > 200:
                                uat_logger.warning(f"⚠️ 检测到超长CSS选择器（{len(selector_value)}字符），建议优化选择器")
                                uat_logger.warning(f"   当前选择器前100字符: {selector_value[:100]}...")
                            
                            # 🔥 检查选择器类型
                            if selector_type == "css" and "nth-child" in selector_value:
                                uat_logger.warning(f"⚠️ 检测到使用nth-child定位，可能不够稳定，建议改用ID或类名")
                            
                            sync_fill_input(
                                    selector_value,
                                    safe_input_value,
                                    selector_type,
                                    iframe_selector=iframe_for_step,
                                    locator_candidates=locator_candidates,
                                )
                            uat_logger.info(f"✅ 输入操作执行完成: 步骤ID={step.get('id', 'unknown')}")
                            
                        except Exception as input_error:
                            uat_logger.error(f"🔥 输入操作执行失败详情:")
                            uat_logger.error(f"   步骤ID: {step.get('id', 'unknown')}")
                            uat_logger.error(f"   选择器类型: {selector_type}")
                            uat_logger.error(f"   选择器值: {selector_value}")
                            uat_logger.error(f"   输入值: {input_value}")
                            uat_logger.error(f"   错误信息: {input_error}")
                            uat_logger.error(f"   iframe选择器: {iframe_for_step}")
                            
                            # 🔥 提供改进建议
                            if len(selector_value) > 200:
                                uat_logger.error(f"   💡 建议: 选择器过长，尝试使用更简单的选择器，如ID、类名或文本定位")
                            if "nth-child" in selector_value:
                                uat_logger.error(f"   💡 建议: 避免使用nth-child，改用更稳定的定位方式")
                            if "h-[" in selector_value or "w-[" in selector_value or "calc(" in selector_value:
                                uat_logger.error(f"   💡 建议: 避免使用Tailwind动态类名，这些类名容易变化")
                            
                            # 直接抛出错误，视为测试用例执行失败
                            raise
                    else:
                        uat_logger.error("输入操作缺少选择器，步骤不能执行")
                        raise Exception("输入操作缺少选择器")
                elif action == 'batch_input':
                    pairs = parse_batch_input_lines(input_value or '')
                    if not pairs:
                        raise Exception("批量输入步骤缺少有效行（每行：选择器 + Tab 或逗号 + 文本，参见步骤说明）")
                    for bsel, bval in pairs:
                        with _case_run_lock:
                            if bool(_case_run_jobs.get(user_id, {}).get('cancel_requested')):
                                raise Exception("用户已停止执行")
                        uat_logger.info(
                            f"批量输入: 选择器={bsel!r} 值长={len(bval or '')}"
                        )
                        sync_fill_input(
                            bsel,
                            bval,
                            selector_type,
                            iframe_selector=iframe_for_step,
                            locator_candidates=None,
                        )
                elif action == 'hover':
                    if selector_value:
                        try:
                            sync_hover_element(selector_value, selector_type, iframe_selector=iframe_for_step)
                            # 悬停后等待页面响应
                            sync_wait_for_timeout(1000)
                        except Exception as hover_error:
                            uat_logger.error(f"执行悬停操作时出错: {hover_error}")
                            # 直接抛出错误，视为测试用例执行失败
                            raise
                    else:
                        uat_logger.error("悬停操作缺少选择器")
                        raise Exception("悬停操作缺少选择器")
                elif action == 'double_click':
                    if selector_value:
                        try:
                            sync_double_click_element(selector_value, selector_type, iframe_selector=iframe_for_step)
                            # 双击后等待页面响应
                            sync_wait_for_timeout(2000)
                        except Exception as double_click_error:
                            uat_logger.error(f"执行双击操作时出错: {double_click_error}")
                            # 直接抛出错误，视为测试用例执行失败
                            raise
                    else:
                        uat_logger.error("双击操作缺少选择器")
                        raise Exception("双击操作缺少选择器")
                elif action == 'right_click':
                    if selector_value:
                        try:
                            sync_right_click_element(selector_value, selector_type, iframe_selector=iframe_for_step)
                            # 右键点击后等待页面响应
                            sync_wait_for_timeout(1000)
                        except Exception as right_click_error:
                            uat_logger.error(f"执行右键点击操作时出错: {right_click_error}")
                            # 直接抛出错误，视为测试用例执行失败
                            raise
                    else:
                        uat_logger.error("右键点击操作缺少选择器")
                        raise Exception("右键点击操作缺少选择器")
                elif action == 'wait':
                    # 修复：wait操作应该等待时间，而不是等待选择器
                    if input_value:
                        try:
                            # 将输入值转换为毫秒（支持秒为单位）
                            wait_time = int(input_value) * 1000 if int(input_value) < 1000 else int(input_value)
                            sync_wait_for_timeout(wait_time)
                        except ValueError:
                            uat_logger.error(f"无效的等待时间值: {input_value}")
                            # 🔥 修复：无效的等待时间应该视为测试失败
                            raise Exception(f"无效的等待时间值: {input_value}")
                    else:
                        # 🔥 修复：如果没有输入值，应该视为失败（或者使用默认值但记录警告）
                        uat_logger.warning("等待操作缺少输入值，使用默认值1000毫秒")
                        sync_wait_for_timeout(1000)
                elif action == 'select':
                    # 修复：添加下拉框选择操作
                    if selector_value and input_value:
                        try:
                            sync_select_option(selector_value, input_value, selector_type, iframe_selector=iframe_for_step)
                            # 选择后等待页面响应
                            sync_wait_for_timeout(1000)
                        except Exception as select_error:
                            uat_logger.error(f"执行下拉框选择操作时出错: {select_error}")
                            # 直接抛出错误，视为测试用例执行失败
                            raise
                elif action == 'date':
                    # 新增：日期选择器操作
                    if selector_value and input_value:
                        try:
                            sync_select_date(selector_value, input_value)
                            # 选择日期后等待页面响应
                            sync_wait_for_timeout(1000)
                        except Exception as date_error:
                            uat_logger.error(f"执行日期选择操作时出错: {date_error}")
                            # 直接抛出错误，视为测试用例执行失败
                            raise
                elif action == 'scroll':
                    try:
                        _run_db_step_scroll(
                            input_value or "",
                            iframe_selector=iframe_for_step,
                        )
                        # 滚动后等待页面响应
                        sync_wait_for_timeout(1500)
                    except Exception as scroll_error:
                        uat_logger.error(f"执行滚动操作时出错: {scroll_error}")
                        # 直接抛出错误，视为测试用例执行失败
                        raise
                elif action == 'swipe':
                    if selector_value:
                        direction = 'up'
                        distance = 100
                        if input_value:
                            # 解析 input_value 中的方向和距离 (格式: direction:distance)
                            parts = input_value.split(':')
                            if len(parts) == 2:
                                direction = parts[0]
                                try:
                                    distance = int(parts[1])
                                except ValueError:
                                    uat_logger.warning(f"无效的滑动距离值: {parts[1]}，使用默认值 100")
                            else:
                                # 兼容旧格式 (只有方向)
                                direction = input_value
                        try:
                            sync_swipe_element(selector_value, direction, distance, selector_type, iframe_selector=iframe_for_step)
                            # 滑动后等待页面响应
                            sync_wait_for_timeout(1500)
                        except Exception as swipe_error:
                            uat_logger.error(f"执行滑动操作时出错: {swipe_error}")
                            # 直接抛出错误，视为测试用例执行失败
                            raise
                elif action == 'verify':
                    # 验证操作处理
                    verify_type = input_value if input_value else 'auto'
                    try:
                        sync_verify_element(
                            selector=selector_value,
                            verify_type=verify_type,
                            selector_type=selector_type,
                            iframe_selector=iframe_for_step,
                            locator_candidates=locator_candidates,
                        )
                        # 验证后等待页面响应
                        sync_wait_for_timeout(1500)
                    except Exception as verify_error:
                        uat_logger.error(f"执行验证操作时出错: {verify_error}")
                        # 直接抛出错误，视为测试用例执行失败
                        raise
                elif action == 'extract_text' or action == 'text_compare':
                    extracted_text, expected_text = _run_extract_text_automation_step(
                        action,
                        step,
                        selector_value,
                        input_value,
                        description,
                        selector_type,
                        iframe_for_step,
                        locator_candidates=locator_candidates,
                    )
                elif action == 'extract_json':
                    if selector_value:
                        # 提取元素JSON数据
                        try:
                            json_data = sync_extract_element_json(selector_value, selector_type)
                            uat_logger.info(f"提取到JSON数据: {json_data}")
                            # 保存到extracted_text变量，以便在结果中显示
                            extracted_text = str(json_data)
                        except Exception as extract_error:
                            uat_logger.error(f"提取JSON数据失败: {extract_error}")
                            # 🔥 修复：提取JSON数据失败应该视为测试失败
                            raise Exception(f"提取JSON数据失败: {extract_error}")
                    else:
                        uat_logger.error("提取JSON数据时缺少选择器")
                        # 🔥 修复：缺少选择器应该视为测试失败
                        raise Exception("提取JSON数据时缺少选择器")
                    
                    # 提取后等待页面响应
                    sync_wait_for_timeout(1000)
                elif action == 'assert':
                    extra_ex = _run_assert_automation_step(
                        step, selector_value, input_value, selector_type, iframe_for_step
                    )
                    if extra_ex is not None:
                        extracted_text = extra_ex
                elif action == 'enter_iframe':
                    if selector_value:
                        # 进入 iframe：状态在 automation.current_iframe，下游步骤由 _effective_step_iframe_selector 继承
                        try:
                            sync_enter_iframe(selector_value, selector_type)
                            uat_logger.info(f"✅ 成功进入iframe: {selector_value}")
                        except Exception as enter_error:
                            uat_logger.error(f"执行进入iframe操作时出错: {enter_error}")
                            # 直接抛出错误，视为测试用例执行失败
                            raise
                    else:
                        uat_logger.warning("进入iframe操作缺少选择器")
                elif action == 'exit_iframe':
                    # 跳出 iframe：清除 automation.current_iframe，后续步骤回到主文档（除非步骤自身勾选 iframe）
                    try:
                        sync_exit_iframe()
                        uat_logger.info("✅ 成功跳出iframe，返回主文档")
                    except Exception as exit_error:
                        uat_logger.error(f"执行跳出iframe操作时出错: {exit_error}")
                        # 直接抛出错误，视为测试用例执行失败
                        raise
                else:
                    from desktop_automation import normalize_automation_layer as _norm_al_web

                    _layer_web = _norm_al_web(step)
                    _st_web = (selector_type or '').strip().lower()
                    if _layer_web == 'desktop' or _st_web == 'visual':
                        raise RuntimeError(
                            f"步骤 #{step.get('id')} 为桌面/visual 步骤，但未通过桌面执行器完成"
                            f"（action={action!r}，automation_layer={step.get('automation_layer')!r}）。"
                            "请在步骤编辑器中确认自动化层为「桌面」并保存后重试。"
                        )

                # 🔥 修复：步骤执行到这里说明成功，更新状态为 success
                step_status = 'success'
                step_error = ''
                
                # ⭐⭐ 记录成功步骤结果
                step_duration = round(time.time() - step_start_time, 3)
                step_results_list.append({
                    'step_id': step.get('id'), 'step_order': step.get('step_order', 0),
                    'action': action, 'selector_value': selector_value,
                    'input_value': input_value, 'description': description,
                    'status': step_status, 'error': step_error,
                    'screenshot': step_screenshot, 'duration': step_duration
                })
                _case_job_update(
                    user_id,
                    completed_steps=len(step_results_list),
                    message=f"已完成 {len(step_results_list)}/{len(steps)} 步",
                )
            
            if not steps:
                raise RuntimeError("用例没有可执行的步骤")

            # 计算执行时间
            duration = round(time.time() - start_time, 2)
            
            uat_logger.info(f"测试用例 #{case_id} 运行成功，耗时: {duration}秒")
            
            # 保存运行历史记录，并写入步骤结果
            try:
                run_id = db.create_run_history(case_id, 'success', duration, "", extracted_text, expected_text)
                for sr in step_results_list:
                    db.create_step_result(run_id, sr['step_id'], sr['step_order'], sr['action'],
                        sr['selector_value'], sr['input_value'], sr['description'],
                        sr['status'], sr['error'], sr['screenshot'], sr['duration'])
                try:
                    from ai_memory_store import ingest_successful_run, memory_ingest_run_success_enabled

                    if memory_ingest_run_success_enabled():
                        tid = db.get_user_tenant_id(user_id)
                        ingest_successful_run(
                            user_id,
                            tid,
                            case_id,
                            case.get('name') or '',
                            case.get('url') or '',
                            duration,
                            run_id,
                        )
                except Exception as _mem_run_e:
                    uat_logger.debug("memory ingest successful run: %s", _mem_run_e)
                sync_run_to_team_server(
                    case_id, 'success', duration, step_results=step_results_list
                )
            except Exception as history_error:
                uat_logger.warning(f"保存运行历史记录失败: {history_error}")
            
            if browser_started:
                try:
                    sync_close_browser()
                except Exception as close_error:
                    uat_logger.warning(f"关闭浏览器时出错: {close_error}")
            
            return jsonify({
                'success': True,
                'status': 'success',
                'duration': duration,
                'message': '测试用例运行成功',
                'step_count': len(step_results_list)
            })
            
        except Exception as e:
            # 执行失败时的处理
            duration = round(time.time() - start_time, 2)

            # 用户已点「停止执行」：不因关应用/断连等中间错误弹失败（桌面步骤常见）
            if _case_run_cancelled(user_id) or '用户已停止执行' in str(e):
                stop_msg = '用户已停止执行'
                uat_logger.info(f"测试用例 #{case_id} 已由用户停止（忽略步骤异常: {str(e)[:300]}）")
                try:
                    run_id = db.create_run_history(
                        case_id, 'stopped', duration, stop_msg, extracted_text, expected_text
                    )
                    for sr in step_results_list:
                        db.create_step_result(
                            run_id,
                            sr['step_id'],
                            sr['step_order'],
                            sr['action'],
                            sr['selector_value'],
                            sr['input_value'],
                            sr['description'],
                            sr['status'],
                            sr['error'],
                            sr['screenshot'],
                            sr['duration'],
                        )
                except Exception as history_error:
                    uat_logger.warning(f"保存停止运行历史失败: {history_error}")
                try:
                    force_reset_execution_state()
                except Exception:
                    pass
                return jsonify({
                    'success': False,
                    'status': 'stopped',
                    'duration': duration,
                    'error': stop_msg,
                    'stopped': True,
                })

            error_msg = str(e)
            
            # 将会话断开类错误单独归类（无头模式下也常见，并非一定是「人为关窗口」）
            # 注意：不要用单词 page/target/context 等做子串匹配，否则 “timeout waiting for …” 等普通错误会被误判。
            _el = error_msg.lower()
            _disconnect_patterns = (
                'has been closed',
                'browser has been closed',
                'browser was closed',
                'browser closed',
                'target page, context or browser has been closed',
                'page was closed',
                'context was closed',
                'browser disconnected',
                'connection closed',
                'connection lost',
                'websocket',
                'econnreset',
                'broken pipe',
                'execution context was destroyed',
                'session deleted',
                'browser crashed',
                ' crashed',
                'disconnected',
            )
            if any(p in _el for p in _disconnect_patterns):
                browser_closed_manually = True
                error_msg = (
                    "浏览器或自动化连接已中断（无头模式下常见于进程崩溃、资源不足、超时或被系统结束；"
                    "不一定是手动关闭窗口）"
                )
                uat_logger.warning(
                    f"测试用例 #{case_id} 执行中断（会话/连接断开）: {error_msg} | 原始: {str(e)[:800]}"
                )
                # 🔥 浏览器被关闭时强制重置所有状态（包括执行锁和浏览器引用）
                try:
                    force_reset_execution_state()
                except Exception:
                    pass
            else:
                uat_logger.error(f"测试用例 #{case_id} 运行失败: {error_msg}")

            # 失败时自动截图（桌面 visual 失败已生成元素 ROI 对比图，不再截浏览器整页）
            failure_screenshot = ''
            visual_match_failed = None
            _cause = e
            while _cause is not None:
                try:
                    from desktop_visual_engine import VisualMatchFailed
                except ImportError:
                    break
                if isinstance(_cause, VisualMatchFailed):
                    visual_match_failed = _cause
                    break
                _cause = getattr(_cause, '__cause__', None)
            if visual_match_failed and getattr(visual_match_failed, 'failure_screenshot', None):
                failure_screenshot = visual_match_failed.failure_screenshot
            elif not browser_closed_manually:
                try:
                    screenshot_dir = os.path.join(os.getcwd(), 'screenshots')
                    os.makedirs(screenshot_dir, exist_ok=True)
                    screenshot_path = os.path.join(screenshot_dir, f'fail_{case_id}_{int(time.time())}.png')
                    sync_take_screenshot(screenshot_path)
                    failure_screenshot = screenshot_path
                    screenshots.append(screenshot_path)
                    uat_logger.info(f"失败截图已保存: {screenshot_path}")
                except Exception as ss_error:
                    uat_logger.warning(f"截图失败: {ss_error}")

            # ⭐⭐ 记录失败步骤：将当前步骤添加到列表（如果该步骤未被记录）
            # 失败的步骤在 raise 后跳过了步骤记录代码，需要在此处补充记录
            # 通过检查 step_results_list 的最后一条记录是否是该失败步骤
            already_recorded = (
                step_results_list and
                step_results_list[-1].get('step_id') == step.get('id')
            ) if 'step' in dir() else False
            if not already_recorded and 'step' in dir() and step:
                failed_step_duration = round(time.time() - step_start_time, 3) if 'step_start_time' in dir() else 0
                step_results_list.append({
                    'step_id': step.get('id'), 'step_order': step.get('step_order', 0),
                    'action': step.get('action', ''), 'selector_value': step.get('selector_value', ''),
                    'input_value': step.get('input_value', ''), 'description': step.get('description', ''),
                    'status': 'error', 'error': error_msg,
                    'screenshot': failure_screenshot, 'duration': failed_step_duration
                })
                uat_logger.error(f"⭐⭐ [步骤记录] 当前失败步骤 ID={step.get('id')} 已记录到列表")
            elif step_results_list and step_results_list[-1]['status'] == 'success':
                # 备用：如果上述逆转属不到，把最后一条成功记录改为失败
                step_results_list[-1]['status'] = 'error'
                step_results_list[-1]['error'] = error_msg
                step_results_list[-1]['screenshot'] = failure_screenshot
            
            # 保存运行历史记录（确保即使浏览器关闭也能保存）
            try:
                run_id = db.create_run_history(case_id, 'error', duration, error_msg, extracted_text, expected_text)
                # 更新 screenshots 字段
                try:
                    conn = __import__('sqlite3').connect(db.db_path)
                    conn.execute("UPDATE run_history SET screenshots = ? WHERE id = ?",
                                 (json.dumps(screenshots), run_id))
                    conn.commit(); conn.close()
                except Exception:
                    pass
                for sr in step_results_list:
                    db.create_step_result(run_id, sr['step_id'], sr['step_order'], sr['action'],
                        sr['selector_value'], sr['input_value'], sr['description'],
                        sr['status'], sr['error'], sr['screenshot'], sr['duration'])
                uat_logger.info(f"运行历史记录已保存，Run ID: {run_id}")
            except Exception as history_error:
                uat_logger.error(f"保存运行历史记录失败: {history_error}")
            
            if browser_started and not browser_closed_manually:
                try:
                    sync_close_browser()
                except Exception:
                    pass
            
            _failed_sid = None
            if visual_match_failed and 'step' in dir() and step:
                _failed_sid = step.get('id')
            return jsonify({
                'success': False,
                'status': 'error',
                'duration': duration,
                'error': error_msg,
                'browser_closed': browser_closed_manually,
                'stopped': ('用户已停止执行' in error_msg),
                'failure_screenshot': failure_screenshot or None,
                'failed_step_id': _failed_sid,
                'need_relearn': bool(
                    visual_match_failed and getattr(visual_match_failed, 'need_relearn', False)
                ),
                'best_score': float(
                    getattr(visual_match_failed, 'best_score', 0.0) if visual_match_failed else 0.0
                ),
            })
            
    except Exception as e:
        # 最外层异常处理 - 确保历史记录被保存
        duration = round(time.time() - start_time, 2)
        if _case_run_cancelled(user_id) or '用户已停止执行' in str(e):
            stop_msg = '用户已停止执行'
            uat_logger.info(f"测试用例 #{case_id} 已由用户停止（外层）")
            try:
                db.create_run_history(case_id, 'stopped', duration, stop_msg, extracted_text, expected_text)
            except Exception:
                pass
            try:
                force_reset_execution_state()
            except Exception:
                pass
            return jsonify({
                'success': False,
                'status': 'stopped',
                'duration': duration,
                'error': stop_msg,
                'stopped': True,
            })
        error_msg = str(e)
        uat_logger.error(f"运行测试用例时发生严重错误: {error_msg}")
        
        # 尝试保存运行历史记录
        try:
            run_id = db.create_run_history(case_id, 'error', duration, f"执行异常: {error_msg}", extracted_text, expected_text)
            uat_logger.info(f"异常情况下运行历史记录已保存，Run ID: {run_id}")
        except Exception as history_error:
            uat_logger.error(f"异常情况下保存运行历史记录失败: {history_error}")
        
        return jsonify({
            'success': False,
            'error': error_msg,
            'stopped': False,
        }), 500
    finally:
        if machine_lock_acquired:
            try:
                from execution_lock import release as release_machine_lock

                release_machine_lock()
            except ImportError:
                pass
        with _case_run_lock:
            job = _case_run_jobs.get(user_id)
            if job:
                job['active'] = False
                job['finished_at'] = time.time()
                job['duration'] = round(time.time() - job.get('started_at', time.time()), 2)


def _case_run_status_payload(user_id: int) -> dict:
    """当前用户 Web 用例步骤运行任务状态（与 /api/cases/current-run/status 响应体一致）。"""
    with _case_run_lock:
        job = _case_run_jobs.get(user_id)
    if not job:
        return {
            'success': True,
            'active': False,
            'total_steps': 0,
            'completed_steps': 0,
            'progress': 0,
            'message': '暂无运行任务',
        }

    total_steps = max(1, int(job.get('total_steps', 0) or 0))
    completed_steps = int(job.get('completed_steps', 0) or 0)
    progress = min(100, int((completed_steps / total_steps) * 100))
    return {
        'success': True,
        'active': bool(job.get('active')),
        'case_id': job.get('case_id'),
        'case_name': job.get('case_name', ''),
        'total_steps': int(job.get('total_steps', 0) or 0),
        'completed_steps': completed_steps,
        'current_step_order': int(job.get('current_step_order', 0) or 0),
        'current_action': job.get('current_action', ''),
        'progress': progress,
        'cancel_requested': bool(job.get('cancel_requested')),
        'message': job.get('message', ''),
        'duration': job.get('duration'),
    }


def _dataset_current_run_payload(user_id: int) -> dict:
    """当前用户数据驱动任务状态（与 /api/datasets/current-run/status 响应体一致）。"""
    run_id, job = _get_current_user_dataset_job(user_id)
    if not job:
        return {'success': True, 'active': False, 'message': '暂无运行任务'}

    finished = bool(job.get('finished'))
    total = int(job.get('total', 0) or 0)
    completed = int(job.get('completed', 0) or 0)
    return {
        'success': True,
        'active': not finished,
        'finished': finished,
        'run_id': run_id,
        'dataset_id': job.get('dataset_id'),
        'case_id': job.get('case_id'),
        'total': total,
        'completed': completed,
        'successful_rows': int(job.get('successful_rows', 0) or 0),
        'failed_rows': int(job.get('failed_rows', 0) or 0),
        'current_row_index': job.get('current_row_index'),
        'error': job.get('error'),
    }


@app.route('/api/ui/current-runs/status', methods=['GET'])
@login_required
@api_error_handler
def api_ui_current_runs_status():
    """合并用例与数据驱动的运行状态，供全站轮询一次请求拿到全部信息。"""
    uid = current_user.id
    return jsonify({
        'success': True,
        'case': _case_run_status_payload(uid),
        'dataset': _dataset_current_run_payload(uid),
    })


@app.route('/api/cases/current-run/status', methods=['GET'])
@login_required
@api_error_handler
def api_case_run_status():
    return jsonify(_case_run_status_payload(current_user.id))


@app.route('/api/cases/current-run/stop', methods=['POST'])
@login_required
@api_error_handler
def api_stop_case_run():
    user_id = current_user.id
    with _case_run_lock:
        job = _case_run_jobs.get(user_id)
        if not job or not job.get('active'):
            # 幂等：避免前端在状态切换瞬间看到“没有任务在运行”的误报弹窗
            return jsonify({'success': True, 'message': '当前没有运行任务'})
        job['cancel_requested'] = True
        job['active'] = False
        job['message'] = '已停止执行'
        total_steps = int(job.get('total_steps', 0) or 0)
        job['completed_steps'] = total_steps
        job['finished_at'] = time.time()
        job['duration'] = round(time.time() - job.get('started_at', time.time()), 2)

    threading.Thread(target=_force_stop_browser_async, daemon=True, name='stop-case-run').start()

    return jsonify({'success': True, 'message': '已发送停止请求'})

@app.route('/run-history', methods=['GET'])
@login_required
def run_history_page():
    """运行历史记录页面"""
    return render_template('run_history.html')


@app.route('/api/run-history', methods=['GET'])
@login_required
def get_run_history():
    """获取所有运行历史记录（支持分页、按测试用例ID过滤、按项目ID过滤和搜索）"""
    try:
        # 获取分页参数
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        case_id = request.args.get('case_id', type=int)
        project_id = request.args.get('project_id', type=int)
        search_text = request.args.get('search_text', type=str)
        raw_status = request.args.get('status', type=str) or ''
        status_filter = raw_status if raw_status in ('passed', 'failed') else None
        
        db = Database()
        history = db.get_all_run_history(page, page_size, case_id, search_text, project_id, status_filter=status_filter)
        total = db.get_run_history_count(case_id, search_text, project_id, status_filter=status_filter)
        
        return jsonify({
            'success': True,
            'history': history,
            'total': total,
            'page': page,
            'page_size': page_size
        })
    except Exception as e:
        uat_logger.error(f"获取运行历史记录失败: {e}")
        return jsonify({
            'success': False,
            'error': f'获取运行历史记录失败: {str(e)}'
        }), 500

@app.route('/api/run-history/<int:record_id>', methods=['DELETE'])
def delete_run_history(record_id):
    """删除运行历史记录"""
    try:
        db = Database()
        success = db.delete_run_history(record_id)
        if success:
            return jsonify({
                'success': True,
                'message': '运行历史记录删除成功'
            })
        else:
            return jsonify({
                'success': False,
                'error': '运行历史记录不存在'
            }), 404
    except Exception as e:
        uat_logger.error(f"删除运行历史记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/run-history', methods=['DELETE'])
def delete_all_run_history():
    """删除所有运行历史记录"""
    try:
        db = Database()
        success = db.delete_all_run_history()
        if success:
            return jsonify({
                'success': True,
                'message': '所有运行历史记录删除成功'
            })
        else:
            return jsonify({
                'success': True,
                'message': '没有运行历史记录可删除'
            })
    except Exception as e:
        uat_logger.error(f"删除所有运行历史记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/run-history/<int:record_id>', methods=['GET'])
def get_run_history_detail(record_id):
    """获取运行历史记录详情"""
    try:
        db = Database()
        record = db.get_run_history_detail(record_id)
        if record:
            return jsonify({
                'success': True,
                'record': record
            })
        else:
            return jsonify({
                'success': False,
                'error': '运行历史记录不存在'
            }), 404
    except Exception as e:
        uat_logger.error(f"获取运行历史记录详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/cases/<int:case_id>/run-history', methods=['GET'])
def get_case_run_history(case_id):
    """获取指定测试用例的运行历史记录"""
    try:
        db = Database()
        history = db.get_case_run_history(case_id)
        return jsonify({
            'success': True,
            'history': history
        })
    except Exception as e:
        uat_logger.error(f"获取测试用例运行历史记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== 测试报告API ====================

# 测试报告页面
@app.route('/test_report')
@login_required
def test_report():
    return render_template('test_report.html')

# API: 获取测试统计概览
@app.route('/api/report/overview', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_report_overview():
    """获取测试统计概览"""
    try:
        project_id = request.args.get('project_id')
        if project_id is not None:
            project_id = int(project_id)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        report_generator = TestReportGenerator()
        overview = report_generator.get_statistics_overview(project_id, start_date, end_date)
        
        return jsonify({
            'success': True,
            'data': overview
        })
    except Exception as e:
        uat_logger.error(f"获取测试统计概览失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# API: 获取状态分布
@app.route('/api/report/status-distribution', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_status_distribution():
    """获取状态分布"""
    try:
        project_id = request.args.get('project_id')
        if project_id is not None:
            project_id = int(project_id)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        report_generator = TestReportGenerator()
        status_dist = report_generator.get_status_distribution(project_id, start_date, end_date)
        
        return jsonify({
            'success': True,
            'data': status_dist
        })
    except Exception as e:
        uat_logger.error(f"获取状态分布失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# API: 获取耗时分布
@app.route('/api/report/duration-distribution', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_duration_distribution():
    """获取耗时分布"""
    try:
        project_id = request.args.get('project_id')
        if project_id is not None:
            project_id = int(project_id)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        report_generator = TestReportGenerator()
        duration_dist = report_generator.get_duration_distribution(project_id, start_date, end_date)
        
        return jsonify({
            'success': True,
            'data': duration_dist
        })
    except Exception as e:
        uat_logger.error(f"获取耗时分布失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# API: 获取趋势数据
@app.route('/api/report/trend', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_trend_data():
    """获取趋势数据"""
    try:
        project_id = request.args.get('project_id')
        if project_id is not None:
            project_id = int(project_id)
        days = request.args.get('days', 30)
        if days is not None:
            days = int(days)
        
        report_generator = TestReportGenerator()
        trend_data = report_generator.get_trend_data(project_id, days)
        
        return jsonify({
            'success': True,
            'data': trend_data
        })
    except Exception as e:
        uat_logger.error(f"获取趋势数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# API: 获取用例统计
@app.route('/api/report/case-statistics', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_case_statistics():
    """获取用例统计"""
    try:
        project_id = request.args.get('project_id')
        if project_id is not None:
            project_id = int(project_id)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        report_generator = TestReportGenerator()
        case_stats = report_generator.get_case_statistics(project_id, start_date, end_date)
        
        return jsonify({
            'success': True,
            'data': case_stats
        })
    except Exception as e:
        uat_logger.error(f"获取用例统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# API: 获取项目统计
@app.route('/api/report/project-statistics', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_project_statistics():
    """获取项目统计"""
    try:
        project_id = request.args.get('project_id')
        if project_id is not None:
            project_id = int(project_id)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        report_generator = TestReportGenerator()
        project_stats = report_generator.get_project_statistics(project_id, start_date, end_date)
        
        return jsonify({
            'success': True,
            'data': project_stats
        })
    except Exception as e:
        uat_logger.error(f"获取项目统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# API: 导出报告
@app.route('/api/report/export', methods=['POST'])
@api_error_handler
@log_api_request
def api_export_report():
    """导出测试报告"""
    try:
        data = request.get_json(silent=True) or {}
        format_type = data.get('format', 'html')
        project_id = data.get('project_id')
        if project_id is not None:
            project_id = int(project_id)
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        filename = data.get('filename')
        
        # 收集报告数据
        report_generator = TestReportGenerator()
        report_data = {
            'overview': report_generator.get_statistics_overview(project_id, start_date, end_date),
            'status_distribution': report_generator.get_status_distribution(project_id, start_date, end_date),
            'duration_distribution': report_generator.get_duration_distribution(project_id, start_date, end_date),
            'case_statistics': report_generator.get_case_statistics(project_id, start_date, end_date),
            'project_statistics': report_generator.get_project_statistics(project_id, start_date, end_date)
        }
        
        # 导出报告
        exporter = ReportExporter()
        
        if format_type == 'html':
            filepath = exporter.export_to_html(report_data, filename)
        elif format_type == 'excel':
            filepath = exporter.export_to_excel(report_data, filename)
        elif format_type == 'pdf':
            filepath = exporter.export_to_pdf(report_data, filename)
        else:
            return jsonify({
                'success': False,
                'error': f'不支持的导出格式: {format_type}'
            }), 400
        
        return jsonify({
            'success': True,
            'filepath': filepath
        })
    except Exception as e:
        uat_logger.error(f"导出报告失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== 变量管理API ====================

@app.route('/api/variables', methods=['GET'])
@login_required
def api_get_variables():
    scope = request.args.get('scope')
    project_id = request.args.get('project_id', type=int)
    case_id = request.args.get('case_id', type=int)
    _db = Database()
    variables = _db.get_variables(scope, project_id, case_id)
    return jsonify({'success': True, 'variables': variables})

@app.route('/api/variables', methods=['POST'])
@login_required
def api_create_variable():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    value = data.get('value', '')
    scope = data.get('scope', 'global')
    if not name:
        return jsonify({'success': False, 'error': '变量名不能为空'}), 400
    if scope not in ('global', 'project', 'case'):
        return jsonify({'success': False, 'error': '无效的作用域'}), 400
    _db = Database()
    var_id = _db.create_variable(name, value, scope, data.get('project_id'), data.get('case_id'), data.get('description', ''))
    return jsonify({'success': True, 'var_id': var_id})

@app.route('/api/variables/<int:var_id>', methods=['PUT'])
@login_required
def api_update_variable(var_id):
    data = request.get_json(silent=True) or {}
    _db = Database()
    success = _db.update_variable(var_id, data.get('name'), data.get('value'), data.get('description'))
    return jsonify({'success': success})

@app.route('/api/variables/<int:var_id>', methods=['DELETE'])
@login_required
def api_delete_variable(var_id):
    _db = Database()
    success = _db.delete_variable(var_id)
    return jsonify({'success': success})

# ==================== 步骤执行结果API ====================

@app.route('/api/run-history/<int:record_id>/steps', methods=['GET'])
def get_run_step_results(record_id):
    """获取某次运行的步骤级结果"""
    try:
        _db = Database()
        results = _db.get_step_results(record_id)
        return jsonify({'success': True, 'step_results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== 定时调度API ====================

@app.route('/api/schedules', methods=['GET'])
@login_required
@feature_required('schedule')
def api_get_schedules():
    _db = Database()
    schedules = _db.get_all_schedules()
    return jsonify({'success': True, 'schedules': schedules})

@app.route('/api/schedules', methods=['POST'])
@login_required
@feature_required('schedule')
@role_required('admin', 'tester')
def api_create_schedule():
    try:
        data = request.get_json(silent=True) or {}
        name = data.get('name', '').strip()
        case_ids = data.get('case_ids', [])
        cron_expr = data.get('cron_expr', '').strip()
        is_active = data.get('is_active', 1)
        if not name or not case_ids or not cron_expr:
            return jsonify({'success': False, 'error': '名称、用例ID列表和cron表达式不能为空'}), 400
        _db = Database()
        project_id = data.get('project_id')
        raw_ec = data.get('execution_count', -1)
        try:
            execution_count = int(raw_ec)
        except (TypeError, ValueError):
            execution_count = -1
        schedule_id = _db.create_schedule(name, case_ids, cron_expr, project_id=project_id, is_active=is_active, execution_count=execution_count)
        _sync_schedule_job(schedule_id)
        return jsonify({'success': True, 'schedule_id': schedule_id})
    except Exception as e:
        uat_logger.error(f"创建定时任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedules/<int:schedule_id>', methods=['PUT'])
@login_required
@feature_required('schedule')
@role_required('admin', 'tester')
def api_update_schedule(schedule_id):
    try:
        data = request.get_json(silent=True) or {}
        _db = Database()
        raw_ec = data.get('execution_count')
        execution_count = None
        if raw_ec is not None:
            try:
                execution_count = int(raw_ec)
            except (TypeError, ValueError):
                execution_count = None
        success = _db.update_schedule(schedule_id,
            name=data.get('name'), cron_expr=data.get('cron_expr'),
            is_active=data.get('is_active'), case_ids=data.get('case_ids'),
            project_id=data.get('project_id'), execution_count=execution_count)
        if success:
            _sync_schedule_job(schedule_id)
        return jsonify({'success': success})
    except Exception as e:
        uat_logger.error(f"更新定时任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedules/<int:schedule_id>', methods=['DELETE'])
@login_required
@feature_required('schedule')
@role_required('admin', 'tester')
def api_delete_schedule(schedule_id):
    _db = Database()
    success = _db.delete_schedule(schedule_id)
    try:
        scheduler.remove_job(f'schedule_{schedule_id}')
    except Exception:
        pass
    return jsonify({'success': success})

@app.route('/api/schedules/<int:schedule_id>/run', methods=['POST'])
@login_required
@feature_required('schedule')
@role_required('admin', 'tester')
def api_run_schedule_now(schedule_id):
    """立即执行定时任务"""
    _db = Database()
    schedules = _db.get_all_schedules()
    schedule = None
    for s in schedules:
        if s['id'] == schedule_id:
            schedule = s
            break

    if not schedule:
        return jsonify({'success': False, 'error': '定时任务不存在'}), 404

    try:
        ec = int(schedule.get('execution_count', 0))
    except (TypeError, ValueError):
        ec = 0
    if ec == 0:
        return jsonify({'success': False, 'error': '剩余执行次数为 0（请设为 -1 无限次或大于 0）'}), 400

    try:
        # 异步执行
        import threading
        thread = threading.Thread(
            target=_run_scheduled_cases,
            args=(schedule_id, schedule['case_ids'])
        )
        thread.start()
        return jsonify({'success': True, 'message': '任务已开始执行'})
    except Exception as e:
        uat_logger.error(f"手动执行任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedules/<int:schedule_id>/history', methods=['GET'])
@login_required
@feature_required('schedule')
def api_get_schedule_history(schedule_id):
    """获取定时任务执行历史"""
    _db = Database()
    history = _db.get_schedule_history(schedule_id)
    return jsonify({'success': True, 'history': history})

# ==================== API Token 管理 ====================

@app.route('/api/tokens', methods=['GET'])
@login_required
@role_required('admin')
def api_get_tokens():
    _db = Database()
    tokens = _db.get_all_tokens()
    return jsonify({'success': True, 'tokens': tokens})

@app.route('/api/tokens', methods=['POST'])
@login_required
@role_required('admin')
def api_create_token():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Token名称不能为空'}), 400
    token_val = secrets.token_urlsafe(32)
    _db = Database()
    token_id = _db.create_api_token(name, token_val, data.get('project_id'), data.get('expires_at'))
    return jsonify({'success': True, 'token_id': token_id, 'token': token_val,
                    'message': '请妥善保存此Token，它只显示一次！'})

@app.route('/api/tokens/<int:token_id>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_revoke_token(token_id):
    _db = Database()
    success = _db.revoke_token(token_id)
    return jsonify({'success': success})

# ==================== Webhook/CI 触发接口 ====================

@app.route('/api/trigger/<int:project_id>', methods=['POST'])
@token_or_login_required
def api_trigger_project(project_id):
    """CI/CD 触发指定项目的所有用例执行"""
    try:
        _db = Database()
        cases = _db.get_project_cases(project_id, case_type="ui")
        if not cases:
            return jsonify({'success': False, 'error': '项目没有测试用例'}), 400
        case_ids = [c['id'] for c in cases]
        uat_logger.info(f"CI/CD 触发项目 #{project_id} 执行，共 {len(case_ids)} 个用例")
        uid = current_user.id if current_user.is_authenticated else None
        tid = None
        if uid is not None:
            try:
                tid = _db.get_user_tenant_id(int(uid))
            except Exception:
                tid = None
        _exec_ctx = ExecutionContext(
            user_id=uid,
            tenant_id=tid,
            trigger="ci",
            on_case_failure=_on_case_execution_failure,
            extra={"project_id": project_id},
        )
        results = sync_execute_multiple_test_cases(case_ids, _db, execution_context=_exec_ctx)
        try:
            sync_close_browser()
        except Exception:
            pass
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        uat_logger.error(f"CI 触发执行失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/trigger/cases', methods=['POST'])
@token_or_login_required
def api_trigger_cases():
    """CI/CD 触发指定用例列表执行"""
    try:
        data = request.get_json(silent=True) or {}
        case_ids = data.get('case_ids', [])
        if not case_ids:
            return jsonify({'success': False, 'error': '缺少 case_ids 参数'}), 400
        _db = Database()
        uat_logger.info(f"CI/CD 触发用例列表执行: {case_ids}")
        uid = current_user.id if current_user.is_authenticated else None
        tid = None
        if uid is not None:
            try:
                tid = _db.get_user_tenant_id(int(uid))
            except Exception:
                tid = None
        _exec_ctx = ExecutionContext(
            user_id=uid,
            tenant_id=tid,
            trigger="ci",
            on_case_failure=_on_case_execution_failure,
        )
        results = sync_execute_multiple_test_cases(case_ids, _db, execution_context=_exec_ctx)
        try:
            sync_close_browser()
        except Exception:
            pass
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        uat_logger.error(f"CI 触发执行失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== 数据驱动测试接口 ====================

def _dataset_job_update(run_id: str, **kwargs):
    with _dataset_run_lock:
        if run_id in _dataset_run_jobs:
            _dataset_run_jobs[run_id].update(kwargs)


def _dataset_run_worker(run_id: str, user_id: int, dataset_id: int, case_id: int):
    """后台线程：按数据行执行用例，每行更新进度。"""
    import time as _time

    machine_lock_acquired = False

    def resolve_with_row(text, row_dict):
        if not text:
            return text
        import re

        def replace(m):
            key = m.group(1).strip()
            if key.startswith('row.'):
                field = key[4:]
                return str(row_dict.get(field, m.group(0)))
            return m.group(0)

        return re.sub(r'\{\{(.+?)\}\}', replace, text)

    try:
        _db = Database()
        dataset = _db.get_dataset(dataset_id)
        if not dataset:
            _dataset_job_update(run_id, finished=True, success=False, error='数据集不存在')
            return
        rows = _db.get_data_rows(dataset_id)
        if not rows:
            _dataset_job_update(run_id, finished=True, success=False, error='数据集没有数据行')
            return
        case = _db.get_test_case_v2(case_id)
        if not case:
            _dataset_job_update(run_id, finished=True, success=False, error='测试用例不存在')
            return
        if _app_case_type(case) == 'api':
            _dataset_job_update(
                run_id,
                finished=True,
                success=False,
                error='数据驱动执行仅支持 Web 用例；接口用例请使用接口测试运行入口。',
            )
            return
        steps = _db.get_case_steps(case_id)
        if not steps:
            _dataset_job_update(run_id, finished=True, success=False, error='测试用例没有步骤')
            return

        machine_lock_acquired = False
        try:
            from execution_lock import acquire as acquire_machine_lock

            machine_lock_acquired = acquire_machine_lock(
                owner=f"dataset:{dataset_id}:case:{case_id}", timeout_sec=120
            )
            if not machine_lock_acquired:
                _dataset_job_update(
                    run_id,
                    finished=True,
                    success=False,
                    error='本机已有自动化任务在执行，请稍后再试。',
                )
                return
        except ImportError:
            pass

        with _dataset_run_lock:
            if run_id in _dataset_run_jobs:
                _dataset_run_jobs[run_id]['total'] = len(rows)

        uat_logger.info(f"数据驱动执行：数据集#{dataset_id}，用例#{case_id}，共{len(rows)}行数据")

        run_results = []
        total_success = 0
        total_fail = 0
        cancelled = False

        for row_info in rows:
            with _dataset_run_lock:
                if _dataset_run_jobs.get(run_id, {}).get('cancel_requested'):
                    cancelled = True
                    break
            row_data = {
                k: _normalize_dataset_cell_value(v)
                for k, v in (row_info.get('data') or {}).items()
            }
            row_index = row_info['row_index']
            _dataset_job_update(run_id, current_row_index=row_index)

            uat_logger.info(f"[数据驱动] 准备执行第{row_index}行数据，重启浏览器...")
            try:
                sync_close_browser()
                uat_logger.info(f"[数据驱动] 浏览器已关闭")
            except Exception as e:
                uat_logger.debug(f"[数据驱动] 关闭浏览器时出错（可忽略）: {e}")

            _time.sleep(0.5)
            uat_logger.info(f"[数据驱动] 启动新浏览器实例...")

            start_time = _time.time()
            status = 'success'
            error_msg = ''
            step_results_list = []

            try:
                sync_start_browser()
                initial_nav_url, nav_source = _resolve_case_navigation_url_for_data_row(
                    _db,
                    case,
                    case_id,
                    steps,
                    row_data,
                    case.get('project_id'),
                    resolve_with_row,
                )
                if initial_nav_url:
                    uat_logger.log_automation_step(
                        'navigate',
                        initial_nav_url,
                        f'数据驱动 行{row_index} 测试开始时导航({nav_source})',
                    )
                    sync_navigate_to(initial_nav_url)
                else:
                    uat_logger.warning(
                        f'[数据驱动] 行{row_index} 未解析到用例初始 URL（case.url 或步骤中首个有效 navigate），'
                        '若首步为 enter_iframe 等需已有页面，可能失败；请配置用例地址或在首步增加导航。'
                    )

                for step_idx, step in enumerate(steps):
                    with _dataset_run_lock:
                        if _dataset_run_jobs.get(run_id, {}).get('cancel_requested'):
                            raise Exception('用户已停止执行')
                    selector_value = resolve_with_row(
                        _db.resolve_variables(step.get('selector_value', ''), project_id=case.get('project_id'), case_id=case_id),
                        row_data
                    )
                    input_value = resolve_with_row(
                        _db.resolve_variables(step.get('input_value', ''), project_id=case.get('project_id'), case_id=case_id),
                        row_data
                    )
                    url_value = resolve_with_row(
                        _db.resolve_variables(step.get('url', '') or '', project_id=case.get('project_id'), case_id=case_id),
                        row_data,
                    )
                    description = resolve_with_row(
                        _db.resolve_variables(step.get('description', '') or '', project_id=case.get('project_id'), case_id=case_id),
                        row_data,
                    )

                    step_start = _time.time()
                    step_status = 'success'
                    step_error = ''
                    try:
                        action = step.get('action', '')
                        selector_type = step.get('selector_type', 'css')
                        iframe_sel = _effective_step_iframe_selector(
                            automation,
                            _db,
                            step,
                            case.get('project_id'),
                            case_id,
                            row_resolve_fn=lambda t: resolve_with_row(t, row_data),
                        )
                        if action == 'navigate':
                            raw_url = (url_value or input_value or case.get('url') or '').strip()
                            fixed_url, url_err = _validate_and_fix_url(raw_url)
                            if url_err:
                                raise Exception(url_err)
                            if fixed_url:
                                sync_navigate_to(fixed_url)
                        elif action == 'click':
                            if selector_value:
                                _repeat = _norm_click_repeat_count(step.get('click_repeat_count'))
                                for _r in range(_repeat):
                                    with _dataset_run_lock:
                                        if _dataset_run_jobs.get(run_id, {}).get('cancel_requested'):
                                            raise Exception('用户已停止执行')
                                    sync_click_element(
                                        selector_value,
                                        selector_type,
                                        iframe_selector=iframe_sel,
                                        locator_candidates=step.get('locator_candidates') or None,
                                    )
                        elif action == 'input':
                            if selector_value:
                                if input_value is None or str(input_value) == '':
                                    raise Exception("输入步骤缺少有效输入值")
                                safe_input_value = input_value
                                sync_fill_input(
                                    selector_value,
                                    safe_input_value,
                                    selector_type,
                                    iframe_selector=iframe_sel,
                                    locator_candidates=step.get('locator_candidates') or None,
                                )
                            else:
                                raise Exception("输入操作缺少选择器")
                        elif action == 'batch_input':
                            b_pairs = parse_batch_input_lines(input_value or '')
                            if not b_pairs:
                                raise Exception("批量输入步骤缺少有效行")
                            for bsel, bval in b_pairs:
                                with _dataset_run_lock:
                                    if _dataset_run_jobs.get(run_id, {}).get('cancel_requested'):
                                        raise Exception('用户已停止执行')
                                sync_fill_input(
                                    bsel,
                                    bval,
                                    selector_type,
                                    iframe_selector=iframe_sel,
                                    locator_candidates=None,
                                )
                        elif action == 'hover':
                            if selector_value:
                                sync_hover_element(selector_value, selector_type, iframe_selector=iframe_sel)
                                sync_wait_for_timeout(1000)
                            else:
                                raise Exception("悬停操作缺少选择器")
                        elif action == 'double_click':
                            if selector_value:
                                sync_double_click_element(selector_value, selector_type, iframe_selector=iframe_sel)
                                sync_wait_for_timeout(2000)
                            else:
                                raise Exception("双击操作缺少选择器")
                        elif action == 'right_click':
                            if selector_value:
                                sync_right_click_element(selector_value, selector_type, iframe_selector=iframe_sel)
                                sync_wait_for_timeout(1000)
                            else:
                                raise Exception("右键点击操作缺少选择器")
                        elif action == 'wait':
                            if input_value:
                                try:
                                    wait_time = (
                                        int(input_value) * 1000
                                        if int(input_value) < 1000
                                        else int(input_value)
                                    )
                                    sync_wait_for_timeout(wait_time)
                                except ValueError:
                                    raise Exception(f"无效的等待时间值: {input_value}")
                            else:
                                sync_wait_for_timeout(1000)
                        elif action == 'select':
                            if selector_value and input_value:
                                sync_select_option(
                                    selector_value,
                                    input_value,
                                    selector_type,
                                    iframe_selector=iframe_sel,
                                )
                                sync_wait_for_timeout(1000)
                        elif action == 'date':
                            if selector_value and input_value:
                                sync_select_date(selector_value, input_value)
                                sync_wait_for_timeout(1000)
                        elif action == 'scroll':
                            _run_db_step_scroll(input_value or "", iframe_selector=iframe_sel)
                            sync_wait_for_timeout(1500)
                        elif action == 'swipe':
                            if selector_value:
                                direction = 'up'
                                distance = 100
                                if input_value:
                                    parts = input_value.split(':')
                                    if len(parts) == 2:
                                        direction = parts[0]
                                        try:
                                            distance = int(parts[1])
                                        except ValueError:
                                            uat_logger.warning(
                                                f"无效的滑动距离值: {parts[1]}，使用默认值 100"
                                            )
                                    else:
                                        direction = input_value
                                sync_swipe_element(
                                    selector_value,
                                    direction,
                                    distance,
                                    selector_type,
                                    iframe_selector=iframe_sel,
                                )
                                sync_wait_for_timeout(1500)
                            else:
                                raise Exception("滑动操作缺少选择器")
                        elif action == 'verify':
                            verify_type = input_value if input_value else 'auto'
                            sync_verify_element(
                                selector=selector_value,
                                verify_type=verify_type,
                                selector_type=selector_type,
                                iframe_selector=iframe_sel,
                                locator_candidates=step.get('locator_candidates') or None,
                            )
                            sync_wait_for_timeout(1500)
                        elif action == 'extract_text' or action == 'text_compare':
                            _run_extract_text_automation_step(
                                action,
                                step,
                                selector_value,
                                input_value,
                                description,
                                selector_type,
                                iframe_sel,
                                locator_candidates=step.get('locator_candidates') or None,
                            )
                        elif action == 'extract_json':
                            if selector_value:
                                sync_extract_element_json(selector_value, selector_type)
                                sync_wait_for_timeout(1000)
                            else:
                                raise Exception("提取JSON数据时缺少选择器")
                        elif action == 'assert':
                            _run_assert_automation_step(
                                step, selector_value, input_value, selector_type, iframe_sel
                            )
                        elif action == 'enter_iframe':
                            if selector_value:
                                sync_enter_iframe(selector_value, selector_type)
                            else:
                                raise Exception('进入 iframe 步骤缺少选择器')
                        elif action == 'exit_iframe':
                            sync_exit_iframe()
                        elif action:
                            raise Exception(
                                f"数据驱动执行不支持的操作类型「{action}」。"
                                "支持的类型：navigate, click, input, batch_input, hover, double_click, right_click, wait, select, date, scroll, swipe, verify, extract_text, text_compare, extract_json, assert, enter_iframe, exit_iframe。"
                            )
                    except Exception as e:
                        step_status = 'failed'
                        step_error = str(e)
                        status = 'failed'
                        error_msg = f"行{row_index} 步骤{step_idx+1}({step.get('action','')}) 失败: {e}"

                    step_results_list.append({
                        'step_order': step_idx + 1,
                        'action': step.get('action', ''),
                        'selector_value': selector_value,
                        'input_value': input_value,
                        'status': step_status,
                        'error': step_error,
                        'duration': round(_time.time() - step_start, 3)
                    })
                    if status == 'failed':
                        break
            except Exception as e:
                status = 'failed'
                error_msg = str(e)

            duration = round(_time.time() - start_time, 3)
            history_id = _db.create_run_history(
                case_id=case_id,
                status=status,
                duration=duration,
                error=f"[数据驱动 行{row_index}] {error_msg}" if error_msg else '',
                extracted_text=f"数据驱动 行{row_index}: {str(row_data)}"
            )

            if status == 'success':
                total_success += 1
            else:
                total_fail += 1

            run_results.append({
                'row_index': row_index,
                'row_data': row_data,
                'status': status,
                'error': error_msg,
                'duration': duration,
                'history_id': history_id,
                'steps': step_results_list
            })

            _dataset_job_update(
                run_id,
                completed=len(run_results),
                successful_rows=total_success,
                failed_rows=total_fail,
                current_row_index=row_index,
            )

        try:
            sync_close_browser()
        except Exception:
            pass

        if cancelled:
            _dataset_job_update(
                run_id,
                finished=True,
                success=False,
                error='用户已停止执行',
                completed=len(run_results),
                results=run_results,
            )
            return

        uat_logger.info(f"数据驱动执行完成：成功{total_success}行，失败{total_fail}行")
        _dataset_job_update(
            run_id,
            finished=True,
            success=True,
            successful_rows=total_success,
            failed_rows=total_fail,
            completed=len(rows),
            results=run_results,
            error=None,
        )
    except Exception as e:
        uat_logger.log_exception("dataset_run_worker", e)
        try:
            sync_close_browser()
        except Exception:
            pass
        _dataset_job_update(run_id, finished=True, success=False, error=str(e))
    finally:
        if machine_lock_acquired:
            try:
                from execution_lock import release as release_machine_lock

                release_machine_lock()
            except ImportError:
                pass


@app.route('/data-driven')
@login_required
@feature_required('data_driven')
def data_driven_page():
    """数据驱动测试管理页面"""
    return render_template('data_driven.html')

@app.route('/api/datasets', methods=['GET'])
@login_required
def api_list_datasets():
    """获取数据集列表"""
    _db = Database()
    case_id = request.args.get('case_id', type=int)
    project_id = request.args.get('project_id', type=int)
    datasets = _db.get_all_datasets(case_id=case_id, project_id=project_id)
    return jsonify({'success': True, 'datasets': datasets})

@app.route('/api/datasets', methods=['POST'])
@login_required
def api_create_dataset():
    """创建数据集（手动创建，不含数据行）"""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': '数据集名称不能为空'}), 400
    _db = Database()
    dataset_id = _db.create_dataset(
        name=name,
        case_id=data.get('case_id'),
        project_id=data.get('project_id'),
        description=data.get('description', '')
    )
    return jsonify({'success': True, 'dataset_id': dataset_id})

@app.route('/api/datasets/<int:dataset_id>', methods=['GET'])
@login_required
def api_get_dataset(dataset_id):
    """获取数据集详情及数据行"""
    _db = Database()
    dataset = _db.get_dataset(dataset_id)
    if not dataset:
        return jsonify({'success': False, 'error': '数据集不存在'}), 404
    rows = _db.get_data_rows(dataset_id)
    dataset['rows'] = rows
    return jsonify({'success': True, 'dataset': dataset})

@app.route('/api/datasets/<int:dataset_id>', methods=['DELETE'])
@login_required
def api_delete_dataset(dataset_id):
    """删除数据集"""
    _db = Database()
    success = _db.delete_dataset(dataset_id)
    return jsonify({'success': success})


def _normalize_dataset_cell_value(v):
    """
    将上传文件中的单元格转为写入数据集的字符串。
    - Excel 日期单元格在 openpyxl data_only 下常为 datetime，str() 会变成 \"YYYY-MM-DD 00:00:00\"，
      与页面控件回读不一致；午夜时间统一压成纯日期 YYYY-MM-DD。
    - 所有结果 strip，去掉 CSV/粘贴带来的首尾空格。
    - 字符串若已是 \"日期 + 00:00:00\"（及 .0 小数秒）也压成纯日期。
    """
    import re
    from datetime import date, datetime, time as dtime

    if v is None:
        return ''
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, datetime):
        vv = v.replace(microsecond=0) if v.microsecond else v
        if vv.time() == dtime(0, 0, 0):
            return vv.strftime('%Y-%m-%d')
        return vv.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, date):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return str(v).strip()
    s = str(v).strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}\s+00:00:00(?:\.0+)?', s):
        return s[:10]
    return s


@app.route('/api/datasets/upload', methods=['POST'])
@login_required
def api_upload_dataset():
    """上传 CSV 或 Excel 文件创建数据集"""
    import csv
    import io
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '缺少文件'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'error': '文件名为空'}), 400
    filename = file.filename.lower()
    name = request.form.get('name', '').strip() or file.filename.rsplit('.', 1)[0]
    case_id = request.form.get('case_id', type=int)
    project_id = request.form.get('project_id', type=int)
    description = request.form.get('description', '')

    rows_data = []
    try:
        if filename.endswith('.csv'):
            content = file.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                rows_data.append({
                    (k.strip() if k else k): _normalize_dataset_cell_value(val)
                    for k, val in row.items()
                })
        elif filename.endswith(('.xlsx', '.xls')):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
                ws = wb.active
                headers = None
                for row in ws.iter_rows(values_only=True):
                    if headers is None:
                        headers = [
                            (str(c).strip() if c is not None else f'col{i}')
                            for i, c in enumerate(row)
                        ]
                    else:
                        row_dict = {
                            headers[i]: _normalize_dataset_cell_value(v)
                            for i, v in enumerate(row)
                            if i < len(headers)
                        }
                        rows_data.append(row_dict)
                wb.close()
            except ImportError:
                return jsonify({'success': False, 'error': '解析 Excel 需要安装 openpyxl: pip install openpyxl==3.1.2'}), 400
        else:
            return jsonify({'success': False, 'error': '只支持 .csv / .xlsx / .xls 格式'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'文件解析失败: {str(e)}'}), 400

    if not rows_data:
        return jsonify({'success': False, 'error': '文件中没有数据行'}), 400

    _db = Database()
    dataset_id = _db.create_dataset(name=name, case_id=case_id, project_id=project_id, description=description)
    count = _db.add_data_rows(dataset_id, rows_data)
    columns = list(rows_data[0].keys()) if rows_data else []
    return jsonify({'success': True, 'dataset_id': dataset_id, 'row_count': count, 'columns': columns})

@app.route('/api/datasets/<int:dataset_id>/run', methods=['POST'])
@login_required
def api_run_dataset(dataset_id):
    """启动数据驱动后台任务，立即返回 run_id；前端轮询 /api/datasets/run-status/<run_id> 更新进度"""
    data = request.get_json(silent=True) or {}
    case_id = data.get('case_id')
    if not case_id:
        return jsonify({'success': False, 'error': '缺少 case_id 参数'}), 400

    try:
        case_id_int = int(case_id)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'case_id 无效'}), 400

    _db = Database()
    dataset = _db.get_dataset(dataset_id)
    if not dataset:
        return jsonify({'success': False, 'error': '数据集不存在'}), 404

    rows = _db.get_data_rows(dataset_id)
    if not rows:
        return jsonify({'success': False, 'error': '数据集没有数据行'}), 400

    case = _db.get_test_case_v2(case_id_int)
    if not case:
        return jsonify({'success': False, 'error': '测试用例不存在'}), 404

    steps = _db.get_case_steps(case_id_int)
    if not steps:
        return jsonify({'success': False, 'error': '测试用例没有步骤'}), 400

    run_id = secrets.token_urlsafe(24)
    uid = current_user.id
    with _dataset_run_lock:
        _dataset_run_jobs[run_id] = {
            'user_id': uid,
            'dataset_id': dataset_id,
            'case_id': case_id_int,
            'finished': False,
            'success': None,
            'error': None,
            'total': len(rows),
            'completed': 0,
            'successful_rows': 0,
            'failed_rows': 0,
            'results': [],
            'current_row_index': None,
            'cancel_requested': False,
        }

    thread = threading.Thread(
        target=_dataset_run_worker,
        args=(run_id, uid, dataset_id, case_id_int),
        daemon=True,
        name=f'dataset-run-{run_id[:10]}',
    )
    thread.start()

    return jsonify({
        'success': True,
        'run_id': run_id,
        'total': len(rows),
    })


@app.route('/api/datasets/run-status/<run_id>', methods=['GET'])
@login_required
def api_dataset_run_status(run_id):
    """查询数据驱动任务进度；完成后首次请求返回明细并移除任务缓存"""
    with _dataset_run_lock:
        job = _dataset_run_jobs.get(run_id)
    if not job:
        return jsonify({'success': False, 'error': '任务不存在或已结束'}), 404
    if job.get('user_id') != current_user.id:
        return jsonify({'success': False, 'error': '无权访问该任务'}), 403

    resp = {
        'success': True,
        'finished': job['finished'],
        'total': job['total'],
        'completed': job['completed'],
        'successful_rows': job['successful_rows'],
        'failed_rows': job['failed_rows'],
        'current_row_index': job.get('current_row_index'),
    }

    if not job['finished']:
        return jsonify(resp)

    resp['run_success'] = bool(job.get('success'))
    if job.get('error'):
        resp['error'] = job['error']
    if job.get('success'):
        resp['results'] = job.get('results') or []
        resp['successful_rows'] = job['successful_rows']
        resp['failed_rows'] = job['failed_rows']
    with _dataset_run_lock:
        _dataset_run_jobs.pop(run_id, None)

    return jsonify(resp)


@app.route('/api/datasets/run-stop/<run_id>', methods=['POST'])
@login_required
def api_dataset_run_stop(run_id):
    with _dataset_run_lock:
        job = _dataset_run_jobs.get(run_id)
        if not job:
            return jsonify({'success': False, 'error': '任务不存在或已结束'}), 404
        if job.get('user_id') != current_user.id:
            return jsonify({'success': False, 'error': '无权访问该任务'}), 403
        if job.get('finished'):
            return jsonify({'success': False, 'error': '任务已结束'}), 400
        job['cancel_requested'] = True
        job['finished'] = True
        job['success'] = False
        job['error'] = '用户已停止执行'
        job['completed'] = int(job.get('total', 0) or 0)
        job['current_row_index'] = None
    threading.Thread(target=_force_stop_browser_async, daemon=True, name='stop-dataset-run').start()
    return jsonify({'success': True, 'message': '已发送停止请求'})


def _get_current_user_dataset_job(user_id: int):
    """获取当前用户最近的一个数据驱动任务（优先 active）。"""
    with _dataset_run_lock:
        items = list(_dataset_run_jobs.items())
    for run_id, job in reversed(items):
        if job.get('user_id') != user_id:
            continue
        return run_id, job
    return None, None


@app.route('/api/datasets/current-run/status', methods=['GET'])
@login_required
def api_dataset_current_run_status():
    """查询当前用户的数据驱动任务状态（用于跨页面恢复显示/停止）。"""
    return jsonify(_dataset_current_run_payload(current_user.id))


@app.route('/api/datasets/current-run/stop', methods=['POST'])
@login_required
def api_dataset_current_run_stop():
    """停止当前用户最近的数据驱动任务（幂等）。"""
    run_id, job = _get_current_user_dataset_job(current_user.id)
    if not job:
        return jsonify({'success': True, 'message': '当前没有运行任务'})
    if job.get('finished'):
        return jsonify({'success': True, 'message': '任务已结束'})
    return api_dataset_run_stop(run_id)


# ==================== 调度器初始化 ====================

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler(timezone='Asia/Shanghai')

    def _run_scheduled_cases(schedule_id: int, case_ids: list, retry_count: int = 0):
        """调度器回调：执行定时用例（支持重跑）"""
        import datetime
        import time
        from notifications import notify

        _db = Database()
        # 仅首次触发消耗执行次数，重试不重复扣减
        if retry_count == 0:
            try:
                consume_result = _db.consume_schedule_execution(schedule_id)
            except Exception as consume_err:
                uat_logger.error(f"⏰ 定时任务 #{schedule_id} 扣减执行次数失败: {consume_err}")
                return

            if not consume_result.get('allowed'):
                reason = consume_result.get('reason')
                uat_logger.warning(f"⏰ 定时任务 #{schedule_id} 跳过执行: {reason}")
                try:
                    # 若任务已失效/不存在，尝试移除本地job，避免后续重复触发
                    scheduler.remove_job(f'schedule_{schedule_id}')
                except Exception:
                    pass
                return

            if consume_result.get('unlimited'):
                uat_logger.info(f"⏰ 定时任务 #{schedule_id} 执行次数模式: 无限次")
            else:
                uat_logger.info(f"⏰ 定时任务 #{schedule_id} 执行次数已扣减，剩余: {consume_result.get('remaining')}")
                if consume_result.get('exhausted'):
                    uat_logger.info(f"⏰ 定时任务 #{schedule_id} 达到执行次数上限，已自动禁用")
                    try:
                        scheduler.remove_job(f'schedule_{schedule_id}')
                    except Exception:
                        pass

        schedule = None
        for s in _db.get_all_schedules():
            if s['id'] == schedule_id:
                schedule = s
                break

        max_retries = schedule['retry_count'] if schedule else 3
        retry_interval = schedule['retry_interval'] if schedule else 5
        schedule_name = schedule['name'] if schedule else f'任务#{schedule_id}'

        # 创建执行历史记录
        history_id = _db.create_schedule_history(
            schedule_id, case_ids, 'running',
            retry_count, max_retries
        )

        start_time = time.time()
        uat_logger.info(f"⏰ 定时任务 #{schedule_id} 开始执行（第{retry_count + 1}次尝试），用例: {case_ids}")

        machine_lock_acquired = False
        try:
            from execution_lock import acquire as acquire_machine_lock

            machine_lock_acquired = acquire_machine_lock(
                owner=f"schedule:{schedule_id}", timeout_sec=120
            )
            if not machine_lock_acquired:
                _db.update_schedule_history(
                    history_id,
                    'failed',
                    '本机已有自动化任务在执行，跳过本次调度',
                )
                uat_logger.warning(
                    "⏰ 定时任务 #%s 因本机执行锁占用而跳过", schedule_id
                )
                return
        except ImportError:
            pass

        try:
            _spid = schedule.get("project_id") if schedule else None
            _exec_ctx = ExecutionContext(
                user_id=None,
                tenant_id=None,
                trigger="schedule",
                on_case_failure=_on_case_execution_failure,
                extra={"schedule_id": schedule_id, "project_id": _spid},
            )
            results = sync_execute_multiple_test_cases(case_ids, _db, execution_context=_exec_ctx)
            successful = results.get('successful_cases', 0)
            failed = results.get('failed_cases', 0)
            duration = time.time() - start_time

            # 为每个用例发送单独的通知（case_success 或 case_failed）
            case_results = results.get('case_results', [])
            for case_result in case_results:
                case_name = case_result.get('case_name', '未知用例')
                case_status = case_result.get('status', 'unknown')
                case_duration = case_result.get('execution_time', 0)
                case_error = case_result.get('error', '')
                
                if case_status == 'success':
                    notify('case_success', {
                        'case_name': case_name,
                        'duration': case_duration,
                        'executed_at': beijing_now_iso()
                    })
                elif case_status in ('error', 'failed'):
                    notify('case_failed', {
                        'case_name': case_name,
                        'error': case_error or '执行失败',
                        'executed_at': beijing_now_iso()
                    })

            if failed == 0:
                # 全部成功
                _db.update_schedule_history(history_id, 'success')
                uat_logger.info(f"⏰ 定时任务 #{schedule_id} 完成，全部成功")
                # 发送成功通知
                notify('schedule_success', {
                    'schedule_name': schedule_name,
                    'success_count': successful,
                    'total_count': successful + failed,
                    'duration': duration,
                    'executed_at': beijing_now_iso()
                })
            elif retry_count < max_retries:
                # 有失败且还可以重跑
                uat_logger.warning(f"⏰ 定时任务 #{schedule_id} 部分失败，{failed}个用例失败，将在{retry_interval}分钟后重试（{retry_count + 1}/{max_retries}）")
                _db.update_schedule_history(history_id, 'retrying', f'{failed}个用例失败，准备重试')

                # 等待后重跑
                time.sleep(retry_interval * 60)
                _run_scheduled_cases(schedule_id, case_ids, retry_count + 1)
            else:
                # 达到最大重跑次数，最终失败
                _db.update_schedule_history(history_id, 'failed', f'达到最大重试次数，{failed}个用例失败')
                uat_logger.error(f"⏰ 定时任务 #{schedule_id} 达到最大重试次数，执行失败")
                # 发送失败通知（成功和失败都需要发送对应通知）
                notify('schedule_failed', {
                    'schedule_name': schedule_name,
                    'retry_count': retry_count,
                    'success_count': successful,
                    'failed_count': failed,
                    'total_count': successful + failed,
                    'error': f'{failed}个用例失败，达到最大重试次数',
                    'executed_at': beijing_now_iso()
                })

        except Exception as e:
            error_msg = str(e)
            uat_logger.error(f"⏰ 定时任务 #{schedule_id} 执行失败: {error_msg}")

            if retry_count < max_retries:
                uat_logger.warning(f"⏰ 定时任务 #{schedule_id} 将在{retry_interval}分钟后重试（{retry_count + 1}/{max_retries}）")
                _db.update_schedule_history(history_id, 'retrying', f'执行异常: {error_msg}')
                time.sleep(retry_interval * 60)
                _run_scheduled_cases(schedule_id, case_ids, retry_count + 1)
            else:
                _db.update_schedule_history(history_id, 'failed', f'达到最大重试次数: {error_msg}')
                # 发送失败通知
                notify('schedule_failed', {
                    'schedule_name': schedule_name,
                    'retry_count': retry_count,
                    'error': error_msg,
                    'executed_at': beijing_now_iso()
                })
        finally:
            if machine_lock_acquired:
                try:
                    from execution_lock import release as release_machine_lock

                    release_machine_lock()
                except ImportError:
                    pass
            try:
                sync_close_browser()
            except Exception:
                pass
            last_run = utc_sql_str()
            _db.update_schedule(schedule_id, last_run=last_run)

    def _register_schedule_job(schedule_id: int, case_ids: list, cron_expr: str):
        """注册/更新定时任务"""
        job_id = f'schedule_{schedule_id}'
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
        parts = cron_expr.strip().split()
        # 将 Quartz 风格的 ? 替换为 *
        parts = [p if p != '?' else '*' for p in parts]
        try:
            if len(parts) == 6:
                # 6个部分：秒 分 时 日 月 周
                second, minute, hour, day, month, day_of_week = parts
                trigger = CronTrigger(second=second, minute=minute, hour=hour,
                                      day=day, month=month, day_of_week=day_of_week)
            elif len(parts) == 5:
                # 5个部分：分 时 日 月 周
                minute, hour, day, month, day_of_week = parts
                trigger = CronTrigger(minute=minute, hour=hour, day=day,
                                      month=month, day_of_week=day_of_week)
            else:
                uat_logger.warning(f"定时任务 #{schedule_id} cron表达式格式不正确: {cron_expr}")
                return
            scheduler.add_job(
                _run_scheduled_cases,
                trigger,
                args=[schedule_id, case_ids],
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30
            )
            uat_logger.info(f"定时任务 #{schedule_id} 已注册，cron: {cron_expr}")
        except Exception as e:
            uat_logger.error(f"注册定时任务 #{schedule_id} 失败: {e}")

    def _sync_schedule_job(schedule_id: int):
        """根据数据库状态注册或移除 APScheduler 任务（启用、次数、Cron 变更时调用）。"""
        if scheduler is None or not getattr(scheduler, "running", False):
            return
        _db = Database()
        sched = None
        for s in _db.get_all_schedules():
            if s["id"] == schedule_id:
                sched = s
                break
        job_id = f"schedule_{schedule_id}"
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
        if not sched:
            return
        try:
            ec = int(sched.get("execution_count", 0))
        except (TypeError, ValueError):
            ec = 0
        if sched.get("is_active") and ec != 0:
            _register_schedule_job(schedule_id, sched["case_ids"], sched["cron_expr"])

    # 启动调度器并加载已有任务（仅在实际提供 HTTP 的进程启动，避免 Flask debug 重载父子双进程各启一调度器）
    _werkzeug_main = os.environ.get("WERKZEUG_RUN_MAIN")
    _start_apscheduler = _werkzeug_main == "true" or (_werkzeug_main is None and not app.debug)
    if _start_apscheduler:
        scheduler.start()
        _init_db = Database()
        for sched in _init_db.get_active_schedules():
            try:
                try:
                    ec = int(sched.get("execution_count", 0))
                except (TypeError, ValueError):
                    ec = 0
                if ec == 0:
                    continue
                _register_schedule_job(sched["id"], sched["case_ids"], sched["cron_expr"])
            except Exception as e:
                uat_logger.warning(f"加载定时任务 #{sched['id']} 失败: {e}")
        uat_logger.info("APScheduler 调度器已启动")
    else:
        uat_logger.info("APScheduler：当前进程为 Flask 重载监视进程，跳过启动，避免定时任务重复触发")

except ImportError:
    uat_logger.warning("APScheduler 未安装，定时执行功能不可用。运行: pip install APScheduler==3.10.4")
    scheduler = None
    def _register_schedule_job(*args, **kwargs):
        pass

    def _sync_schedule_job(*args, **kwargs):
        pass


# ==================== License 管理 API ====================

@app.route('/license')
@login_required
def license_page():
    """License 管理页面"""
    return render_template('license.html')


@app.route('/user-management')
@login_required
@role_required('admin')
def user_management_page():
    """用户管理页面（仅管理员）"""
    return render_template('user_management.html')


@app.route('/api/license/info', methods=['GET'])
@login_required
@api_error_handler
def api_get_user_license_info():
    """获取当前用户的 License 信息和使用情况"""
    try:
        # 使用缓存的 license 信息，提高性能
        # 只有在激活新 license 时才需要清除缓存
        license_info = license_manager.get_current_license()
        limits = license_manager.get_limits()
        
        # 获取今日使用统计
        _db = Database()
        today_stats = _db.get_user_usage_stats(current_user.id)
        
        return jsonify({
            'success': True,
            'info': {
                'license_type': license_info.license_type,
                'issued_to': license_info.issued_to,
                'issued_at': license_info.issued_at,
                'expires_at': license_info.expires_at,
                'features': license_info.features
            },
            'limits': limits,
            'usage': {
                'today_executions': today_stats.get('execution_count', 0) if today_stats else 0,
                'today_created_cases': today_stats.get('created_cases', 0) if today_stats else 0
            }
        })
    except Exception as e:
        uat_logger.log_exception('api_get_user_license_info', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/license/activate', methods=['POST'])
@login_required
def api_activate_license():
    """用户激活 License"""
    data = request.get_json(silent=True) or {}
    license_key = data.get('license_key', '').strip()
    
    if not license_key:
        return jsonify({'success': False, 'error': '请输入 License 密钥'}), 400
    
    # 验证 License
    result = license_manager.validate_license(license_key)
    if not result['valid']:
        return jsonify({'success': False, 'error': result['message']}), 400
    
    # 保存 License
    if license_manager.save_license(license_key):
        # 清除缓存，使新 license 立即生效
        license_manager._cached_license = None
        return jsonify({
            'success': True,
            'message': 'License 激活成功',
            'license_type': result['info'].license_type,
            'expires_at': result['info'].expires_at
        })
    else:
        return jsonify({'success': False, 'error': 'License 保存失败'}), 500


@app.route('/api/license', methods=['GET'])
@login_required
@role_required('admin')
def api_get_license_info():
    """获取当前 License 信息"""
    license_info = license_manager.get_current_license()
    limits = license_manager.get_limits()
    return jsonify({
        'success': True,
        'license': {
            'type': license_info.license_type,
            'issued_to': license_info.issued_to,
            'issued_at': license_info.issued_at,
            'expires_at': license_info.expires_at,
            'features': license_info.features,
            'limits': limits
        }
    })


@app.route('/api/license', methods=['POST'])
@login_required
@role_required('admin')
def api_upload_license():
    """上传并激活 License"""
    data = request.get_json(silent=True) or {}
    license_str = data.get('license', '').strip()

    if not license_str:
        return jsonify({'success': False, 'error': 'License 不能为空'}), 400

    # 验证 License
    result = license_manager.validate_license(license_str)
    if not result['valid']:
        return jsonify({'success': False, 'error': result['message']}), 400

    from deployment_config import is_client_mode, is_server_mode
    from instance_identity import get_instance_id, get_machine_id

    binding_type = ""
    binding_id = ""
    if is_server_mode():
        binding_type = "instance"
        binding_id = get_instance_id()
    elif is_client_mode():
        binding_type = "machine"
        binding_id = get_machine_id()

    result = license_manager.activate_license_key(license_str, binding_type, binding_id)
    if not result['valid']:
        return jsonify({'success': False, 'error': result['message']}), 400

    info = result.get('info')
    return jsonify({
        'success': True,
        'message': 'License 激活成功',
        'license': {
            'type': info.license_type if info else '',
            'expires_at': info.expires_at if info else '',
            'license_id': info.license_id if info else '',
        }
    })


# ==================== 审计日志 API ====================

def _collect_audit_export_rows(target_type=None, username=None, max_rows=50000):
    """分页拉取审计日志，用于导出（最多 max_rows 条）。"""
    _db = Database()
    rows_out = []
    page = 1
    page_size = 2000
    while len(rows_out) < max_rows:
        logs = _db.get_audit_logs(
            target_type=target_type,
            username=username,
            page=page,
            page_size=page_size,
        )
        if not logs:
            break
        for log in logs:
            details = log.get('details') or ''
            if details is not None and not isinstance(details, str):
                details = str(details)
            if isinstance(details, str) and len(details) > 8000:
                details = details[:8000] + '...'
            created = log.get('created_at') or ''
            rows_out.append([
                created,
                log.get('username') or '',
                log.get('action') or '',
                log.get('target_type') or '',
                log.get('target_id') if log.get('target_id') is not None else '',
                log.get('ip_address') or '',
                details,
            ])
            if len(rows_out) >= max_rows:
                return rows_out
        if len(logs) < page_size:
            break
        page += 1
    return rows_out


def _audit_logs_export_response(fmt: str):
    """生成审计日志导出响应，fmt 为 csv 或 xlsx。"""
    import io
    import csv
    from datetime import datetime

    fmt = (fmt or '').lower()
    target_type = (request.args.get('target_type') or '').strip() or None
    username = (request.args.get('username') or '').strip() or None

    header = ['时间', '用户', '操作', '目标类型', '目标ID', 'IP', '详情']
    data_rows = _collect_audit_export_rows(target_type=target_type, username=username)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    if fmt == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(header)
        writer.writerows(data_rows)
        filename = f'audit_logs_{ts}.csv'
        payload = output.getvalue().encode('utf-8-sig')
        return Response(
            payload,
            mimetype='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Cache-Control': 'no-store',
            },
        )

    if fmt in ('xlsx', 'excel'):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
            from openpyxl.utils import get_column_letter
        except ImportError:
            return jsonify({'success': False, 'error': '请先安装 openpyxl: pip install openpyxl'}), 500

        wb = Workbook()
        ws = wb.active
        ws.title = 'audit_logs'
        ws.append(header)
        for row in data_rows:
            ws.append(row)
        bold = Font(bold=True)
        for c in range(1, len(header) + 1):
            ws.cell(row=1, column=c).font = bold
        ws.freeze_panes = 'A2'
        widths = (22, 14, 28, 14, 12, 18, 72)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        filename = f'audit_logs_{ts}.xlsx'
        return Response(
            bio.getvalue(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Cache-Control': 'no-store',
            },
        )

    return jsonify({'success': False, 'error': '不支持的导出格式'}), 400


@app.route('/api/audit-logs', methods=['GET'])
@login_required
@role_required('admin')
def api_get_audit_logs():
    """获取审计日志（仅管理员）。指定 format=csv 或 format=xlsx 时导出文件（与列表筛选一致）。"""
    fmt = (request.args.get('format') or '').strip().lower()
    if fmt in ('csv', 'xlsx', 'excel'):
        if fmt == 'excel':
            fmt = 'xlsx'
        return _audit_logs_export_response(fmt)

    page = max(1, request.args.get('page', 1, type=int) or 1)
    page_size = request.args.get('page_size', 50, type=int) or 50
    page_size = min(200, max(1, page_size))
    target_type = (request.args.get('target_type') or '').strip() or None
    username = (request.args.get('username') or '').strip() or None

    _db = Database()
    logs = _db.get_audit_logs(
        target_type=target_type,
        username=username,
        page=page,
        page_size=page_size,
    )
    total = _db.get_audit_logs_count(target_type=target_type, username=username)

    return jsonify({
        'success': True,
        'logs': logs,
        'total': total,
        'page': page,
        'page_size': page_size
    })


@app.route('/api/audit-logs/export', methods=['GET'])
@login_required
@role_required('admin')
def api_export_audit_logs():
    """兼容旧版前端路径：等同于 format=csv。"""
    return _audit_logs_export_response('csv')


# ==================== 项目成员管理 API ====================

@app.route('/api/projects/<int:project_id>/members', methods=['GET'])
@login_required
@project_access_required(min_role='viewer')
def api_get_project_members(project_id):
    """获取项目成员列表"""
    _db = Database()
    members = _db.get_project_members(project_id)
    return jsonify({'success': True, 'members': members})


@app.route('/api/projects/<int:project_id>/members', methods=['POST'])
@login_required
@project_access_required(min_role='owner')
@audit_log('ADD_PROJECT_MEMBER', 'project_member')
def api_add_project_member(project_id):
    """添加项目成员"""
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    role = data.get('role', 'editor')

    if not username:
        return jsonify({'success': False, 'error': '用户名不能为空'}), 400

    if role not in ('viewer', 'editor', 'owner'):
        return jsonify({'success': False, 'error': '无效的角色'}), 400

    _db = Database()
    user = _db.get_user_by_username(username)
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'}), 404

    success = _db.add_project_member(project_id, user['id'], role)
    if success:
        return jsonify({'success': True, 'message': '成员添加成功'})
    else:
        return jsonify({'success': False, 'error': '成员已存在'}), 409


@app.route('/api/projects/<int:project_id>/members/<int:user_id>', methods=['PUT'])
@login_required
@project_access_required(min_role='owner')
@audit_log('UPDATE_PROJECT_MEMBER', 'project_member')
def api_update_project_member(project_id, user_id):
    """更新项目成员角色"""
    data = request.get_json(silent=True) or {}
    role = data.get('role')

    if role not in ('viewer', 'editor', 'owner'):
        return jsonify({'success': False, 'error': '无效的角色'}), 400

    _db = Database()
    success = _db.update_project_member_role(project_id, user_id, role)
    return jsonify({'success': success})


@app.route('/api/projects/<int:project_id>/members/<int:user_id>', methods=['DELETE'])
@login_required
@project_access_required(min_role='owner')
@audit_log('REMOVE_PROJECT_MEMBER', 'project_member')
def api_remove_project_member(project_id, user_id):
    """移除项目成员"""
    if user_id == current_user.id:
        return jsonify({'success': False, 'error': '不能移除自己'}), 400

    _db = Database()
    success = _db.remove_project_member(project_id, user_id)
    return jsonify({'success': success})


# ==================== 健康检查 API ====================

@app.route('/api/health', methods=['GET'])
def api_health():
    """存活探针：仅表示进程可响应（不访问数据库，便于与就绪探针区分）。"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.datetime.now().isoformat(),
        'version': '2.0.0',
    })


@app.route('/api/health/ready', methods=['GET'])
def api_health_ready():
    """就绪探针：校验 SQLite 可连接（编排/负载均衡建议用此 URL）。"""
    payload = {
        'timestamp': datetime.datetime.now().isoformat(),
    }
    if sys.platform == 'win32':
        try:
            from desktop_runtime import desktop_runtime_available, desktop_runtime_unavailable_reason

            payload['desktop_runtime'] = {
                'available': bool(desktop_runtime_available()),
                'reason': desktop_runtime_unavailable_reason() or None,
                'python_executable': sys.executable,
            }
        except Exception as exc:
            payload['desktop_runtime'] = {
                'available': False,
                'reason': str(exc),
                'python_executable': sys.executable,
            }
    if not db.ping():
        payload['status'] = 'unready'
        payload['database'] = 'unavailable'
        return jsonify(payload), 503
    payload['status'] = 'ready'
    payload['database'] = 'ok'
    return jsonify(payload)


# ==================== 通知配置 API ====================

@app.route('/api/notifications/configs', methods=['GET'])
@login_required
@role_required('admin')
def api_get_notification_configs():
    """获取通知配置列表"""
    db = Database()
    configs = db.get_all_notification_configs()
    return jsonify({'success': True, 'configs': configs})


@app.route('/api/notifications/configs', methods=['POST'])
@login_required
@role_required('admin')
@audit_log('CREATE_NOTIFICATION_CONFIG', 'notification')
def api_create_notification_config():
    """创建通知配置"""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    config_type = data.get('type', '').strip()
    config_data = data.get('config', {})
    events = data.get('events', [])
    is_active = 1 if data.get('is_active', True) else 0

    if not name or not config_type:
        return jsonify({'success': False, 'error': '名称和类型不能为空'}), 400

    db = Database()
    config_id = db.create_notification_config(name, config_type, config_data, events, is_active)

    # 重新加载通知管理器配置
    from notification_manager import notification_manager
    notification_manager.reload_configs()

    return jsonify({'success': True, 'message': '配置创建成功', 'id': config_id})


@app.route('/api/notifications/configs/<int:config_id>', methods=['PUT'])
@login_required
@role_required('admin')
@audit_log('UPDATE_NOTIFICATION_CONFIG', 'notification')
def api_update_notification_config(config_id):
    """更新通知配置"""
    data = request.get_json(silent=True) or {}

    updates = {}
    if 'name' in data:
        updates['name'] = data['name'].strip()
    if 'type' in data:
        updates['type'] = data['type'].strip()
    if 'config' in data:
        updates['config'] = data['config']
    if 'events' in data:
        updates['events'] = data['events']
    if 'is_active' in data:
        updates['is_active'] = 1 if data['is_active'] else 0

    if not updates:
        return jsonify({'success': False, 'error': '没有要更新的字段'}), 400

    db = Database()
    success = db.update_notification_config(config_id, **updates)

    if success:
        # 重新加载通知管理器配置
        from notification_manager import notification_manager
        notification_manager.reload_configs()
        return jsonify({'success': True, 'message': '配置更新成功'})
    else:
        return jsonify({'success': False, 'error': '配置不存在'}), 404


@app.route('/api/notifications/configs/<int:config_id>', methods=['DELETE'])
@login_required
@role_required('admin')
@audit_log('DELETE_NOTIFICATION_CONFIG', 'notification')
def api_delete_notification_config(config_id):
    """删除通知配置"""
    db = Database()
    success = db.delete_notification_config(config_id)

    if success:
        # 重新加载通知管理器配置
        from notification_manager import notification_manager
        notification_manager.reload_configs()
        return jsonify({'success': True, 'message': '配置删除成功'})
    else:
        return jsonify({'success': False, 'error': '配置不存在'}), 404


@app.route('/api/notifications/test', methods=['POST'])
@login_required
@role_required('admin')
def api_test_notification():
    """测试通知"""
    from notification_manager import notification_manager, NotificationConfig

    data = request.get_json(silent=True) or {}
    config_data = data.get('config', {})
    config_type = config_data.get('type', 'webhook')

    # 根据类型构建配置
    if config_type == 'email':
        config = NotificationConfig(
            name='test',
            type='email',
            webhook_url='',  # email 不需要 webhook_url
            secret=None,
            enabled=True
        )
        # 邮件配置存储在 template 字段
        config.template = json.dumps({
            'smtp_server': config_data.get('smtp_server', ''),
            'smtp_port': config_data.get('smtp_port', 587),
            'username': config_data.get('username', ''),
            'password': config_data.get('password', ''),
            'to_emails': config_data.get('to_emails', [])
        })
    else:
        config = NotificationConfig(
            name='test',
            type=config_type,
            webhook_url=config_data.get('webhook_url', ''),
            secret=config_data.get('secret') or None,
            enabled=True
        )

    success = notification_manager.send_notification(
        config,
        title="测试通知",
        content="这是一条测试通知消息",
        data={'test': True}
    )

    return jsonify({
        'success': success,
        'message': '发送成功' if success else '发送失败'
    })


# ==================== 浏览器支持 API ====================

@app.route('/api/browsers', methods=['GET'])
@login_required
def api_get_browsers():
    """获取支持的浏览器列表"""
    from browser_manager import BrowserManager
    browsers = BrowserManager.get_available_browsers()
    devices = BrowserManager.get_device_presets()
    return jsonify({
        'success': True,
        'browsers': browsers,
        'device_presets': devices
    })


# ==================== 断言类型 API ====================

@app.route('/api/assertion-types', methods=['GET'])
@login_required
def api_get_assertion_types():
    """获取支持的断言类型"""
    types = [
        {'id': 'text_equals', 'name': '文本相等', 'category': 'text'},
        {'id': 'text_contains', 'name': '文本包含', 'category': 'text'},
        {'id': 'text_regex', 'name': '文本正则匹配', 'category': 'text'},
        {'id': 'element_exists', 'name': '元素存在', 'category': 'element'},
        {'id': 'element_visible', 'name': '元素可见', 'category': 'element'},
        {'id': 'element_attr', 'name': '元素属性', 'category': 'element'},
        {'id': 'element_css', 'name': '元素CSS样式', 'category': 'element'},
        {'id': 'element_count', 'name': '元素数量', 'category': 'element'},
        {'id': 'url_equals', 'name': 'URL相等', 'category': 'url'},
        {'id': 'url_contains', 'name': 'URL包含', 'category': 'url'},
        {'id': 'api_status', 'name': 'API状态码', 'category': 'api'},
        {'id': 'api_json', 'name': 'API JSON响应', 'category': 'api'},
        {'id': 'javascript', 'name': 'JavaScript执行', 'category': 'script'}
    ]
    return jsonify({'success': True, 'types': types})


# ==================== SSO 单点登录 API ====================

@app.route('/sso-settings')
@login_required
@role_required('admin')
def sso_settings_page():
    """返回 SSO 设置页面"""
    return render_template('sso_settings.html')


@app.route('/api/sso/configs', methods=['GET'])
@login_required
@role_required('admin')
def api_get_sso_configs():
    """获取 SSO 配置列表"""
    try:
        from sso_manager import sso_manager
        configs = sso_manager.get_sso_configs()
        return jsonify({'success': True, 'configs': configs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sso/configs', methods=['POST'])
@login_required
@role_required('admin')
@audit_log('CREATE_SSO_CONFIG', 'sso_config')
def api_create_sso_config():
    """创建 SSO 配置"""
    try:
        from sso_manager import sso_manager
        data = request.get_json(silent=True) or {}
        
        if not data.get('provider_type'):
            return jsonify({'success': False, 'error': '请选择 SSO 类型'}), 400
        if not data.get('name'):
            return jsonify({'success': False, 'error': '请输入配置名称'}), 400
        
        config_id = sso_manager.create_sso_config(data)
        return jsonify({'success': True, 'config_id': config_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sso/configs/<int:config_id>', methods=['GET'])
@login_required
@role_required('admin')
def api_get_sso_config_detail(config_id):
    """获取单个 SSO 配置详情（用于编辑）"""
    try:
        from sso_manager import sso_manager
        config = sso_manager.get_sso_config(config_id)
        if not config:
            return jsonify({'success': False, 'error': 'SSO 配置不存在'}), 404
        return jsonify({'success': True, 'config': config.__dict__})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sso/configs/<int:config_id>', methods=['PUT'])
@login_required
@role_required('admin')
@audit_log('UPDATE_SSO_CONFIG', 'sso_config')
def api_update_sso_config(config_id):
    """更新 SSO 配置"""
    try:
        from sso_manager import sso_manager
        data = request.get_json(silent=True) or {}
        success = sso_manager.update_sso_config(config_id, data)
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sso/configs/<int:config_id>', methods=['DELETE'])
@login_required
@role_required('admin')
@audit_log('DELETE_SSO_CONFIG', 'sso_config')
def api_delete_sso_config(config_id):
    """删除 SSO 配置"""
    try:
        from sso_manager import sso_manager
        success = sso_manager.delete_sso_config(config_id)
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sso/login/<int:config_id>', methods=['GET'])
def api_sso_login_url(config_id):
    """获取 SSO 登录 URL"""
    try:
        from sso_manager import sso_manager, SSOProviderType
        config = sso_manager.get_sso_config(config_id)
        
        if not config:
            return jsonify({'success': False, 'error': 'SSO 配置不存在'}), 404
        
        redirect_uri = request.host_url.rstrip('/') + f'/api/sso/callback/{config_id}'
        
        if config.provider_type == SSOProviderType.WECOM.value:
            login_url = sso_manager.wecom_get_login_url(config, redirect_uri)
        elif config.provider_type == SSOProviderType.OAUTH2.value:
            login_url = sso_manager.oauth2_get_login_url(config)
            if not login_url:
                return jsonify({'success': False, 'error': 'OAuth2 授权地址或回调地址未配置完整'}), 400
        else:
            return jsonify({'success': False, 'error': '该 SSO 类型不支持跳转登录'}), 400
        
        return jsonify({'success': True, 'login_url': login_url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sso/callback/<int:config_id>', methods=['GET'])
def api_sso_callback(config_id):
    """处理 SSO 登录回调"""
    try:
        from urllib.parse import quote

        from sso_manager import sso_manager, SSOProviderType

        def _err_redirect(msg: str):
            return redirect('/login?error=' + quote(msg, safe=''))

        config = sso_manager.get_sso_config(config_id)
        if not config:
            return _err_redirect('SSO配置不存在')

        code = request.args.get('code')
        state = request.args.get('state')

        # 企业微信 / OAuth2：校验 state，防止 CSRF 与伪造回调
        if config.provider_type in (SSOProviderType.WECOM.value, SSOProviderType.OAUTH2.value):
            if not state:
                return _err_redirect('缺少安全参数state请重新登录')
            verified_id = sso_manager.verify_state(state)
            if verified_id != config_id:
                uat_logger.warning(
                    'SSO state 校验失败: path_config_id=%s verified_id=%s', config_id, verified_id
                )
                return _err_redirect('登录状态已失效请重新登录')

        if not code:
            return _err_redirect('授权失败未返回code')

        success, user_info, message = sso_manager.authenticate(config_id, code=code)

        if not success:
            uat_logger.error(f"SSO 登录失败: {message}")
            return _err_redirect(message or 'SSO登录失败')

        # 获取或创建用户
        user_id = sso_manager.get_or_create_user(
            config.provider_type,
            user_info,
            tenant_id=config.tenant_id
        )
        
        # 登录用户
        _db = Database()
        user_data = _db.get_user_by_id(user_id)
        if user_data:
            user = UserModel(user_data)
            login_user(user, remember=True)
            _db.update_user_last_login(user_id)
            uat_logger.info(f"SSO 用户 {user_info.get('username')} 登录成功")
            return redirect('/')
        
        return redirect('/login?error=user_not_found')
    except Exception as e:
        uat_logger.error(f"SSO 回调处理失败: {e}")
        return redirect('/login?error=sso_error')


@app.route('/api/sso/ldap-login', methods=['POST'])
def api_sso_ldap_login():
    """处理 LDAP 登录"""
    try:
        from sso_manager import sso_manager
        data = request.get_json(silent=True) or {}
        config_id = data.get('config_id')
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not config_id:
            return jsonify({'success': False, 'error': '请选择 LDAP 配置'}), 400
        if not username or not password:
            return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400
        
        success, user_info, message = sso_manager.authenticate(
            config_id, username=username, password=password
        )
        
        if not success:
            return jsonify({'success': False, 'error': message}), 401
        
        # 获取或创建用户
        config = sso_manager.get_sso_config(config_id)
        user_id = sso_manager.get_or_create_user(
            config.provider_type,
            user_info,
            tenant_id=config.tenant_id
        )
        
        # 登录用户
        _db = Database()
        user_data = _db.get_user_by_id(user_id)
        if user_data:
            user = UserModel(user_data)
            login_user(user, remember=True)
            _db.update_user_last_login(user_id)
            uat_logger.info(f"LDAP 用户 {username} 登录成功")
            return jsonify({'success': True, 'user': {'id': user_id, 'username': user_data['username']}})
        
        return jsonify({'success': False, 'error': '用户创建失败'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 支付集成 API ====================

@app.route('/pricing')
def pricing_page():
    """返回定价页面"""
    blocked = guard_billing_route()
    if blocked:
        return blocked
    return render_template('pricing.html')


@app.route('/api/payment/plans', methods=['GET'])
def api_get_payment_plans():
    """获取套餐列表"""
    try:
        from payment_manager import payment_manager
        plans = payment_manager.get_plan_list()
        return jsonify({'success': True, 'plans': plans})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/payment/orders', methods=['POST'])
@login_required
@audit_log('CREATE_ORDER', 'order')
def api_create_order():
    """创建订单"""
    try:
        from payment_manager import payment_manager
        data = request.get_json(silent=True) or {}
        
        plan_type = data.get('plan_type')
        period = data.get('period', 'monthly')
        quantity = data.get('quantity', 1)
        
        if not plan_type:
            return jsonify({'success': False, 'error': '请选择套餐类型'}), 400
        
        order = payment_manager.create_order(
            user_id=current_user.id,
            plan_type=plan_type,
            period=period,
            quantity=quantity
        )
        
        return jsonify({'success': True, 'order': order})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/payment/orders', methods=['GET'])
@login_required
def api_get_user_orders():
    """获取用户订单列表"""
    try:
        from payment_manager import payment_manager
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        
        orders, total = payment_manager.get_user_orders(current_user.id, page, page_size)
        
        return jsonify({
            'success': True,
            'orders': orders,
            'total': total,
            'page': page,
            'page_size': page_size
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/payment/orders/<order_no>', methods=['GET'])
@login_required
def api_get_order_detail(order_no):
    """获取订单详情"""
    try:
        from payment_manager import payment_manager
        order = payment_manager.get_order(order_no=order_no)
        
        if not order:
            return jsonify({'success': False, 'error': '订单不存在'}), 404
        
        if order['user_id'] != current_user.id and current_user.role != 'admin':
            return jsonify({'success': False, 'error': '无权限查看此订单'}), 403
        
        # 已支付订单返回自动生成的 License Key（按订单唯一）
        license_key = None
        if order.get('status') == 'paid':
            license_key = payment_manager.get_or_create_order_license(order_no)
        return jsonify({'success': True, 'order': order, 'license_key': license_key})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/payment/orders/<order_no>/cancel', methods=['POST'])
@login_required
def api_cancel_order(order_no):
    """取消订单"""
    try:
        from payment_manager import payment_manager
        ok, err = payment_manager.cancel_order(order_no, current_user.id)
        if not ok:
            return jsonify({'success': False, 'error': err}), 400
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/payment/pay/<order_no>', methods=['POST'])
@login_required
def api_create_payment(order_no):
    """创建支付"""
    try:
        from payment_manager import payment_manager, PaymentMethod
        data = request.get_json(silent=True) or {}
        payment_method = data.get('payment_method', 'alipay')
        
        order = payment_manager.get_order(order_no=order_no)
        if not order:
            return jsonify({'success': False, 'error': '订单不存在'}), 404
        
        if order['user_id'] != current_user.id:
            return jsonify({'success': False, 'error': '无权限支付此订单'}), 403
        
        if payment_method == PaymentMethod.ALIPAY.value:
            result = payment_manager.create_alipay_payment(order_no)
        elif payment_method == PaymentMethod.WECHAT.value:
            result = payment_manager.create_wechat_payment(order_no)
        else:
            return jsonify({'success': False, 'error': '不支持的支付方式'}), 400
        
        return jsonify(result)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/payment/callback/alipay', methods=['POST'])
def api_alipay_callback():
    """支付宝支付回调"""
    try:
        from payment_manager import payment_manager
        params = request.form.to_dict()
        
        success, order_no = payment_manager.verify_alipay_callback(params)
        
        if success:
            uat_logger.info(f"支付宝支付成功: 订单 {order_no}")
            return 'success'
        else:
            uat_logger.warning(f"支付宝支付回调失败: 订单 {order_no}")
            return 'fail'
    except Exception as e:
        uat_logger.error(f"支付宝回调处理异常: {e}")
        return 'fail'


@app.route('/api/payment/callback/wechat', methods=['POST'])
def api_wechat_callback():
    """微信支付回调"""
    try:
        from payment_manager import payment_manager
        xml_data = request.data.decode('utf-8')
        
        success, order_no = payment_manager.verify_wechat_callback(xml_data)
        
        if success:
            uat_logger.info(f"微信支付成功: 订单 {order_no}")
            return '<xml><return_code><![CDATA[SUCCESS]]></return_code></xml>'
        else:
            uat_logger.warning(f"微信支付回调失败")
            return '<xml><return_code><![CDATA[FAIL]]></return_code></xml>'
    except Exception as e:
        uat_logger.error(f"微信回调处理异常: {e}")
        return '<xml><return_code><![CDATA[FAIL]]></return_code></xml>'


@app.route('/api/payment/subscription', methods=['GET'])
@login_required
def api_get_subscription():
    """获取当前用户订阅信息"""
    try:
        from payment_manager import payment_manager
        subscription = payment_manager.get_user_subscription(current_user.id)
        return jsonify({'success': True, 'subscription': subscription})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/payment/mock/complete/<order_no>', methods=['POST'])
@login_required
def api_mock_complete_payment(order_no):
    """模拟支付成功（开发/演示使用）"""
    try:
        from payment_manager import payment_manager, PaymentStatus, PaymentMethod
        order = payment_manager.get_order(order_no=order_no)
        if not order:
            return jsonify({'success': False, 'error': '订单不存在'}), 404
        if order['user_id'] != current_user.id and current_user.role != 'admin':
            return jsonify({'success': False, 'error': '无权限操作此订单'}), 403
        if order['status'] == PaymentStatus.PAID.value:
            return jsonify({'success': True, 'message': '订单已支付'})

        txid = f"MOCK-{int(time.time())}-{current_user.id}"
        ok = payment_manager.update_order_status(
            order_no=order_no,
            status=PaymentStatus.PAID.value,
            payment_method=PaymentMethod.MANUAL.value,
            transaction_id=txid
        )
        return jsonify({'success': bool(ok), 'transaction_id': txid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/payment/<order_no>')
@login_required
def payment_page(order_no):
    blocked = guard_billing_route()
    if blocked:
        return blocked
    """支付页面"""
    return render_template('payment.html', order_no=order_no)


@app.route('/payment/orders')
@login_required
def payment_orders_page():
    """订单列表页面"""
    blocked = guard_billing_route()
    if blocked:
        return blocked
    return render_template('payment_orders.html')


# ==================== 审计日志页面 ====================

@app.route('/audit-logs')
@login_required
@role_required('admin')
def audit_logs_page():
    """审计日志页面"""
    return render_template('audit_logs.html')


# ==================== 缺陷管理 API ====================

@app.route('/defects')
@login_required
@feature_required('defect_management')
def defects_page():
    """缺陷管理页面"""
    return render_template('defects.html')

@app.route('/defects/<int:defect_id>')
@login_required
def defect_detail_page(defect_id):
    """缺陷详情页面"""
    return render_template('defect_detail.html', defect_id=defect_id)

@app.route('/api/defects', methods=['GET'])
@login_required
@feature_required('defect_management')
@api_error_handler
@log_api_request
def api_get_defects():
    """获取缺陷列表"""
    project_id = request.args.get('project_id', type=int)
    status = request.args.get('status')
    assignee_id = request.args.get('assignee_id', type=int)
    severity = request.args.get('severity')
    case_id = request.args.get('case_id', type=int)
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 20, type=int)
    
    _db = Database()
    result = _db.get_defects(
        project_id=project_id, status=status, assignee_id=assignee_id,
        severity=severity, case_id=case_id, page=page, page_size=page_size
    )
    return jsonify({'success': True, **result})

@app.route('/api/defects', methods=['POST'])
@login_required
@feature_required('defect_management')
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('CREATE_DEFECT', 'defect')
def api_create_defect():
    """创建缺陷"""
    data = request.get_json(silent=True) or {}
    
    project_id = data.get('project_id')
    title = data.get('title', '').strip()
    
    if not project_id:
        return jsonify({'success': False, 'error': '项目ID不能为空'}), 400
    if not title:
        return jsonify({'success': False, 'error': '缺陷标题不能为空'}), 400
    
    _db = Database()
    defect_id = _db.create_defect(
        project_id=project_id,
        title=title,
        reporter_id=current_user.id,
        description=data.get('description', ''),
        severity=data.get('severity', 'medium'),
        priority=data.get('priority', 'medium'),
        assignee_id=data.get('assignee_id'),
        case_id=data.get('case_id'),
        run_history_id=data.get('run_history_id'),
        step_result_id=data.get('step_result_id'),
        error_message=data.get('error_message', ''),
        screenshots=data.get('screenshots', ''),
        environment=data.get('environment', ''),
        browser_info=data.get('browser_info', ''),
        reproduce_steps=data.get('reproduce_steps', ''),
        expected_result=data.get('expected_result', ''),
        actual_result=data.get('actual_result', ''),
        status=data.get('status', 'open')
    )
    
    return jsonify({'success': True, 'defect_id': defect_id})

@app.route('/api/defects/batch_from_cases', methods=['POST'])
@login_required
@feature_required('defect_management')
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_batch_create_defects_from_cases():
    """从选中的测试用例批量创建缺陷"""
    data = request.get_json(silent=True) or {}
    
    case_ids = data.get('case_ids', [])
    project_id = data.get('project_id')
    
    if not case_ids or not isinstance(case_ids, list):
        return jsonify({'success': False, 'error': '请选择至少一个测试用例'}), 400
    if not project_id:
        return jsonify({'success': False, 'error': '项目ID不能为空'}), 400
    
    title_template = data.get('title_template', '').strip()
    description = data.get('description', '').strip()
    severity = data.get('severity', 'medium')
    priority = data.get('priority', 'medium')
    assignee_id = data.get('assignee_id')
    environment = data.get('environment', '').strip()
    expected_result = data.get('expected_result', '').strip()
    actual_result = data.get('actual_result', '').strip()
    
    valid_severities = ['critical', 'high', 'medium', 'low']
    if severity not in valid_severities:
        severity = 'medium'
    valid_priorities = ['urgent', 'high', 'medium', 'low']
    if priority not in valid_priorities:
        priority = 'medium'
    
    _db = Database()
    created_ids = _db.batch_create_defects_from_cases(
        case_ids=case_ids,
        project_id=project_id,
        reporter_id=current_user.id,
        title_template=title_template,
        description=description,
        severity=severity,
        priority=priority,
        assignee_id=assignee_id,
        environment=environment,
        expected_result=expected_result,
        actual_result=actual_result
    )
    
    return jsonify({
        'success': True,
        'created_count': len(created_ids),
        'defect_ids': created_ids,
        'message': f'成功创建 {len(created_ids)} 个缺陷记录'
    })

@app.route('/api/defects/<int:defect_id>', methods=['GET'])
@login_required
@feature_required('defect_management')
@api_error_handler
@log_api_request
def api_get_defect(defect_id):
    """获取缺陷详情"""
    _db = Database()
    defect = _db.get_defect(defect_id)
    if not defect:
        return jsonify({'success': False, 'error': '缺陷不存在'}), 404
    return jsonify({'success': True, 'defect': defect})

@app.route('/api/defects/<int:defect_id>', methods=['PUT'])
@login_required
@feature_required('defect_management')
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('UPDATE_DEFECT', 'defect')
def api_update_defect(defect_id):
    """更新缺陷"""
    data = request.get_json(silent=True) or {}
    
    _db = Database()
    success = _db.update_defect(defect_id, current_user.id, **data)
    
    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': '更新失败'}), 400

@app.route('/api/defects/<int:defect_id>', methods=['DELETE'])
@login_required
@feature_required('defect_management')
@role_required('admin', 'project_manager')
@api_error_handler
@log_api_request
@audit_log('DELETE_DEFECT', 'defect')
def api_delete_defect(defect_id):
    """删除缺陷"""
    _db = Database()
    _db.delete_defect(defect_id)
    return jsonify({'success': True})

@app.route('/api/defects/<int:defect_id>/status', methods=['PUT'])
@login_required
@feature_required('defect_management')
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('UPDATE_DEFECT_STATUS', 'defect')
def api_update_defect_status(defect_id):
    """更新缺陷状态（状态流转）"""
    data = request.get_json(silent=True) or {}
    new_status = data.get('status')
    
    if not new_status:
        return jsonify({'success': False, 'error': '状态不能为空'}), 400
    
    valid_statuses = ['open', 'in_progress', 'resolved', 'closed', 'reopened']
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'error': f'无效状态，允许的状态: {valid_statuses}'}), 400
    
    _db = Database()
    
    # 先获取当前缺陷状态
    current_defect = _db.get_defect(defect_id)
    if not current_defect:
        return jsonify({'success': False, 'error': '缺陷不存在'}), 404
    
    success = _db.update_defect_status(defect_id, current_user.id, new_status)
    
    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': '状态更新失败，可能是状态未发生变化'}), 400

@app.route('/api/defects/<int:defect_id>/comments', methods=['GET'])
@login_required
@feature_required('defect_management')
@api_error_handler
@log_api_request
def api_get_defect_comments(defect_id):
    """获取缺陷评论"""
    _db = Database()
    comments = _db.get_defect_comments(defect_id)
    return jsonify({'success': True, 'comments': comments})

@app.route('/api/defects/<int:defect_id>/comments', methods=['POST'])
@login_required
@feature_required('defect_management')
@api_error_handler
@log_api_request
def api_add_defect_comment(defect_id):
    """添加缺陷评论"""
    data = request.get_json(silent=True) or {}
    content = data.get('content', '').strip()
    
    if not content:
        return jsonify({'success': False, 'error': '评论内容不能为空'}), 400
    
    _db = Database()
    comment_id = _db.add_defect_comment(defect_id, current_user.id, content)
    return jsonify({'success': True, 'comment_id': comment_id})

@app.route('/api/defects/<int:defect_id>/history', methods=['GET'])
@login_required
@feature_required('defect_management')
@api_error_handler
@log_api_request
def api_get_defect_history(defect_id):
    """获取缺陷状态变更历史"""
    _db = Database()
    history = _db.get_defect_history(defect_id)
    return jsonify({'success': True, 'history': history})

@app.route('/api/defects/from-failure', methods=['POST'])
@login_required
@feature_required('defect_management')
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('CREATE_DEFECT_FROM_FAILURE', 'defect')
def api_create_defect_from_failure():
    """从失败用例一键创建缺陷"""
    data = request.get_json(silent=True) or {}
    run_history_id = data.get('run_history_id')
    
    if not run_history_id:
        return jsonify({'success': False, 'error': '执行历史ID不能为空'}), 400
    
    _db = Database()
    defect_id = _db.create_defect_from_failure(
        run_history_id=run_history_id,
        reporter_id=current_user.id,
        title=data.get('title'),
        assignee_id=data.get('assignee_id')
    )
    
    if defect_id:
        return jsonify({'success': True, 'defect_id': defect_id})
    return jsonify({'success': False, 'error': '创建缺陷失败，可能执行历史不存在'}), 400

@app.route('/api/defects/statistics', methods=['GET'])
@login_required
@feature_required('defect_management')
@api_error_handler
@log_api_request
def api_get_defect_statistics():
    """获取缺陷统计数据"""
    project_id = request.args.get('project_id', type=int)
    _db = Database()
    stats = _db.get_defect_statistics(project_id)
    return jsonify({'success': True, 'statistics': stats})


# ==================== 用例导入导出 API ====================

@app.route('/api/cases/export/excel', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_export_cases_excel():
    """导出用例到Excel"""
    try:
        from case_importer import case_importer
        
        data = request.get_json(silent=True) or {}
        project_id = data.get('project_id')
        case_ids = data.get('case_ids', [])
        
        if not project_id and not case_ids:
            return jsonify({'success': False, 'error': '请指定项目ID或用例ID列表'}), 400
        
        filepath = case_importer.export_cases_to_excel(
            project_id=project_id,
            case_ids=case_ids if case_ids else None
        )
        
        return jsonify({
            'success': True,
            'filepath': filepath,
            'download_url': f'/api/download/{os.path.basename(filepath)}'
        })
    except ImportError as e:
        return jsonify({'success': False, 'error': '请先安装 openpyxl: pip install openpyxl'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cases/export/json', methods=['POST'])
@login_required
@api_error_handler
@log_api_request
def api_export_cases_json():
    """导出用例到JSON"""
    try:
        from case_importer import case_importer
        
        data = request.get_json(silent=True) or {}
        project_id = data.get('project_id')
        case_ids = data.get('case_ids', [])
        
        if not project_id and not case_ids:
            return jsonify({'success': False, 'error': '请指定项目ID或用例ID列表'}), 400
        
        filepath = case_importer.export_cases_to_json(
            project_id=project_id,
            case_ids=case_ids if case_ids else None
        )
        
        return jsonify({
            'success': True,
            'filepath': filepath,
            'download_url': f'/api/download/{os.path.basename(filepath)}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cases/import/excel', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('IMPORT_CASES', 'case')
def api_import_cases_excel():
    """从Excel导入用例"""
    try:
        from case_importer import case_importer
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '请上传文件'}), 400
        
        file = request.files['file']
        if not file.filename.endswith(('.xlsx', '.xls')):
            return jsonify({'success': False, 'error': '请上传Excel文件(.xlsx或.xls)'}), 400
        
        project_id = request.form.get('project_id', type=int)
        
        # 保存上传的文件（Windows上需要先关闭临时文件再写入）
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.xlsx')
        os.close(tmp_fd)
        file.save(tmp_path)
        
        try:
            results = case_importer.import_cases_from_excel(tmp_path, project_id)
            return jsonify({
                'success': True,
                'cases_created': results['cases_created'],
                'steps_created': results['steps_created'],
                'errors': results['errors']
            })
        finally:
            # 尝试删除临时文件，失败不影响返回结果
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            
    except ImportError as e:
        return jsonify({'success': False, 'error': '请先安装 openpyxl: pip install openpyxl'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cases/import/json', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
@audit_log('IMPORT_CASES', 'case')
def api_import_cases_json():
    """从JSON导入用例"""
    try:
        from case_importer import case_importer
        
        data = request.get_json(silent=True) or {}
        project_id = data.get('project_id')
        json_data = data.get('data')
        
        if not project_id:
            return jsonify({'success': False, 'error': '请指定项目ID'}), 400
        if not json_data:
            return jsonify({'success': False, 'error': '请提供JSON数据'}), 400
        
        results = case_importer.import_cases_from_json(
            json_data=json.dumps(json_data) if isinstance(json_data, dict) else json_data,
            project_id=project_id
        )
        
        return jsonify({
            'success': True,
            'cases_created': results['cases_created'],
            'steps_created': results['steps_created'],
            'errors': results['errors']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cases/template/excel', methods=['GET'])
@login_required
def api_get_excel_template():
    """获取Excel导入模板"""
    try:
        from case_importer import case_importer
        filepath = case_importer.generate_excel_template()
        return jsonify({
            'success': True,
            'download_url': f'/api/download/{os.path.basename(filepath)}'
        })
    except ImportError:
        return jsonify({'success': False, 'error': '请先安装 openpyxl'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cases/template/json', methods=['GET'])
@login_required
def api_get_json_template():
    """获取JSON导入模板"""
    try:
        from case_importer import case_importer
        template = case_importer.generate_json_template()
        return jsonify({
            'success': True,
            'template': json.loads(template)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/download/<filename>')
@login_required
def api_download_file(filename):
    """下载导出文件（限制在 exports 目录内，防止路径穿越）"""
    from flask import abort, send_from_directory
    from werkzeug.utils import secure_filename

    safe_name = secure_filename(os.path.basename(filename))
    if not safe_name:
        abort(404)
    export_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'exports'))
    candidate = os.path.abspath(os.path.join(export_dir, safe_name))
    try:
        if os.path.commonpath([export_dir, candidate]) != export_dir:
            abort(404)
    except ValueError:
        abort(404)
    if not os.path.isfile(candidate):
        abort(404)
    return send_from_directory(export_dir, safe_name, as_attachment=True)


def _run_case_worker_sync(case_id: int, user_id: int) -> dict:
    """Worker / 内部 API 同步执行用例（跳过 server 入队）。"""
    with app.test_request_context(
        f"/api/cases/{case_id}/run",
        method="POST",
        json={},
    ):
        from flask import g
        from flask_login import login_user

        g.force_local_run = True
        user_data = Database().get_user_by_id(user_id)
        if user_data:
            login_user(UserModel(user_data))
        try:
            resp = api_run_case(case_id)
            if isinstance(resp, tuple):
                rv, _code = resp
                if hasattr(rv, "get_json"):
                    return rv.get_json() or {}
                return {"success": False, "error": "empty response"}
            if hasattr(resp, "get_json"):
                return resp.get_json() or {}
            return {"success": False, "error": "unexpected response"}
        finally:
            g.force_local_run = False


from deployment_hooks import start_background_workers, wire_internal_runner  # noqa: E402

wire_internal_runner(app, _run_case_worker_sync)
start_background_workers()


if __name__ == '__main__':
    _port = int(os.environ.get('FLASK_RUN_PORT', '5000'))
    # 默认关闭 debug，避免生产式部署误暴露调试信息与 Werkzeug 交互式调试器
    _debug = os.environ.get('FLASK_DEBUG', 'false').lower() in ('1', 'true', 'yes')
    app.run(debug=_debug, host='0.0.0.0', port=_port, threaded=True)
