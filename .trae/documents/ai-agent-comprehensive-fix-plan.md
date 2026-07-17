# AI Agent 工作流综合修复计划

## 问题分析

### 1. AI 输出乱码

**现象**: 聊天记录中出现 `Ã¥Â¤Â§Ã¥Â¥Â½` 等乱码字符

**根因**: UTF-8 编码的中文被错误地解码为 Latin-1 (ISO-8859-1)

**位置**: 
- `hermes_gateway_client.py` 第 107-116 行：`requests.post` 返回的响应使用 `resp.json()` 时可能未正确处理 UTF-8 编码
- `ai_chat_tool_loop.py` 第 650-663 行：流式调用 `dispatch_chat_stream` 的错误处理

**证据**: 
- 乱码模式 `Ã¥Â¤Â§Ã¥Â¥Â½` 是典型的 UTF-8 → Latin-1 错误解码
- `requests` 的 `json()` 方法在某些情况下会使用响应的 `Content-Type` 头中的 charset，如果未指定则可能使用默认编码

### 2. 停止按钮无效

**现象**: 点击停止按钮后任务继续运行

**根因**: `abort_event` 是 SSE 生成器内部的局部变量，停止接口无法访问

**位置**: 
- `app.py` 第 5964 行：`abort_event = threading.Event()` 在 `_gen()` 内部定义
- `app.py` 第 6194-6198 行：`/api/ai/task/execute/stop` 接口只是返回成功，没有实际停止逻辑

**证据**: 停止接口代码：
```python
@app.route('/api/ai/task/execute/stop', methods=['POST'])
@login_required
@api_error_handler
def api_ai_task_execute_stop():
    return jsonify({'success': True})  # 没有任何停止逻辑！
```

### 3. 浏览器没有自动打开

**现象**: 用户选择 Web 浏览器模式，但 Agent 没有打开浏览器执行操作

**根因**: 
1. `hermes_execute_allowed()` 检查过于严格，在嵌入式网关未启用时虽然返回 True，但实际上没有浏览器可用
2. 系统没有自动启动浏览器并建立 CDP 连接的机制
3. 用户必须手动连接"实时画面"才能触发 CDP attach

**位置**: 
- `ai_chat_tool_loop.py` 第 67-87 行：`hermes_execute_allowed()` 函数
- `app.py` 第 5987-5988 行：当 `hermes_available` 为 True 时直接使用工具循环，但没有检查浏览器是否已连接

## 修复方案

### 文件修改

#### 文件 1: `hermes_gateway_client.py` - 修复乱码问题

**修改点**: 在 `_chat_completions` 方法中确保正确处理 UTF-8 编码

```python
# 修改前
resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
if not resp.ok:
    raise ValueError(_http_error_detail(resp))
data = resp.json() if resp.content else {}

# 修改后
resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
resp.encoding = 'utf-8'  # 强制使用 UTF-8 编码
if not resp.ok:
    raise ValueError(_http_error_detail(resp))
data = resp.json() if resp.content else {}
```

#### 文件 2: `app.py` - 修复停止按钮无效问题

**修改点 1**: 在全局作用域定义线程安全的中止事件存储

```python
# 在文件顶部（import 之后）添加
import threading
_ABORT_EVENTS: Dict[str, threading.Event] = {}
_ABORT_EVENTS_LOCK = threading.Lock()
```

**修改点 2**: 在 `api_ai_task_execute` 中注册中止事件

```python
# 在 _gen() 函数内部，abort_event = threading.Event() 之后
abort_event = threading.Event()

# 添加注册逻辑
with _ABORT_EVENTS_LOCK:
    _ABORT_EVENTS[job_id] = abort_event

try:
    # ... 现有逻辑 ...
finally:
    with _ABORT_EVENTS_LOCK:
        _ABORT_EVENTS.pop(job_id, None)
```

**修改点 3**: 修改停止接口实现

```python
# 修改前
@app.route('/api/ai/task/execute/stop', methods=['POST'])
@login_required
@api_error_handler
def api_ai_task_execute_stop():
    return jsonify({'success': True})

# 修改后
@app.route('/api/ai/task/execute/stop', methods=['POST'])
@login_required
@api_error_handler
def api_ai_task_execute_stop():
    data = request.get_json(silent=True) or {}
    job_id = data.get('job_id')
    
    with _ABORT_EVENTS_LOCK:
        if job_id and job_id in _ABORT_EVENTS:
            _ABORT_EVENTS[job_id].set()
            return jsonify({'success': True, 'message': '已发送停止信号'})
        # 如果没有 job_id，尝试停止所有任务
        for evt in _ABORT_EVENTS.values():
            evt.set()
        _ABORT_EVENTS.clear()
    
    return jsonify({'success': True, 'message': '已发送停止信号'})
```

#### 文件 3: `ai_chat_tool_loop.py` - 修复浏览器未自动打开问题

**修改点**: 修改 `hermes_execute_allowed()` 函数，在 Web 平台且没有 CDP 连接时自动允许执行（依赖 Hermes Agent 自身启动浏览器）

```python
# 修改前
def hermes_execute_allowed(*, embedded_session_id: str = "", platform_type: str = "web") -> bool:
    plat = (platform_type or "web").strip().lower()
    if plat == "desktop":
        from agent_gateway_client import agent_gateway_configured
        return agent_gateway_configured()
    if plat == "android":
        return False
    if not embedded_gateway_enabled():
        return True
    if _ai_allow_main_playwright_fallback():
        return True
    if hermes_cdp_attached():
        return True
    if (embedded_session_id or "").strip() and hermes_cdp_attached():
        return True
    return False

# 修改后
def hermes_execute_allowed(*, embedded_session_id: str = "", platform_type: str = "web") -> bool:
    plat = (platform_type or "web").strip().lower()
    if plat == "desktop":
        from agent_gateway_client import agent_gateway_configured
        return agent_gateway_configured()
    if plat == "android":
        return False
    # Web 平台：只要 Hermes Gateway 可用，就允许执行
    # Hermes Agent 自身会负责启动浏览器并建立 CDP 连接
    if not embedded_gateway_enabled():
        return True
    if _ai_allow_main_playwright_fallback():
        return True
    if hermes_cdp_attached():
        return True
    # 新增：如果没有 CDP 连接但平台是 web，且 Hermes 已配置，允许执行
    # 让 Hermes Agent 自行启动浏览器
    try:
        from hermes_gateway_client import HermesGatewayClient
        client = HermesGatewayClient()
        if client.is_configured() and client.health_check(timeout_sec=2.0):
            return True
    except Exception:
        pass
    if (embedded_session_id or "").strip() and hermes_cdp_attached():
        return True
    return False
```

#### 文件 4: `app.py` - 添加自动启动浏览器逻辑

**修改点**: 在 `api_ai_task_execute` 中，当检测到没有 CDP 连接时，自动尝试启动浏览器

```python
# 在 hermes_available 检查之后添加
if hermes_available:
    yield send('think', text='Hermes 智能体已就绪', status='done')
    
    # 检查 CDP 连接状态，如果没有连接，尝试启动浏览器
    try:
        from hermes_config import hermes_cdp_attached
        if not hermes_cdp_attached():
            yield send('think', text='正在启动浏览器...', status='running')
            # 触发 Hermes Agent 启动浏览器
            from hermes_gateway_client import HermesGatewayClient
            client = HermesGatewayClient()
            # 发送一个初始化指令让 Hermes 启动浏览器
            init_result = client.execute_user_instruction(
                "[INIT] 请启动浏览器并准备接收后续指令",
                session_id="init_" + str(_time.time())
            )
            yield send('think', text='浏览器已启动', status='done')
    except Exception as e:
        uat_logger.warning("自动启动浏览器失败: %s", e)
        yield send('think', text='浏览器自动启动失败，请手动连接', status='warning')
else:
    yield send('think', text='Hermes 智能体不可用，将使用内置浏览器模式', status='warning')
```

## 风险评估

| 风险 | 等级 | 说明 | 缓解措施 |
|------|------|------|----------|
| UTF-8 强制编码导致其他问题 | 低 | 大多数 API 返回都是 UTF-8 | 保留异常处理，如有问题可回退 |
| 全局中止事件线程安全 | 中 | 多个用户同时执行时可能冲突 | 使用 Lock 保护，按 job_id 区分 |
| 自动启动浏览器失败 | 中 | Hermes Agent 可能未正确配置 | 添加错误处理和警告提示 |
| 浏览器启动超时 | 低 | 首次启动可能较慢 | 设置合理超时时间 |

## 验证步骤

1. 运行语法检查：`python -m py_compile app.py hermes_gateway_client.py ai_chat_tool_loop.py`
2. 启动应用并测试 AI 自主测试功能
3. 验证乱码问题是否解决
4. 验证停止按钮是否能正常停止任务
5. 验证浏览器是否能自动打开

## 预期结果

- AI 输出中的中文正常显示，不再出现乱码
- 点击停止按钮后任务立即停止
- 选择 Web 浏览器模式后，系统自动启动浏览器执行操作
