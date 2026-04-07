from flask import Flask, render_template, request, jsonify, session, make_response, redirect, url_for, Response
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
import sys
import time
import shlex
import shutil
import secrets
import json
import functools
import tempfile
import subprocess
from database import Database
from playwright_automation import (
    automation,
    force_reset_execution_state,
    parse_platform_scroll_input_value,
    scroll_event_to_platform_input_value,
    sync_analyze_page_content,
    sync_click_element,
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
    sync_navigate_to,
    sync_right_click_element,
    sync_scroll_by_delta,
    sync_scroll_page,
    sync_select_date,
    sync_select_option,
    sync_start_browser,
    sync_swipe_element,
    sync_take_screenshot,
    sync_verify_element,
    sync_wait_for_element_visible,
    sync_wait_for_page_stable,
    sync_wait_for_selector,
    sync_wait_for_timeout,
    worker,
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

def _case_job_update(user_id: int, **kwargs):
    with _case_run_lock:
        if user_id in _case_run_jobs:
            _case_run_jobs[user_id].update(kwargs)


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

# 统一的API错误处理装饰器
def api_error_handler(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # 记录异常
            uat_logger.log_exception(f"API Error in {func.__name__}", e)
            # 返回统一的错误响应
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    return wrapper

# API请求日志装饰器
def log_api_request(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 记录请求，处理没有请求体的情况
        try:
            request_data = request.json if request.method in ['POST', 'PUT', 'PATCH'] else None
        except Exception:
            # 如果解析JSON失败（如请求体为空），使用None
            request_data = None
        uat_logger.log_api_request(func.__name__, request.method, request_data)
        # 执行函数
        response = func(*args, **kwargs)
        # 记录响应
        try:
            if isinstance(response, tuple):
                uat_logger.log_api_response(func.__name__, response[1], response[0].get_json())
            else:
                uat_logger.log_api_response(func.__name__, 200, response.get_json())
        except Exception:
            # 如果响应无法解析为JSON，记录基本信息
            status_code = response[1] if isinstance(response, tuple) else 200
            uat_logger.log_api_response(func.__name__, status_code, None)
        return response
    return wrapper

app = Flask(__name__)
CORS(app)
# 设置Flask应用的密钥，用于session加密
app.secret_key = 'your-secret-key-here'

# ==================== Flask-Login 初始化 ====================
login_manager = LoginManager(app)
login_manager.login_view = 'login_page'
login_manager.login_message = '请先登录'

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
            project_id = kwargs.get('project_id') or request.view_args.get('project_id')
            if not project_id:
                # 尝试从请求参数中获取
                project_id = request.args.get('project_id', type=int) or request.json.get('project_id') if request.json else None

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
                            details = json.dumps(request.get_json(), ensure_ascii=False)
                        except:
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
                except:
                    pass

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
    if db.count_users() == 0:
        db.create_user('admin', generate_password_hash('admin123'), role='admin')
        uat_logger.info("首次启动：已创建默认管理员账号 admin/admin123，请尽快修改密码！")

_ensure_admin()

# 主页路由
@app.route('/')
@login_required
def index():
    return render_template('index.html')

# ==================== 用户认证路由 ====================

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/profile')
@login_required
def profile_page():
    return render_template('profile.html')

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
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400
    _db = Database()
    user_data = _db.get_user_by_username(username)
    if not user_data or not check_password_hash(user_data['password_hash'], password):
        return jsonify({'success': False, 'error': '用户名或密码错误'}), 401
    if not user_data.get('is_active', 1):
        return jsonify({'success': False, 'error': '账号已被禁用'}), 403
    user = UserModel(user_data)
    login_user(user, remember=True)
    _db.update_user_last_login(user_data['id'])
    uat_logger.info(f"用户 {username} 登录成功")
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
    email = data.get('email', '').strip()

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
@login_required
@role_required('admin')
def api_update_user(user_id):
    data = request.get_json(silent=True) or {}
    _db = Database()
    success = _db.update_user(user_id,
        email=data.get('email'),
        role=data.get('role'),
        is_active=data.get('is_active'),
        password_hash=generate_password_hash(data['password']) if data.get('password') else None
    )
    return jsonify({'success': success})

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_user(user_id):
    if user_id == current_user.id:
        return jsonify({'success': False, 'error': '不能删除当前登录的账号'}), 400
    _db = Database()
    success = _db.delete_user(user_id)
    return jsonify({'success': success})

@app.route('/create_case_v2')
@login_required
def create_case_v2():
    response = make_response(render_template('create_case_v2.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response

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

    url = url.strip()

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
                'error': f'今日执行次数已达上限（{DAILY_LIMIT}次）。请升级至专业版解除限制。',
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

            results = sync_execute_multiple_test_cases(case_ids, thread_db, should_stop=_should_stop_batch)
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

# API: 启动可视化选择
@app.route('/api/start_visual_selection', methods=['POST'])
@api_error_handler
@log_api_request
def api_start_visual_selection():
    try:
        # 获取请求数据，使用silent=True避免解析失败时返回400错误
        data = request.get_json(silent=True) or {}
        target_url = data.get('url', '')
        
        # 启动可视化选择功能，并传递目标URL
        sync_enable_element_selection(target_url)
        return jsonify({'success': True, 'message': '可视化选择已启动'})
    except Exception as e:
        uat_logger.error(f"启动可视化选择失败: {e}")
        return jsonify({'success': False, 'error': str(e)})

# API: 停止可视化选择
@app.route('/api/stop_visual_selection', methods=['POST'])
@api_error_handler
@log_api_request
def api_stop_visual_selection():
    try:
        # 停止可视化选择功能
        sync_disable_element_selection()
        return jsonify({'success': True, 'message': '可视化选择已停止'})
    except Exception as e:
        uat_logger.error(f"停止可视化选择失败: {e}")
        return jsonify({'success': False, 'error': str(e)})

# API: 检查选择的元素
@app.route('/api/check_selected_element', methods=['GET'])
@api_error_handler
@log_api_request
def api_check_selected_element():
    try:
        # 获取选择的元素
        selected_element = sync_get_selected_element()
        if selected_element:
            return jsonify({'success': True, 'selected_element': selected_element})
        else:
            return jsonify({'success': True, 'selected_element': None})
    except Exception as e:
        uat_logger.error(f"检查选择元素失败: {e}")
        return jsonify({'success': False, 'error': str(e)})

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

# API: 启用元素选择模式
@app.route('/api/enable_element_selection', methods=['POST'])
@api_error_handler
@log_api_request
def api_enable_element_selection():
    try:
        sync_enable_element_selection()
        return jsonify({'success': True, 'message': '元素选择模式已启用'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# API: 禁用元素选择模式
@app.route('/api/disable_element_selection', methods=['POST'])
@api_error_handler
@log_api_request
def api_disable_element_selection():
    try:
        sync_disable_element_selection()
        return jsonify({'success': True, 'message': '元素选择模式已禁用'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# API: 获取选中的元素信息
@app.route('/api/get_selected_element', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_selected_element():
    try:
        element_info = sync_get_selected_element()
        return jsonify({'success': True, 'element': element_info})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '删除项目失败'}), 400

# API: 获取项目下的所有测试用例
@app.route('/api/projects/<int:project_id>/cases', methods=['GET'])
@login_required
@project_access_required(min_role='viewer')
@api_error_handler
@log_api_request
def api_get_project_cases(project_id):
    cases = db.get_project_cases(project_id)
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
    
    # 获取项目当前用例数量
    project_cases = db.get_project_cases(project_id)
    current_case_count = len(project_cases) if project_cases else 0
    
    if limits['max_cases_per_project'] != -1 and current_case_count >= limits['max_cases_per_project']:
        return jsonify({
            'success': False,
            'error': f'已达到项目用例数量限制（{limits["max_cases_per_project"]}个）。请升级至专业版解除限制。',
            'limit_reached': True,
            'current_count': current_case_count,
            'limit': limits['max_cases_per_project'],
            'upgrade_url': '/license'
        }), 403
    
    # 记录创建用例计数（仅免费版）
    if license_info.license_type == LicenseType.FREE.value:
        _db.increment_created_cases(current_user.id)
    
    case_id = db.create_test_case_v2(project_id, name, url, description, precondition, expected_result)
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
    
    if not case_id:
        return jsonify({'error': '用例ID不能为空'}), 400
    if not action:
        return jsonify({'error': '操作类型不能为空'}), 400
    
    step_id = db.create_test_step(case_id, action, selector_type, selector_value, 
                                  input_value, description, step_order, page_name,
                                  swipe_x, swipe_y, url, enter_iframe, iframe_selector, compare_type,
                                  locator_candidates or '')
    return jsonify({'success': True, 'step_id': step_id})

# API: 更新测试步骤
@app.route('/api/steps/<int:step_id>', methods=['PUT'])
@api_error_handler
@log_api_request
def api_update_step(step_id):
    data = request.get_json(silent=True) or {}
    action = data.get('action')
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
    
    success = db.update_test_step(step_id, action, selector_type, selector_value,
                                   input_value, description, step_order, enter_iframe, iframe_selector, compare_type,
                                   locator_candidates)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '更新测试步骤失败'}), 400

# API: 删除测试步骤
@app.route('/api/steps/<int:step_id>', methods=['DELETE'])
@api_error_handler
@log_api_request
def api_delete_step(step_id):
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


def _run_db_step_scroll(input_value: str, iframe_selector=None):
    """按平台存储的 up/down/left/right 像素执行滚动；无有效值时回退为向下 500px。"""
    v = parse_platform_scroll_input_value(input_value)
    dx = v["right"] - v["left"]
    dy = v["down"] - v["up"]
    if dx != 0 or dy != 0:
        sync_scroll_by_delta(dx, dy, iframe_selector=iframe_selector)
    else:
        sync_scroll_page("down", 500, iframe_selector=iframe_selector)


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

    _db = Database()
    if case.get('project_id') and not _db.check_project_access(current_user.id, case['project_id'], 'editor'):
        return jsonify({'success': False, 'error': '无权限修改此用例'}), 403

    steps, warnings = parse_playwright_codegen_to_steps(code)
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

    _db = Database()
    if case.get('project_id') and not _db.check_project_access(current_user.id, case['project_id'], 'editor'):
        return jsonify({'success': False, 'error': '无权限修改此用例'}), 403

    steps, warnings = parse_selenium_ide_to_steps(payload)
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
                'error': f'今日执行次数已达上限（{DAILY_LIMIT}次）。请升级至专业版解除限制。',
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
    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'error': '测试用例不存在'}), 404

    # 获取测试步骤
    steps = db.get_case_steps(case_id)
    if not steps:
        # 用例无步骤，保存一条失败历史并友好提示
        run_id = db.create_run_history(case_id, 'error', 0, '该用例没有步骤，无法执行', '', '')
        uat_logger.warning(f"测试用例 #{case_id} 没有步骤，无法执行")
        return jsonify({'success': False, 'status': 'error', 'duration': 0,
                        'error': '该用例尚未添加任何步骤，请先编辑用例添加步骤后再执行。'})

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
    
    try:
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
            # 调用强制重置函数，它会清空执行锁和所有浏览器引用
            force_reset_execution_state()
            uat_logger.info("✅ [浏览器恢复] 状态已重置，将自动重新启动浏览器")

        # 启动浏览器（如果断连后已重置，这里会创建新实例）
        sync_start_browser()
        
        # 执行测试步骤
        try:
            # 统一导航优先级：case.url > step.url/navigate.input_value
            initial_nav_url, nav_source = _resolve_case_navigation_url(case=case, case_id=case_id, steps=steps)
            if initial_nav_url:
                uat_logger.log_automation_step("navigate", initial_nav_url, f"测试开始时导航({nav_source})")
                sync_navigate_to(initial_nav_url)
            else:
                uat_logger.warning("未找到可用初始URL，跳过测试开始导航")
            
            # 执行所有步骤
            for step in steps:
                with _case_run_lock:
                    cancel_requested = bool(_case_run_jobs.get(user_id, {}).get('cancel_requested'))
                if cancel_requested:
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
                locator_candidates = step.get('locator_candidates') or None

                step_start_time = time.time()
                # 🔥 修复：初始化为 error，只有执行成功才改为 success
                step_status = 'error'
                step_error = ''
                step_screenshot = ''
                
                uat_logger.log_automation_step(action, selector_value or input_value, description)
                _case_job_update(
                    user_id,
                    current_step_order=step.get('step_order', 0),
                    current_action=action,
                    message=f"正在执行步骤 {step.get('step_order', 0)}/{len(steps)}: {action}",
                )
                                                        
                # 详细的调试日志，跟踪 action 值和执行的方法
                uat_logger.debug(f"执行步骤：ID={step.get('id')}, Action={action}, SelectorType={selector_type}, SelectorValue={selector_value}, InputValue={input_value}, EnterIframe={enter_iframe}, IframeSelector={iframe_selector}")
                
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
                            if selector_value:
                                try:
                                    sync_click_element(
                                        selector_value,
                                        selector_type,
                                        iframe_selector=iframe_selector if enter_iframe else None,
                                        locator_candidates=locator_candidates,
                                    )
                                except Exception as click_error:
                                    uat_logger.error(f"执行点击操作时出错: {click_error}")
                                    # 直接抛出错误，视为测试用例执行失败
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
                                    iframe_selector=iframe_selector if enter_iframe else None,
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
                            uat_logger.error(f"   iframe选择器: {iframe_selector if enter_iframe else None}")
                            
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
                elif action == 'hover':
                    if selector_value:
                        try:
                            sync_hover_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
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
                            sync_double_click_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
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
                            sync_right_click_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
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
                            sync_select_option(selector_value, input_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
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
                            iframe_selector=iframe_selector if enter_iframe else None,
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
                            sync_swipe_element(selector_value, direction, distance, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
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
                        sync_verify_element(selector=selector_value, verify_type=verify_type, selector_type=selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                        # 验证后等待页面响应
                        sync_wait_for_timeout(1500)
                    except Exception as verify_error:
                        uat_logger.error(f"执行验证操作时出错: {verify_error}")
                        # 直接抛出错误，视为测试用例执行失败
                        raise
                elif action == 'extract_text' or action == 'text_compare':
                    if selector_value:
                        # 构建完整的选择器
                        full_selector = selector_value
                        if selector_type == 'xpath' and not full_selector.startswith('xpath='):
                            full_selector = f'xpath={full_selector}'
                        # 提取元素文本（添加异常处理）
                        try:
                            current_extracted = sync_extract_element_text(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                            uat_logger.info(f"提取到文本: {current_extracted[:100]}...")
                            # 保存到extracted_text变量，而不是覆盖
                            extracted_text = current_extracted
                        except Exception as extract_error:
                            uat_logger.error(f"提取文本失败: {extract_error}")
                            # 🔥 修复：提取文本失败应该视为测试失败
                            raise Exception(f"提取文本失败: {extract_error}")
                        
                        # 检查是否需要验证文本
                        current_expected = input_value or description
                        verify_type = step.get('compare_type', step.get('verify_type', 'equals'))
                        # 保存预期结果
                        expected_text = current_expected
                        
                        if expected_text:
                            # 只有当提取到文本时才进行验证
                            if extracted_text:
                                uat_logger.info(f"验证文本 - 提取: {extracted_text[:100]}..., 预期: {expected_text[:100]}..., 验证方式: {verify_type}")
                                
                                # 根据验证方式执行不同的验证逻辑
                                if verify_type == 'equals':
                                    if extracted_text != expected_text:
                                        uat_logger.error("文本验证失败: 提取的文本与预期结果不相等")
                                        raise Exception(f"文本验证失败: 提取的文本与预期结果不相等")
                                elif verify_type == 'not_equals':
                                    if extracted_text == expected_text:
                                        uat_logger.error("文本验证失败: 提取的文本与预期结果相等")
                                        raise Exception(f"文本验证失败: 提取的文本与预期结果相等")
                                elif verify_type == 'contains':
                                    if expected_text not in extracted_text:
                                        uat_logger.error("文本验证失败: 提取的文本不包含预期内容")
                                        raise Exception(f"文本验证失败: 提取的文本不包含预期内容")
                                elif verify_type == 'partial':
                                    if expected_text not in extracted_text:
                                        uat_logger.error("文本验证失败: 提取的文本不包含预期的部分内容")
                                        raise Exception(f"文本验证失败: 提取的文本不包含预期的部分内容")
                                
                                uat_logger.info("文本验证成功")
                            else:
                                # 如果没有提取到文本，且是text_compare操作，则跳过验证
                                if action == 'text_compare':
                                    uat_logger.warning("未提取到文本，跳过文本验证")
                                else:
                                    uat_logger.info("提取文本操作完成（未提取到文本）")
                        
                        # 提取后等待页面响应
                        sync_wait_for_timeout(1000)
                    else:
                        # 提取整个页面文本
                        try:
                            current_extracted = sync_get_page_text()
                            uat_logger.info(f"提取到页面文本: {current_extracted[:100]}...")
                            # 保存到extracted_text变量
                            extracted_text = current_extracted
                        except Exception as extract_error:
                            uat_logger.error(f"提取页面文本失败: {extract_error}")
                            # 🔥 修复：提取页面文本失败应该视为测试失败
                            raise Exception(f"提取页面文本失败: {extract_error}")
                        
                        # 检查是否需要验证文本
                        current_expected = input_value or description
                        verify_type = step.get('compare_type', step.get('verify_type', 'equals'))
                        # 保存预期结果
                        expected_text = current_expected
                        
                        if expected_text:
                            # 只有当提取到文本时才进行验证
                            if extracted_text:
                                uat_logger.info(f"验证页面文本 - 提取: {extracted_text[:100]}..., 预期: {expected_text[:100]}..., 验证方式: {verify_type}")
                                
                                # 根据验证方式执行不同的验证逻辑
                                if verify_type == 'equals':
                                    if extracted_text != expected_text:
                                        uat_logger.error("页面文本验证失败: 提取的文本与预期结果不相等")
                                        raise Exception(f"页面文本验证失败: 提取的文本与预期结果不相等")
                                elif verify_type == 'not_equals':
                                    if extracted_text == expected_text:
                                        uat_logger.error("页面文本验证失败: 提取的文本与预期结果相等")
                                        raise Exception(f"页面文本验证失败: 提取的文本与预期结果相等")
                                elif verify_type == 'contains':
                                    if expected_text not in extracted_text:
                                        uat_logger.error("页面文本验证失败: 提取的文本不包含预期内容")
                                        raise Exception(f"页面文本验证失败: 提取的文本不包含预期内容")
                                elif verify_type == 'partial':
                                    if expected_text not in extracted_text:
                                        uat_logger.error("页面文本验证失败: 提取的文本不包含预期的部分内容")
                                        raise Exception(f"页面文本验证失败: 提取的文本不包含预期的部分内容")
                                
                                uat_logger.info("页面文本验证成功")
                            else:
                                # 🔥 修复：如果没有提取到文本，应该视为失败（除非是text_compare）
                                if action == 'text_compare':
                                    uat_logger.warning("未提取到页面文本，跳过文本验证")
                                else:
                                    uat_logger.error("提取页面文本操作失败：未提取到文本")
                                    raise Exception("提取页面文本操作失败：未提取到文本")
                        
                        # 提取后等待页面响应
                        sync_wait_for_timeout(1000)
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
                    # 断言操作 - 使用断言引擎执行断言
                    from assertion_engine import AssertionEngine
                    
                    # 获取断言类型和参数
                    assert_type = step.get('compare_type', 'text_equals')  # 复用compare_type字段存储断言类型
                    expected_value = input_value
                    
                    uat_logger.info(f"执行断言操作: 类型={assert_type}, 选择器={selector_value}, 预期={expected_value}")
                    
                    try:
                        # 根据断言类型执行不同的断言
                        if assert_type in ['text_equals', 'text_contains', 'text_regex']:
                            # 文本类断言 - 需要先提取元素文本
                            actual_text = sync_extract_element_text(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                            
                            if assert_type == 'text_equals':
                                if actual_text != expected_value:
                                    raise Exception(f"断言失败: 实际文本 '{actual_text}' 不等于预期 '{expected_value}'")
                            elif assert_type == 'text_contains':
                                if expected_value not in actual_text:
                                    raise Exception(f"断言失败: 实际文本 '{actual_text}' 不包含预期 '{expected_value}'")
                            elif assert_type == 'text_regex':
                                import re
                                if not re.search(expected_value, actual_text):
                                    raise Exception(f"断言失败: 实际文本 '{actual_text}' 不匹配正则 '{expected_value}'")
                            
                            extracted_text = actual_text
                            uat_logger.info(f"断言成功: {assert_type}")
                            
                        elif assert_type in ['element_exists', 'element_visible']:
                            # 元素存在/可见断言
                            from playwright_automation import sync_wait_for_selector, sync_wait_for_element_visible
                            
                            if assert_type == 'element_exists':
                                sync_wait_for_selector(selector_value, timeout=5000)
                                uat_logger.info(f"断言成功: 元素存在 {selector_value}")
                            else:
                                sync_wait_for_element_visible(selector_value, timeout=5000)
                                uat_logger.info(f"断言成功: 元素可见 {selector_value}")
                                
                        elif assert_type == 'element_count':
                            # 元素数量断言
                            actual_count = sync_get_element_count(selector_value, selector_type)
                            expected_count = int(expected_value) if expected_value else 0
                            operator = step.get('swipe_x', 'equals')  # 复用swipe_x字段存储运算符
                            
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
                                raise Exception(f"断言失败: 实际数量 {actual_count} 不符合预期 {operator} {expected_count}")
                            uat_logger.info(f"断言成功: 元素数量符合预期")
                            
                        elif assert_type in ['url_equals', 'url_contains']:
                            # URL断言
                            actual_url = sync_get_current_url()
                            
                            if assert_type == 'url_equals':
                                if actual_url != expected_value:
                                    raise Exception(f"断言失败: 实际URL '{actual_url}' 不等于预期 '{expected_value}'")
                            else:
                                if expected_value not in actual_url:
                                    raise Exception(f"断言失败: 实际URL '{actual_url}' 不包含预期 '{expected_value}'")
                            
                            uat_logger.info(f"断言成功: {assert_type}")
                            
                        elif assert_type == 'element_attr':
                            # 元素属性断言
                            attr_name = step.get('page_name', '')  # 复用page_name字段存储属性名
                            # 使用JavaScript获取属性值（automation/worker 已在文件顶部导入；此处再 import 会使
                            # api_run_case 整函数内 automation 变为局部变量，导致前面浏览器检测处 UnboundLocalError）
                            async def get_attr():
                                page = await automation.get_page()
                                element = await page.query_selector(selector_value)
                                if element:
                                    return await element.get_attribute(attr_name)
                                return None
                            actual_attr = worker.execute(get_attr)
                            
                            if actual_attr != expected_value:
                                raise Exception(f"断言失败: 属性 {attr_name} 实际值 '{actual_attr}' 不等于预期 '{expected_value}'")
                            uat_logger.info(f"断言成功: 属性 {attr_name} = {actual_attr}")
                            
                        else:
                            uat_logger.warning(f"未知的断言类型: {assert_type}")
                            
                    except Exception as assert_error:
                        uat_logger.error(f"断言失败: {assert_error}")
                        raise
                    
                    # 断言后等待页面响应
                    sync_wait_for_timeout(500)
                elif action == 'enter_iframe':
                    if selector_value:
                        # 进入iframe
                        try:
                            sync_enter_iframe(selector_value, selector_type)
                            # 更新iframe状态
                            enter_iframe = True
                            iframe_selector = selector_value
                            uat_logger.info(f"✅ 成功进入iframe: {selector_value}")
                        except Exception as enter_error:
                            uat_logger.error(f"执行进入iframe操作时出错: {enter_error}")
                            # 直接抛出错误，视为测试用例执行失败
                            raise
                    else:
                        uat_logger.warning("进入iframe操作缺少选择器")
                elif action == 'exit_iframe':
                    # 跳出iframe
                    try:
                        sync_exit_iframe()
                        # 更新iframe状态
                        enter_iframe = False
                        iframe_selector = None
                        uat_logger.info("✅ 成功跳出iframe，返回主文档")
                    except Exception as exit_error:
                        uat_logger.error(f"执行跳出iframe操作时出错: {exit_error}")
                        # 直接抛出错误，视为测试用例执行失败
                        raise

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
            except Exception as history_error:
                uat_logger.warning(f"保存运行历史记录失败: {history_error}")
            
            # 尝试关闭浏览器
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
            error_msg = str(e)
            
            # 检测浏览器是否被手动关闭
            browser_closed_keywords = ['browser', 'closed', 'connection', 'target', 'page', 'context', 'crashed', 'disconnected']
            if any(keyword in error_msg.lower() for keyword in browser_closed_keywords):
                browser_closed_manually = True
                error_msg = "浏览器被手动关闭或连接中断"
                uat_logger.warning(f"测试用例 #{case_id} 执行中断: {error_msg}")
                # 🔥 浏览器被关闭时强制重置所有状态（包括执行锁和浏览器引用）
                try:
                    force_reset_execution_state()
                except Exception:
                    pass
            else:
                uat_logger.error(f"测试用例 #{case_id} 运行失败: {error_msg}")

            # 失败时自动截图（仅在浏览器未关闭时）
            failure_screenshot = ''
            if not browser_closed_manually:
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
            
            # 尝试关闭浏览器（仅在浏览器未被关闭时）
            if not browser_closed_manually:
                try:
                    sync_close_browser()
                except Exception:
                    pass
            
            return jsonify({
                'success': False,
                'status': 'error',
                'duration': duration,
                'error': error_msg,
                'browser_closed': browser_closed_manually,
                'stopped': ('用户已停止执行' in error_msg)
            })
            
    except Exception as e:
        # 最外层异常处理 - 确保历史记录被保存
        duration = round(time.time() - start_time, 2)
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
            'error': error_msg
        }), 500
    finally:
        with _case_run_lock:
            job = _case_run_jobs.get(user_id)
            if job:
                job['active'] = False
                job['finished_at'] = time.time()
                job['duration'] = round(time.time() - job.get('started_at', time.time()), 2)


@app.route('/api/cases/current-run/status', methods=['GET'])
@login_required
@api_error_handler
def api_case_run_status():
    user_id = current_user.id
    with _case_run_lock:
        job = _case_run_jobs.get(user_id)
    if not job:
        return jsonify({
            'success': True,
            'active': False,
            'total_steps': 0,
            'completed_steps': 0,
            'progress': 0,
            'message': '暂无运行任务'
        })

    total_steps = max(1, int(job.get('total_steps', 0) or 0))
    completed_steps = int(job.get('completed_steps', 0) or 0)
    progress = min(100, int((completed_steps / total_steps) * 100))
    return jsonify({
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
    })


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
        cases = _db.get_project_cases(project_id)
        if not cases:
            return jsonify({'success': False, 'error': '项目没有测试用例'}), 400
        case_ids = [c['id'] for c in cases]
        uat_logger.info(f"CI/CD 触发项目 #{project_id} 执行，共 {len(case_ids)} 个用例")
        results = sync_execute_multiple_test_cases(case_ids, _db)
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
        results = sync_execute_multiple_test_cases(case_ids, _db)
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
        steps = _db.get_case_steps(case_id)
        if not steps:
            _dataset_job_update(run_id, finished=True, success=False, error='测试用例没有步骤')
            return

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
                    url_value = resolve_with_row(step.get('url', '') or '', row_data)

                    step_start = _time.time()
                    step_status = 'success'
                    step_error = ''
                    try:
                        action = step.get('action', '')
                        selector_type = step.get('selector_type', 'css')
                        enter_iframe = step.get('enter_iframe', False)
                        iframe_sel = step.get('iframe_selector', '') if enter_iframe else None
                        if action == 'navigate':
                            nav_url = url_value or input_value or case.get('url', '')
                            if nav_url:
                                if not nav_url.startswith(('http://', 'https://')):
                                    nav_url = 'http://' + nav_url
                                sync_navigate_to(nav_url)
                        elif action == 'click':
                            if selector_value:
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
                        elif action == 'wait':
                            wait_ms = int(input_value) * 1000 if input_value and int(input_value) < 1000 else (int(input_value) if input_value else 1000)
                            sync_wait_for_timeout(wait_ms)
                        elif action == 'select':
                            if selector_value and input_value:
                                sync_select_option(selector_value, input_value, selector_type, iframe_selector=iframe_sel)
                        elif action == 'scroll':
                            _run_db_step_scroll(input_value or "", iframe_selector=iframe_sel)
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
    run_id, job = _get_current_user_dataset_job(current_user.id)
    if not job:
        return jsonify({'success': True, 'active': False, 'message': '暂无运行任务'})

    finished = bool(job.get('finished'))
    total = int(job.get('total', 0) or 0)
    completed = int(job.get('completed', 0) or 0)
    return jsonify({
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
    })


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

        try:
            results = sync_execute_multiple_test_cases(case_ids, _db)
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
                        'executed_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    })
                elif case_status in ('error', 'failed'):
                    notify('case_failed', {
                        'case_name': case_name,
                        'error': case_error or '执行失败',
                        'executed_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                    'executed_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                    'executed_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                    'executed_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
        finally:
            try:
                sync_close_browser()
            except Exception:
                pass
            last_run = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
        import traceback
        print(f"[ERROR] api_get_user_license_info: {str(e)}")
        print(traceback.format_exc())
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

    # 保存 License
    if license_manager.save_license(license_str):
        return jsonify({
            'success': True,
            'message': 'License 激活成功',
            'license': {
                'type': result['info'].license_type,
                'expires_at': result['info'].expires_at
            }
        })
    else:
        return jsonify({'success': False, 'error': 'License 保存失败'}), 500


# ==================== 审计日志 API ====================

@app.route('/api/audit-logs', methods=['GET'])
@login_required
@role_required('admin')
def api_get_audit_logs():
    """获取审计日志（仅管理员）"""
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 50, type=int)
    target_type = request.args.get('target_type')

    _db = Database()
    logs = _db.get_audit_logs(target_type=target_type, page=page, page_size=page_size)
    total = _db.get_audit_logs_count(target_type=target_type)

    return jsonify({
        'success': True,
        'logs': logs,
        'total': total,
        'page': page,
        'page_size': page_size
    })


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
    """健康检查接口"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.datetime.now().isoformat(),
        'version': '2.0.0'
    })


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
        else:
            return jsonify({'success': False, 'error': '该 SSO 类型不支持跳转登录'}), 400
        
        return jsonify({'success': True, 'login_url': login_url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sso/callback/<int:config_id>', methods=['GET'])
def api_sso_callback(config_id):
    """处理 SSO 登录回调"""
    try:
        from sso_manager import sso_manager
        code = request.args.get('code')
        state = request.args.get('state')
        
        if not code:
            return redirect('/login?error=sso_failed')
        
        success, user_info, message = sso_manager.authenticate(config_id, code=code)
        
        if not success:
            uat_logger.error(f"SSO 登录失败: {message}")
            return redirect(f'/login?error={message}')
        
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
        
        return jsonify({'success': True, 'order': order})
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


# ==================== 审计日志页面 ====================

@app.route('/audit-logs')
@login_required
@role_required('admin')
def audit_logs_page():
    """审计日志页面"""
    return render_template('audit_logs.html')


# ==================== Allure 风格报告 API ====================

@app.route('/allure-report')
@login_required
def allure_report_page():
    """返回 Allure 风格报告页面"""
    return render_template('allure_report.html')


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
    
    print(f"[DEBUG] Update defect status: defect_id={defect_id}, new_status={new_status}, user_id={current_user.id}")
    
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
    
    print(f"[DEBUG] Current defect status: {current_defect.get('status')}, new_status: {new_status}")
    
    success = _db.update_defect_status(defect_id, current_user.id, new_status)
    
    print(f"[DEBUG] Update result: success={success}")
    
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
    """下载导出文件"""
    from flask import send_from_directory
    export_dir = os.path.join(os.path.dirname(__file__), 'exports')
    return send_from_directory(export_dir, filename, as_attachment=True)


if __name__ == '__main__':
    _port = int(os.environ.get('FLASK_RUN_PORT', '5000'))
    _debug = os.environ.get('FLASK_DEBUG', 'true').lower() in ('1', 'true', 'yes')
    app.run(debug=_debug, host='0.0.0.0', port=_port)
