from flask import Flask, render_template, request, jsonify, session, make_response
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
import time
import secrets
import json
import functools
from database import Database
from playwright_automation import automation, sync_start_browser, sync_navigate_to, sync_scroll_page, sync_get_page_text, sync_extract_element_text, sync_extract_element_json, sync_get_page_title, sync_get_current_url, sync_get_all_links, sync_hover_element, sync_double_click_element, sync_right_click_element, sync_click_element, sync_fill_input, sync_get_page_elements, sync_extract_element_data, sync_get_page_data, sync_analyze_page_content, sync_close_browser, sync_wait_for_selector, sync_wait_for_element_visible, sync_take_screenshot, worker, sync_wait_for_timeout, sync_swipe_element, sync_verify_element, sync_select_option, sync_get_element_count, sync_select_date, sync_start_recording, sync_stop_recording, sync_enable_element_selection, sync_disable_element_selection, sync_get_selected_element, sync_extract_json_from_selected_element, sync_execute_multiple_test_cases, sync_enter_iframe, sync_exit_iframe, sync_wait_for_page_stable
from test_report import TestReportGenerator
from report_exporter import ReportExporter
from logger import uat_logger

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
def index():
    return render_template('index.html')

# ==================== 用户认证路由 ====================

@app.route('/login')
def login_page():
    return render_template('login.html')

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
    return jsonify({'success': True, 'user': {'id': current_user.id, 'username': current_user.username, 'role': current_user.role}})

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
def create_case_v2():
    response = make_response(render_template('create_case_v2.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response

# 项目管理页面
@app.route('/list_projects')
def list_projects():
    return render_template('list_projects.html')

# 测试用例管理页面（新版本）
@app.route('/list_cases_v2/<int:project_id>')
def list_cases_v2(project_id):
    return render_template('list_cases_v2.html', project_id=project_id)

# 测试步骤管理页面
@app.route('/list_steps')
def list_steps():
    return render_template('list_steps.html')

# API: 创建测试用例
@app.route('/api/create_case', methods=['POST'])
@api_error_handler
@log_api_request
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
@api_error_handler
@log_api_request
def api_get_test_cases():
    cases = db.get_all_test_cases()
    return jsonify({'cases': cases})

# API: 获取单个测试用例
@app.route('/api/test_case/<int:case_id>', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_test_case(case_id):
    case = db.get_test_case(case_id)
    if not case:
        return jsonify({'error': '测试用例不存在'}), 404
    return jsonify({'test_case': case})

# API: 更新测试用例
@app.route('/api/test_case/<int:case_id>', methods=['PUT'])
@api_error_handler
@log_api_request
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
@api_error_handler
@log_api_request
def api_delete_test_case(case_id):
    success = db.delete_test_case(case_id)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '删除测试用例失败'}), 400

# API: 启动浏览器进行录制
@app.route('/api/start_recording', methods=['POST'])
@api_error_handler
@log_api_request
def api_start_recording():
    data = request.get_json(silent=True) or {}
    url = data.get('url', '')
    
    try:
        # 启动浏览器
        uat_logger.info("启动浏览器用于录制")
        sync_start_browser(headless=False)
        
        # 开始录制 - 使用同步函数确保浏览器完全初始化
        sync_start_recording()
        
        # 如果提供了URL，导航到该URL并保存到会话中
        if url:
            uat_logger.log_automation_step("navigate", url, "录制开始时导航")
            sync_navigate_to(url)
            # 保存URL到会话中，以便后续使用
            try:
                session['current_url'] = url
            except Exception:
                # 如果session不可用，忽略错误
                pass
        
        response_data = {'success': True, 'message': '浏览器已启动，开始录制'}
        return jsonify(response_data)
    except Exception as e:
        uat_logger.error(f"启动录制失败: {str(e)}")
        # 尝试关闭浏览器，清理资源
        try:
            sync_close_browser()
        except Exception:
            pass
        return jsonify({'success': False, 'error': f'录制启动失败: {str(e)}'}), 500

# API: 停止录制并保存步骤
@app.route('/api/stop_recording', methods=['POST'])
@api_error_handler
@log_api_request
def api_stop_recording():
    # 获取录制的步骤
    steps = sync_stop_recording()
    uat_logger.info(f"停止录制，获取到 {len(steps)} 个步骤")
    
    # 尝试关闭浏览器，但不影响结果返回
    warning_msg = None
    try:
        sync_close_browser()
        uat_logger.info("浏览器已关闭")
    except Exception as close_error:
        warning_msg = f'录制成功但关闭浏览器时出现问题: {str(close_error)}'
        uat_logger.warning(warning_msg)
    
    response_data = {'success': True, 'steps': steps}
    if warning_msg:
        response_data['warning'] = warning_msg
        
    return jsonify(response_data)

# API: 执行多个测试用例
@app.route('/api/execute_multiple_cases', methods=['POST'])
@api_error_handler
@log_api_request
def api_execute_multiple_cases():
    data = request.get_json(silent=True) or {}
    case_ids = data.get('case_ids', [])
    
    if not case_ids:
        return jsonify({'success': False, 'error': '缺少测试用例ID列表参数'}), 400
    
    if not isinstance(case_ids, list):
        return jsonify({'success': False, 'error': 'case_ids参数必须是数组'}), 400
    
    # 添加调试信息
    uat_logger.info(f"📥 [API_ENTRY] 接收到的执行顺序: {case_ids}")
    uat_logger.info(f"📥 [API_ENTRY] 用例数量: {len(case_ids)}")
    
    uat_logger.info(f"开始执行多个测试用例，共 {len(case_ids)} 个用例")
    
    results = None
    
    try:
        # 严格同步执行多个测试用例，确保执行顺序
        uat_logger.info(f"🔧 [API] 开始严格同步执行多个测试用例")
        
        try:
            # 创建独立的数据库连接实例，确保线程安全
            from database import Database
            thread_db = Database()
            
            # 直接在主线程中同步执行测试用例，确保严格的执行顺序
            uat_logger.info(f"🚀 [API] 在主线程中同步执行测试用例序列: {case_ids}")
            results = sync_execute_multiple_test_cases(case_ids, thread_db)
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
    
    response_data = {'success': True, 'results': results}
    return jsonify(response_data)

# API: 导航到指定URL
@app.route('/api/navigate', methods=['POST'])
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
@api_error_handler
@log_api_request
def api_create_project():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '')
    description = data.get('description', '')
    
    if not name:
        return jsonify({'error': '项目名称不能为空'}), 400
    
    project_id = db.create_project(name, description)
    return jsonify({'success': True, 'project_id': project_id})

# API: 获取所有项目
@app.route('/api/projects', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_projects():
    projects = db.get_all_projects()
    return jsonify({'projects': projects})

# API: 获取单个项目
@app.route('/api/projects/<int:project_id>', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_project(project_id):
    project = db.get_project(project_id)
    if not project:
        return jsonify({'error': '项目不存在'}), 404
    return jsonify({'project': project})

# API: 更新项目
@app.route('/api/projects/<int:project_id>', methods=['PUT'])
@api_error_handler
@log_api_request
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
@api_error_handler
@log_api_request
def api_delete_project(project_id):
    success = db.delete_project(project_id)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '删除项目失败'}), 400

# API: 获取项目下的所有测试用例
@app.route('/api/projects/<int:project_id>/cases', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_project_cases(project_id):
    cases = db.get_project_cases(project_id)
    return jsonify({'cases': cases})

# ==================== 测试用例管理API（新版本） ====================

# API: 创建测试用例（关联到项目）
@app.route('/api/cases', methods=['POST'])
@api_error_handler
@log_api_request
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
    
    case_id = db.create_test_case_v2(project_id, name, url, description, precondition, expected_result)
    return jsonify({'success': True, 'case_id': case_id})

# API: 获取测试用例详情（新版本）
@app.route('/api/cases/<int:case_id>', methods=['GET'])
@api_error_handler
@log_api_request
def api_get_case_v2(case_id):
    case = db.get_test_case_v2(case_id)
    if not case:
        return jsonify({'error': '测试用例不存在'}), 404
    return jsonify({'test_case': case})

# API: 更新测试用例（新版本）
@app.route('/api/cases/<int:case_id>', methods=['PUT'])
@api_error_handler
@log_api_request
def api_update_case_v2(case_id):
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
@api_error_handler
@log_api_request
def api_delete_case_v2(case_id):
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
    
    steps = db.get_case_steps(case_id, page, page_size)
    total = db.get_case_steps_count(case_id)
    
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
    
    if not case_id:
        return jsonify({'error': '用例ID不能为空'}), 400
    if not action:
        return jsonify({'error': '操作类型不能为空'}), 400
    
    step_id = db.create_test_step(case_id, action, selector_type, selector_value, 
                                  input_value, description, step_order, page_name,
                                  swipe_x, swipe_y, url, enter_iframe, iframe_selector, compare_type)
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
    
    success = db.update_test_step(step_id, action, selector_type, selector_value,
                                   input_value, description, step_order, enter_iframe, iframe_selector, compare_type)
    
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

# API: 运行测试用例
@app.route('/api/cases/<int:case_id>/run', methods=['POST'])
@api_error_handler
@log_api_request
def api_run_case(case_id):
    try:
        # 记录开始时间
        start_time = time.time()
        
        # 初始化数据库连接（修复变量作用域问题）
        db = Database()
        
        # 获取测试用例信息
        case = db.get_test_case_v2(case_id)
        if not case:
            return jsonify({'error': '测试用例不存在'}), 404
        
        # 获取测试步骤
        steps = db.get_case_steps(case_id)
        if not steps:
            return jsonify({'error': '测试用例没有步骤'}), 400
        
        uat_logger.info(f"开始运行测试用例 #{case_id}: {case['name']}")
        uat_logger.info(f"测试用例共有 {len(steps)} 个步骤")
        
        # 提取的文本
        extracted_text = ""
        # 预期结果
        expected_text = ""
        # 截图列表（失败截图路径）
        screenshots = []
        # 步骤结果列表（用于步骤级记录）
        step_results_list = []
        
        # 启动浏览器
        sync_start_browser(headless=False)
        
        # 执行测试步骤
        try:
            # 如果有目标URL，先导航到该URL
            if case.get('url'):
                url = case['url']
                # 验证URL有效性
                if url and url.strip():
                    # 清理URL
                    url = url.strip()
                    
                    # 🔥 修复：跳过无意义的默认URL（如 example.com）
                    if 'example.com' in url.lower():
                        uat_logger.warning(f"检测到无意义的默认URL ({url})，跳过初始导航，等待步骤中的导航操作")
                    else:
                        # 自动添加协议前缀
                        if not url.startswith(('http://', 'https://')):
                            url = 'http://' + url
                        uat_logger.log_automation_step("navigate", url, "测试开始时导航")
                        sync_navigate_to(url)
                else:
                    uat_logger.warning("测试用例URL为空或无效，跳过初始导航")
            
            # 执行所有步骤
            for step in steps:
                action = step.get('action', '')
                selector_type = step.get('selector_type', 'css')
                # 变量替换：支持 {{变量名}} 语法
                selector_value = db.resolve_variables(step.get('selector_value', ''), project_id=case.get('project_id'), case_id=case_id)
                input_value = db.resolve_variables(step.get('input_value', ''), project_id=case.get('project_id'), case_id=case_id)
                description = step.get('description', '')
                # 添加iframe相关字段
                enter_iframe = step.get('enter_iframe', False)
                iframe_selector = step.get('iframe_selector', '')
                
                step_start_time = time.time()
                step_status = 'success'
                step_error = ''
                step_screenshot = ''

                uat_logger.log_automation_step(action, selector_value or input_value, description)
                                        
                # 详细的调试日志，跟踪 action 值和执行的方法
                uat_logger.debug(f"执行步骤: ID={step.get('id')}, Action={action}, SelectorType={selector_type}, SelectorValue={selector_value}, InputValue={input_value}, EnterIframe={enter_iframe}, IframeSelector={iframe_selector}")
                
                if action == 'navigate':
                    # 获取URL并进行有效性检查
                    url = None
                    if step.get('url'):
                        url = step['url']
                    elif step.get('input_value'):
                        url = step['input_value']
                    
                    # URL有效性检查和修复
                    if url:
                        # 清理URL
                        url = url.strip()
                        # 自动添加协议前缀
                        if not url.startswith(('http://', 'https://')):
                            url = 'http://' + url
                        
                        # 验证URL是否为有效地址（避免0.0.0.1等无效地址）
                        import re
                        # 改进的URL格式验证，包含IP地址范围验证
                        url_pattern = re.compile(
                            r'^(https?://)?'  # 协议前缀
                            r'(([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}|'  # 域名
                            r'localhost|'  # localhost
                            r'((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?))'  # 有效的IP地址
                            r'(:\d+)?'  # 端口
                            r'(/.*)?$'  # 路径
                        )
                        
                        if url_pattern.match(url):
                            uat_logger.log_automation_step("navigate", url, "导航到URL")
                            sync_navigate_to(url)
                        else:
                            error_msg = f"无效的URL地址: {url}"
                            uat_logger.error(error_msg)
                            raise Exception(error_msg)
                    else:
                        uat_logger.warning("导航步骤缺少有效的URL")
                elif action == 'click':
                            if selector_value:
                                try:
                                    sync_click_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                                except Exception as click_error:
                                    uat_logger.error(f"执行点击操作时出错: {click_error}")
                                    # 直接抛出错误，视为测试用例执行失败
                                    raise
                elif action == 'input':
                    if selector_value and input_value:
                        try:
                            uat_logger.info(f"🔍 准备执行输入操作: 步骤ID={step.get('id', 'unknown')}, 选择器类型={selector_type}, 选择器值={selector_value}, 输入值={input_value}")
                            
                            # 🔥 添加详细的诊断信息
                            if len(selector_value) > 200:
                                uat_logger.warning(f"⚠️ 检测到超长CSS选择器（{len(selector_value)}字符），建议优化选择器")
                                uat_logger.warning(f"   当前选择器前100字符: {selector_value[:100]}...")
                            
                            # 🔥 检查选择器类型
                            if selector_type == "css" and "nth-child" in selector_value:
                                uat_logger.warning(f"⚠️ 检测到使用nth-child定位，可能不够稳定，建议改用ID或类名")
                            
                            sync_fill_input(selector_value, input_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
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
                    direction = 'down'
                    pixels = 500
                    try:
                        sync_scroll_page(direction, pixels, iframe_selector=iframe_selector if enter_iframe else None)
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

                # 记录本步骤结果
                step_duration = round(time.time() - step_start_time, 3)
                step_results_list.append({
                    'step_id': step.get('id'), 'step_order': step.get('step_order', 0),
                    'action': action, 'selector_value': selector_value,
                    'input_value': input_value, 'description': description,
                    'status': step_status, 'error': step_error,
                    'screenshot': step_screenshot, 'duration': step_duration
                })
            
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
            uat_logger.error(f"测试用例 #{case_id} 运行失败: {str(e)}")

            # 失败时自动截图
            failure_screenshot = ''
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

            # 标记最后一步为失败
            if step_results_list and step_results_list[-1]['status'] == 'success':
                step_results_list[-1]['status'] = 'error'
                step_results_list[-1]['error'] = str(e)
                step_results_list[-1]['screenshot'] = failure_screenshot
            
            # 保存运行历史记录
            try:
                run_id = db.create_run_history(case_id, 'error', duration, str(e), extracted_text, expected_text)
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
            except Exception as history_error:
                uat_logger.warning(f"保存运行历史记录失败: {history_error}")
            
            # 尝试关闭浏览器
            try:
                sync_close_browser()
            except Exception:
                pass
            
            return jsonify({
                'success': False,
                'status': 'error',
                'duration': duration,
                'error': str(e)
            })
            
    except Exception as e:
        uat_logger.error(f"运行测试用例时发生错误: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/run-history', methods=['GET'])
def run_history_page():
    """运行历史记录页面"""
    return render_template('run_history.html')


@app.route('/api/run-history', methods=['GET'])
def get_run_history():
    """获取所有运行历史记录（支持分页、按测试用例ID过滤、按项目ID过滤和搜索）"""
    try:
        # 获取分页参数
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        case_id = request.args.get('case_id', type=int)
        project_id = request.args.get('project_id', type=int)
        search_text = request.args.get('search_text', type=str)
        
        db = Database()
        history = db.get_all_run_history(page, page_size, case_id, search_text, project_id)
        total = db.get_run_history_count(case_id, search_text, project_id)
        
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
def api_get_schedules():
    _db = Database()
    schedules = _db.get_all_schedules()
    return jsonify({'success': True, 'schedules': schedules})

@app.route('/api/schedules', methods=['POST'])
@login_required
@role_required('admin', 'tester')
def api_create_schedule():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    case_ids = data.get('case_ids', [])
    cron_expr = data.get('cron_expr', '').strip()
    if not name or not case_ids or not cron_expr:
        return jsonify({'success': False, 'error': '名称、用例ID列表和cron表达式不能为空'}), 400
    _db = Database()
    schedule_id = _db.create_schedule(name, case_ids, cron_expr, data.get('project_id'))
    # 注册到调度器
    _register_schedule_job(schedule_id, case_ids, cron_expr)
    return jsonify({'success': True, 'schedule_id': schedule_id})

@app.route('/api/schedules/<int:schedule_id>', methods=['PUT'])
@login_required
@role_required('admin', 'tester')
def api_update_schedule(schedule_id):
    data = request.get_json(silent=True) or {}
    _db = Database()
    success = _db.update_schedule(schedule_id,
        name=data.get('name'), cron_expr=data.get('cron_expr'),
        is_active=data.get('is_active'), case_ids=data.get('case_ids'))
    if success and data.get('cron_expr'):
        schedules = _db.get_all_schedules()
        for s in schedules:
            if s['id'] == schedule_id:
                _register_schedule_job(schedule_id, s['case_ids'], data['cron_expr'])
                break
    return jsonify({'success': success})

@app.route('/api/schedules/<int:schedule_id>', methods=['DELETE'])
@login_required
@role_required('admin', 'tester')
def api_delete_schedule(schedule_id):
    _db = Database()
    success = _db.delete_schedule(schedule_id)
    try:
        scheduler.remove_job(f'schedule_{schedule_id}')
    except Exception:
        pass
    return jsonify({'success': success})

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

# ==================== 调度器初始化 ====================

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler(timezone='Asia/Shanghai')

    def _run_scheduled_cases(schedule_id: int, case_ids: list):
        """调度器回调：执行定时用例"""
        import datetime
        uat_logger.info(f"⏰ 定时任务 #{schedule_id} 开始执行，用例: {case_ids}")
        _db = Database()
        try:
            results = sync_execute_multiple_test_cases(case_ids, _db)
            uat_logger.info(f"⏰ 定时任务 #{schedule_id} 完成，成功: {results.get('successful_cases',0)}, 失败: {results.get('failed_cases',0)}")
        except Exception as e:
            uat_logger.error(f"⏰ 定时任务 #{schedule_id} 执行失败: {e}")
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
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts
            trigger = CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week)
            scheduler.add_job(_run_scheduled_cases, trigger, args=[schedule_id, case_ids], id=job_id)
            uat_logger.info(f"定时任务 #{schedule_id} 已注册，cron: {cron_expr}")

    # 启动调度器并加载已有任务
    scheduler.start()
    _init_db = Database()
    for sched in _init_db.get_active_schedules():
        try:
            _register_schedule_job(sched['id'], sched['case_ids'], sched['cron_expr'])
        except Exception as e:
            uat_logger.warning(f"加载定时任务 #{sched['id']} 失败: {e}")
    uat_logger.info("APScheduler 调度器已启动")

except ImportError:
    uat_logger.warning("APScheduler 未安装，定时执行功能不可用。运行: pip install APScheduler==3.10.4")
    scheduler = None
    def _register_schedule_job(*args, **kwargs):
        pass


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
