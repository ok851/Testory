from flask import Flask, render_template, request, jsonify, session, make_response
from flask_cors import CORS
import os
import time
from database import Database
from playwright_automation import automation, sync_start_browser, sync_navigate_to, sync_scroll_page, sync_get_page_text, sync_extract_element_text, sync_extract_element_json, sync_get_page_title, sync_get_current_url, sync_get_all_links, sync_hover_element, sync_double_click_element, sync_right_click_element, sync_click_element, sync_fill_input, sync_get_page_elements, sync_extract_element_data, sync_get_page_data, sync_analyze_page_content, sync_close_browser, sync_execute_script_steps, sync_start_recording, sync_stop_recording, sync_wait_for_selector, sync_wait_for_element_visible, sync_take_screenshot, sync_execute_multiple_test_cases, worker, sync_enable_element_selection, sync_disable_element_selection, sync_get_selected_element, sync_extract_json_from_selected_element, sync_wait_for_timeout  # 使用全局实例和同步包装器
import asyncio
import json
import functools
from logger import uat_logger

def generate_selector_by_method(method, value):
    """根据定位方法生成对应的选择器"""
    if not value:
        return ""
    
    method = method.lower()
    
    if method == 'xpath':
        return value
    elif method == 'id':
        return f'#{value}'
    elif method == 'name':
        return f'[name="{value}"]'
    elif method == 'class':
        return f'.{value}'
    elif method == 'text':
        return f':has-text("{value}")'
    elif method == 'css':
        return value
    else:
        return value

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

# 初始化数据库
db = Database()

# 主页路由
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_case_v2')
def create_case_v2():
    response = make_response(render_template('create_case_v2.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response

# CDN测试页面
@app.route('/cdn_test')
def cdn_test():
    response = make_response(render_template('cdn_test.html'))
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
    
    uat_logger.info(f"开始执行多个测试用例，共 {len(case_ids)} 个用例")
    
    results = None
    
    try:
        # 执行多个测试用例，添加超时处理
        import threading
        import queue
        import time
        
        # 创建队列用于返回结果
        result_queue = queue.Queue(maxsize=1)  # 设置队列大小，防止阻塞
        
        def execute_test_cases():
            """在子线程中执行测试用例"""
            try:
                # 创建独立的数据库连接实例，确保线程安全
                from database import Database
                thread_db = Database()
                
                # 执行测试用例
                result = sync_execute_multiple_test_cases(case_ids, thread_db)
                
                # 尝试将结果放入队列，设置超时
                try:
                    result_queue.put((True, result), timeout=10)
                except queue.Full:
                    uat_logger.error("结果队列已满，无法放入执行结果")
            except Exception as e:
                uat_logger.error(f"执行测试用例时发生异常: {str(e)}")
                try:
                    result_queue.put((False, str(e)), timeout=10)
                except queue.Full:
                    uat_logger.error("结果队列已满，无法放入错误信息")
        
        # 启动子线程执行测试用例
        thread = threading.Thread(target=execute_test_cases)
        thread.daemon = True
        thread.start()
        
        # 等待测试用例执行完成，设置超时时间为300秒（5分钟）
        thread.join(300)  # 300秒超时
        
        if thread.is_alive():
            # 测试用例执行超时
            uat_logger.error("测试用例执行超时，已超过300秒")
            results = {
                "total_cases": len(case_ids),
                "successful_cases": 0,
                "failed_cases": len(case_ids),
                "case_results": [
                    {
                        "case_id": case_id,
                        "case_name": "未知",
                        "status": "error",
                        "error": "测试用例执行超时"
                    } for case_id in case_ids
                ]
            }
        else:
            # 获取测试用例执行结果，设置超时
            try:
                success, result = result_queue.get(timeout=10)  # 10秒超时
                if success:
                    results = result
                else:
                    uat_logger.error(f"测试用例执行出错: {result}")
                    results = {
                        "total_cases": len(case_ids),
                        "successful_cases": 0,
                        "failed_cases": len(case_ids),
                        "case_results": [
                            {
                                "case_id": case_id,
                                "case_name": "未知",
                                "status": "error",
                                "error": f"执行出错: {result}"
                            } for case_id in case_ids
                        ]
                    }
            except queue.Empty:
                uat_logger.error("结果队列为空，无法获取执行结果")
                results = {
                    "total_cases": len(case_ids),
                    "successful_cases": 0,
                    "failed_cases": len(case_ids),
                    "case_results": [
                        {
                            "case_id": case_id,
                            "case_name": "未知",
                            "status": "error",
                            "error": "无法获取执行结果，可能是执行过程中发生异常"
                        } for case_id in case_ids
                    ]
                }
        
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
        # 确保浏览器关闭，无论测试用例执行结果如何
        try:
            # 注意：不要在这里调用 sync_close_browser，因为每个测试用例已经在自己的线程中关闭了浏览器
            # 这里主要是清理可能的全局实例
            uat_logger.info("多个测试用例执行完成，浏览器清理完成")
        except Exception as close_error:
            uat_logger.warning(f"清理浏览器时出错: {close_error}")
    
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
        import os
        import time
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
    steps = db.get_case_steps(case_id)
    return jsonify({'steps': steps})

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
    data = request.json
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
                selector_value = step.get('selector_value', '')
                input_value = step.get('input_value', '')
                description = step.get('description', '')
                # 添加iframe相关字段
                enter_iframe = step.get('enter_iframe', False)
                iframe_selector = step.get('iframe_selector', '')
                                        
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
                                    # 点击后等待页面响应
                                    sync_wait_for_timeout(2000)
                                except Exception as click_error:
                                    uat_logger.error(f"执行点击操作时出错: {click_error}")
                                    # 检查是否是页面关闭导致的错误
                                    if "closed" in str(click_error).lower():
                                        # 重新启动浏览器并导航到之前的URL
                                        uat_logger.info("页面已关闭,重新启动浏览器")
                                        sync_start_browser(headless=False)
                                        # 如果有目标URL，重新导航到该URL
                                        if case.get('url'):
                                            url = case['url']
                                            if url and url.strip():
                                                url = url.strip()
                                                if not url.startswith(('http://', 'https://')):
                                                    url = 'http://' + url
                                                sync_navigate_to(url)
                                    else:
                                        # 其他错误直接抛出
                                        raise
                elif action == 'input':
                    if selector_value and input_value:
                        sync_fill_input(selector_value, input_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                        # 输入后等待页面响应
                        sync_wait_for_timeout(1000)
                elif action == 'hover':
                    if selector_value:
                        sync_hover_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                        # 悬停后等待页面响应
                        sync_wait_for_timeout(1000)
                elif action == 'double_click':
                    if selector_value:
                        sync_double_click_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                        # 双击后等待页面响应
                        sync_wait_for_timeout(2000)
                elif action == 'right_click':
                    if selector_value:
                        sync_right_click_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                        # 右键点击后等待页面响应
                        sync_wait_for_timeout(1000)
                elif action == 'wait':
                    if selector_value:
                        sync_wait_for_selector(selector_value, selector_type=selector_type)
                elif action == 'scroll':
                            direction = 'down'
                            pixels = 500
                            try:
                                sync_scroll_page(direction, pixels, iframe_selector=iframe_selector if enter_iframe else None)
                                # 滚动后等待页面响应
                                sync_wait_for_timeout(1500)
                            except Exception as scroll_error:
                                uat_logger.error(f"执行滚动操作时出错: {scroll_error}")
                                # 检查是否是页面关闭导致的错误
                                if "closed" in str(scroll_error).lower():
                                    # 重新启动浏览器并导航到之前的URL
                                    uat_logger.info("页面已关闭,重新启动浏览器")
                                    sync_start_browser(headless=False)
                                    # 如果有目标URL，重新导航到该URL
                                    if case.get('url'):
                                        url = case['url']
                                        if url and url.strip():
                                            url = url.strip()
                                            if not url.startswith(('http://', 'https://')):
                                                url = 'http://' + url
                                            sync_navigate_to(url)
                                else:
                                    # 其他错误直接抛出
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
                            uat_logger.warning(f"提取文本失败: {extract_error}")
                            # 仅在提取失败时才将当前提取结果设为空，不影响之前的提取结果
                            current_extracted = ""
                        
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
                            uat_logger.warning(f"提取页面文本失败: {extract_error}")
                            current_extracted = ""
                        
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
                                # 如果没有提取到文本，且是text_compare操作，则跳过验证
                                if action == 'text_compare':
                                    uat_logger.warning("未提取到页面文本，跳过文本验证")
                                else:
                                    uat_logger.info("提取页面文本操作完成（未提取到文本）")
                        
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
                            uat_logger.warning(f"提取JSON数据失败: {extract_error}")
                    else:
                        uat_logger.warning("提取JSON数据时缺少选择器")
                    
                    # 提取后等待页面响应
                    sync_wait_for_timeout(1000)
            
            # 计算执行时间
            duration = round(time.time() - start_time, 2)
            
            uat_logger.info(f"测试用例 #{case_id} 运行成功，耗时: {duration}秒")
            
            # 保存运行历史记录
            try:
                db.create_run_history(case_id, 'success', duration, "", extracted_text, expected_text)
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
                'message': '测试用例运行成功'
            })
            
        except Exception as e:
            # 执行失败时的处理
            duration = round(time.time() - start_time, 2)
            uat_logger.error(f"测试用例 #{case_id} 运行失败: {str(e)}")
            
            # 保存运行历史记录
            try:
                db.create_run_history(case_id, 'error', duration, str(e), extracted_text, expected_text)
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

@app.route('/api/execute_multiple_cases', methods=['POST'])
@api_error_handler
@log_api_request
def execute_multiple_cases():
    """执行多个测试用例"""
    try:
        data = request.get_json(silent=True) or {}
        case_ids = data.get('case_ids', [])
        
        if not case_ids or not isinstance(case_ids, list):
            return jsonify({
                'success': False,
                'error': '请提供有效的测试用例ID列表'
            }), 400
        
        # 初始化数据库连接
        db = Database()
        
        # 执行结果
        case_results = []
        total_cases = len(case_ids)
        successful_cases = 0
        failed_cases = 0
        
        # 逐个执行测试用例
        for case_id in case_ids:
            try:
                # 获取测试用例信息
                case = db.get_test_case_v2(case_id)
                if not case:
                    case_results.append({
                        'case_id': case_id,
                        'case_name': f'用例 {case_id}',
                        'status': 'error',
                        'error': '测试用例不存在'
                    })
                    failed_cases += 1
                    continue
                
                # 获取测试步骤
                steps = db.get_case_steps(case_id)
                if not steps:
                    case_results.append({
                        'case_id': case_id,
                        'case_name': case['name'],
                        'status': 'error',
                        'error': '测试用例没有步骤'
                    })
                    failed_cases += 1
                    continue
                
                # 记录开始时间
                start_time = time.time()
                
                uat_logger.info(f"开始运行测试用例 #{case_id}: {case['name']}")
                uat_logger.info(f"测试用例共有 {len(steps)} 个步骤")
                
                # 提取的文本
                extracted_text = ""
                # 预期结果
                expected_text = ""
                
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
                        selector_value = step.get('selector_value', '')
                        input_value = step.get('input_value', '')
                        description = step.get('description', '')
                        # 添加iframe相关字段
                        enter_iframe = step.get('enter_iframe', False)
                        iframe_selector = step.get('iframe_selector', '')
                                                
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
                                sync_click_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                                # 点击后等待页面响应
                                sync_wait_for_timeout(2000)
                        elif action == 'input':
                            if selector_value and input_value:
                                sync_fill_input(selector_value, input_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                                # 输入后等待页面响应
                                sync_wait_for_timeout(1000)
                        elif action == 'hover':
                            if selector_value:
                                sync_hover_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                                # 悬停后等待页面响应
                                sync_wait_for_timeout(1000)
                        elif action == 'double_click':
                            if selector_value:
                                sync_double_click_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                                # 双击后等待页面响应
                                sync_wait_for_timeout(2000)
                        elif action == 'right_click':
                            if selector_value:
                                sync_right_click_element(selector_value, selector_type, iframe_selector=iframe_selector if enter_iframe else None)
                                # 右键点击后等待页面响应
                                sync_wait_for_timeout(1000)
                        elif action == 'wait':
                            if selector_value:
                                sync_wait_for_selector(selector_value, selector_type=selector_type)
                        elif action == 'scroll':
                            direction = 'down'
                            pixels = 500
                            try:
                                sync_scroll_page(direction, pixels, iframe_selector=iframe_selector if enter_iframe else None)
                                # 滚动后等待页面响应
                                sync_wait_for_timeout(1500)
                            except Exception as scroll_error:
                                uat_logger.error(f"执行滚动操作时出错: {scroll_error}")
                                # 检查是否是页面关闭导致的错误
                                if "closed" in str(scroll_error).lower():
                                    # 重新启动浏览器并导航到之前的URL
                                    uat_logger.info("页面已关闭,重新启动浏览器")
                                    sync_start_browser(headless=False)
                                    # 如果有目标URL，重新导航到该URL
                                    if case.get('url'):
                                        url = case['url']
                                        if url and url.strip():
                                            url = url.strip()
                                            if not url.startswith(('http://', 'https://')):
                                                url = 'http://' + url
                                            sync_navigate_to(url)
                                else:
                                    # 其他错误直接抛出
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
                                    uat_logger.warning(f"提取文本失败: {extract_error}")
                                    # 仅在提取失败时才将当前提取结果设为空，不影响之前的提取结果
                                    current_extracted = ""
                                
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
                                    uat_logger.warning(f"提取页面文本失败: {extract_error}")
                                    current_extracted = ""
                                
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
                                        # 如果没有提取到文本，且是text_compare操作，则跳过验证
                                        if action == 'text_compare':
                                            uat_logger.warning("未提取到页面文本，跳过文本验证")
                        else:
                            uat_logger.warning(f"未知操作类型: {action}")
                    
                    # 记录结束时间
                    end_time = time.time()
                    duration = round(end_time - start_time, 2)
                    
                    uat_logger.info(f"测试用例 #{case_id} 运行成功，耗时: {duration}秒")
                    
                    # 保存运行结果到数据库
                    db = Database()
                    db.add_run_history(
                        case_id=case_id,
                        status='passed',
                        duration=duration,
                        extracted_text=extracted_text,
                        expected_text=expected_text
                    )
                    
                    # 添加到结果列表
                    case_results.append({
                        'case_id': case_id,
                        'case_name': case['name'],
                        'status': 'success',
                        'duration': duration
                    })
                    successful_cases += 1
                    
                except Exception as e:
                    # 记录结束时间
                    end_time = time.time()
                    duration = round(end_time - start_time, 2)
                    
                    uat_logger.error(f"运行测试用例 #{case_id} 时发生错误: {str(e)}")
                    
                    # 保存失败结果到数据库
                    db = Database()
                    db.add_run_history(
                        case_id=case_id,
                        status='failed',
                        duration=duration,
                        extracted_text=extracted_text,
                        expected_text=expected_text,
                        error=str(e)
                    )
                    
                    # 添加到结果列表
                    case_results.append({
                        'case_id': case_id,
                        'case_name': case['name'],
                        'status': 'error',
                        'error': str(e),
                        'duration': duration
                    })
                    failed_cases += 1
                    
                finally:
                    # 关闭浏览器
                    try:
                        sync_close_browser()
                    except:
                        pass
            except Exception as e:
                uat_logger.error(f"执行测试用例 #{case_id} 时发生错误: {str(e)}")
                case_results.append({
                    'case_id': case_id,
                    'case_name': f'用例 {case_id}',
                    'status': 'error',
                    'error': str(e)
                })
                failed_cases += 1
                
        # 构建响应结果
        results = {
            'total_cases': total_cases,
            'successful_cases': successful_cases,
            'failed_cases': failed_cases,
            'case_results': case_results
        }
        
        return jsonify({
            'success': True,
            'results': results
        })
        
    except Exception as e:
        uat_logger.error(f"执行多个测试用例时发生错误: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)