# AI 自主测试工作流断裂修复计划

## 摘要

经过 4 个并行审计 agent 对 `ai_chat_tool_loop.py`、`app.py` AI 接口、10 个现有 AI 模块、`ai_test.html` 前端的完整代码审计，确认以下事实：

1. **我之前的修改是表面功夫**：添加了 `_ai_task_abort_events` 全局字典和停止接口逻辑，但 `job_id` 从未定义导致整个机制抛 NameError 后空转
2. **停止按钮从前端到后端全链路断裂**：前端不传 job_id、不中断 SSE 流；后端字典永远为空
3. **自动启动浏览器是"伪成功"**：`init_result` 赋值后从未检查，失败也报"已启动"
4. **乱码修复不完整**：`resp.encoding = 'utf-8'` 在 `resp.json()` 之前设置无效，因为 `json()` 内部会用 `resp.text` 的编码
5. **现有 10 个 AI 模块中 0 个被 `ai_chat_tool_loop.py` 导入**，工具循环完全脱离已有能力

## 当前状态分析（基于代码审计）

### 问题 1：`job_id` 未定义 → 中止机制完全空转

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py)

`api_ai_task_execute()` 函数体（第 5939-5956 行）只定义了 `task / project_id / platform / engine / url / enable_vision / timeout`，从未生成 `job_id`。但 `_gen()` 第 5969 行引用了它：

```python
with _ai_task_abort_lock:
    _ai_task_abort_events[job_id] = abort_event  # NameError!
```

**后果链**：
- 生成器首次迭代到 5969 行 → `NameError: name 'job_id' is not defined`
- 被 6176 行 `except Exception` 捕获 → yield 一条 error 事件
- `finally` 块 6179 行 `_ai_task_abort_events.pop(job_id, None)` → 再次 NameError
- 字典始终为空 → 停止接口遍历 0 次 → 返回 success 但什么都没停

### 问题 2：前端停止按钮全链路断裂

**文件**: [ai_test.html](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/templates/ai_test.html)

| 断裂点 | 行号 | 现状 |
|--------|------|------|
| `aiStopExecution()` 不传 job_id | 1461-1474 | `fetch('/api/ai/task/execute/stop', { method: 'POST' })` 无 body |
| `aiStreamExecute()` 不捕获 job_id | 1313-1346 | 直接 `readSSEStream(response.body)`，不解析响应头/首事件 |
| `AI_TEST_STATE` 无 jobId 字段 | 1016-1026 | 只有 isExecuting / thinkSteps / actions 等 |
| 无 AbortController | 全文 | grep 无匹配，SSE 流无法前端中断 |
| `pump()` 不检查 isExecuting | 1356-1385 | 停止后仍继续读取和处理事件 |

### 问题 3：abort_event 在长同步调用期间不检查

**文件**: [ai_chat_tool_loop.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_chat_tool_loop.py)

- 第 648-650 行：每轮循环开头检查 `_abort.is_set()` ✓
- 第 398 行 `_handle_agent_execute`：**不接收 abort_event 参数**，`agent_client.execute_user_instruction()` 同步阻塞数十秒到数分钟，期间无法中止
- 第 506/560/703/755 行 `refine_case_and_steps`：同样不传 abort_event
- [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) `_gen()` 内部：bootstrap、init_result、降级 start_browser 期间均不检查

### 问题 4：自动启动浏览器"伪成功"

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) 第 5988-5998 行

```python
init_result = hermes_client.execute_user_instruction(
    "[INIT] 请启动浏览器并准备接收后续指令",
    session_id="init_" + str(_time.time())
)
yield send('think', text='浏览器已启动', status='done')  # 无论结果都报成功
```

- `init_result` 赋值后从未检查
- 失败时只打 warning，不向前端 yield error
- session_id 每次新建，不复用

### 问题 5：乱码修复不完整

**文件**: [hermes_gateway_client.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/hermes_gateway_client.py) 第 107-111 行

我之前添加的 `resp.encoding = 'utf-8'` 位置在 `resp.json()` 之前，但 `requests` 的 `json()` 方法内部调用 `self.text`，而 `text` 属性的 encoding 在 `resp.encoding` 赋值后确实会生效——**但问题在于 `json()` 使用的是 `json.loads(resp.text)`**，如果响应头没有声明 charset，`requests` 默认按 ISO-8859-1 解码。

正确修复方式：不用 `resp.json()`，改为 `json.loads(resp.content.decode('utf-8', errors='replace'))`，直接用原始字节。

### 问题 6：降级模式 case_steps 是占位伪数据

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) 第 6138-6175 行

Hermes 不可用时的降级路径硬编码了"点击/输入/等待/断言"四步，与实际用户任务完全无关。

### 问题 7：`/api/ai/task/execute/logs` 是桩接口

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) 第 6206-6210 行

永远返回 `{'success': True, 'logs': []}`。

### 问题 8：`hermes_execute_allowed()` 死代码 + 快照问题

**文件**: [ai_chat_tool_loop.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_chat_tool_loop.py) 第 67-95 行

- 第 93-94 行 `if embedded_session_id and hermes_cdp_attached()` 是死代码（L84 已判断过）
- `allow_agent` 在循环外一次性快照（L617），循环中不更新。用户中途连接浏览器后，LLM 仍收到"禁止调 hermes_execute"的旧 prompt

## 修复方案

### 修改 1：后端生成 job_id 并通过 SSE 推送

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py)

**位置**: `api_ai_task_execute()` 函数体，`def _gen():` 之前

```python
import uuid
job_id = uuid.uuid4().hex
```

**位置**: `_gen()` 内部，`abort_event` 创建之后、注册之前

```python
abort_event = threading.Event()

with _ai_task_abort_lock:
    _ai_task_abort_events[job_id] = abort_event

# 在第一个 yield 之前推送 job_id
yield send('job_started', job_id=job_id)
```

**为什么**: 修复 NameError 导致的空转；让前端能拿到 job_id 用于停止请求。

### 修改 2：前端捕获 job_id 并在停止时回传

**文件**: [ai_test.html](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/templates/ai_test.html)

**2a**: `AI_TEST_STATE` 添加 `jobId` 字段（第 1016-1026 行附近）：
```javascript
var AI_TEST_STATE = {
    isExecuting: false,
    jobId: null,          // 新增
    isScreenSharing: false,
    // ...
};
```

**2b**: `handleStreamEvent` 添加 `job_started` 事件处理（第 1389 行附近）：
```javascript
function handleStreamEvent(ev, progressCb) {
    if (!ev || !ev.type) return;
    if (ev.type === 'job_started') {
        AI_TEST_STATE.jobId = ev.job_id || null;
        return;
    }
    // ... 现有逻辑
}
```

**2c**: `aiStopExecution()` 传递 job_id（第 1461 行）：
```javascript
function aiStopExecution() {
    AI_TEST_STATE.isExecuting = false;
    fetch('/api/ai/task/execute/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: AI_TEST_STATE.jobId })
    })
    .then(function() {
        hideTyping();
        setStatus('error', '已停止');
        addChatMessage('任务已停止');
        saveChatHistory();
        finishExecution();
    })
    .catch(function() { finishExecution(); });
}
```

**2d**: `aiStreamExecute` 添加 AbortController（第 1313 行附近）：
```javascript
function aiStreamExecute(task) {
    var payload = JSON.stringify({ task, project_id, platform, engine, url, enable_vision, timeout });
    AI_TEST_STATE.abortController = new AbortController();
    fetch('/api/ai/task/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        signal: AI_TEST_STATE.abortController.signal
    }).then(function(response) {
        if (!response.ok || !response.body) { /* ... */ return null; }
        return readSSEStream(response.body);
    })/* ... */;
}
```

**2e**: `aiStopExecution` 中调用 abort（第 1461 行）：
```javascript
function aiStopExecution() {
    AI_TEST_STATE.isExecuting = false;
    if (AI_TEST_STATE.abortController) {
        try { AI_TEST_STATE.abortController.abort(); } catch(e) {}
        AI_TEST_STATE.abortController = null;
    }
    fetch('/api/ai/task/execute/stop', {
        // ... 同上
    })/* ... */;
}
```

**2f**: `readSSEStream` 的 `pump()` 添加 isExecuting 检查（第 1356 行附近）：
```javascript
function pump() {
    if (!AI_TEST_STATE.isExecuting) return;
    return reader.read().then(function(result) {
        if (result.done) { /* ... */ return; }
        // ... 现有解码和处理逻辑
        return pump();
    });
}
```

**2g**: `finishExecution` 清理 abortController 和 jobId：
```javascript
function finishExecution() {
    AI_TEST_STATE.isExecuting = false;
    AI_TEST_STATE.jobId = null;
    AI_TEST_STATE.abortController = null;
    // ... 现有 UI 重置
}
```

### 修改 3：后端 `_gen()` 在关键阻塞点检查 abort_event

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py)

在 `_gen()` 内部，以下位置添加 abort 检查：

**3a**: bootstrap 之后（第 5983 行之后）：
```python
if abort_event.is_set():
    yield send('error', error='任务已取消')
    return
```

**3b**: 自动启动浏览器之前（第 5990 行之前）：
```python
if abort_event.is_set():
    yield send('error', error='任务已取消')
    return
```

**3c**: 工具循环流式调用之前（第 6004 行之前）：
```python
if abort_event.is_set():
    yield send('error', error='任务已取消')
    return
```

### 修改 4：`_handle_agent_execute` 增加 abort 检查

**文件**: [ai_chat_tool_loop.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_chat_tool_loop.py)

**4a**: 修改 `_handle_agent_execute` 签名，增加 `abort_event` 参数（第 360 行）：
```python
def _handle_agent_execute(
    *,
    name: str,
    args: Dict[str, Any],
    allow_agent: bool,
    agent_client: Any,
    meta: Dict[str, Any],
    abort_event: Optional[threading.Event] = None,
) -> str:
```

**4b**: 在 `execute_user_instruction` 调用前检查（第 390 行之前）：
```python
if abort_event is not None and abort_event.is_set():
    return json.dumps({"ok": False, "error": "操作已被用户取消"}, ensure_ascii=False)
```

**4c**: 调用处传参（第 540-546 行和第 742-748 行）：
```python
result_text = _handle_agent_execute(
    name=name,
    args=args,
    allow_agent=allow_agent,
    agent_client=agent_client,
    meta=meta,
    abort_event=_abort,
)
```

### 修改 5：修复自动启动浏览器"伪成功"

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) 第 5988-5998 行

```python
if hermes_available:
    yield send('think', text='Hermes 智能体已就绪', status='done')
    try:
        from hermes_config import hermes_cdp_attached
        if not hermes_cdp_attached():
            yield send('think', text='正在启动浏览器...', status='running')
            init_result = hermes_client.execute_user_instruction(
                "[INIT] 请启动浏览器并准备接收后续指令",
                session_id="init_" + str(_time.time())
            )
            # 检查返回结果
            if init_result and '"ok": false' in init_result.lower():
                yield send('think', text='浏览器启动失败: ' + init_result[:200], status='warning')
            else:
                yield send('think', text='浏览器已启动', status='done')
    except Exception as e:
        uat_logger.warning("自动启动浏览器失败: %s", e)
        yield send('think', text='浏览器自动启动失败: ' + str(e)[:200], status='warning')
```

### 修改 6：修复乱码问题

**文件**: [hermes_gateway_client.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/hermes_gateway_client.py) 第 107-111 行

```python
resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
if not resp.ok:
    raise ValueError(_http_error_detail(resp))
# 不用 resp.json()，直接从原始字节解码，避免 requests 按 ISO-8859-1 解码
raw_text = resp.content.decode('utf-8', errors='replace')
data = json.loads(raw_text) if raw_text.strip() else {}
```

### 修改 7：删除降级模式伪数据 + 日志桩接口

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py)

**7a**: 降级模式（第 6138-6175 行）：当 Hermes 不可用时，不再硬编码伪 case_steps，改为直接报错引导用户：
```python
else:
    yield send('error', error='Hermes 智能体不可用且内置浏览器模式未集成。请先启动 Hermes Gateway 或连接浏览器后重试。')
```

**7b**: `/api/ai/task/execute/logs`（第 6206-6210 行）：保持现状但注释标明是占位，避免误导（不删除路由以免前端报 404）。

### 修改 8：清理 `hermes_execute_allowed()` 死代码

**文件**: [ai_chat_tool_loop.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_chat_tool_loop.py) 第 93-94 行

删除死代码分支：
```python
# 删除这两行（hermes_cdp_attached() 在 L84 已判断过，走到这里必为 False）
if (embedded_session_id or "").strip() and hermes_cdp_attached():
    return True
```

## 不做的事情（避免过度工程）

1. **不重构 `ai_chat_tool_loop.py` 的整体架构** — 工具循环逻辑本身是完整的，只是中止信号传递有缺口
2. **不导入 10 个现有 AI 模块到 `ai_chat_tool_loop.py`** — 这些模块由执行层（playwright_automation 等）间接消费，是正确的分层设计，不是缺陷
3. **不实现 `/api/ai/task/execute/logs` 的真实日志** — 当前前端不依赖这个接口返回的数据，优先级低
4. **不修改 system prompt 的动态构建逻辑** — CDP 状态快照问题影响有限（用户通常在执行前就连好浏览器），优先级低于核心链路修复
5. **不修改 `allow_agent` 为每轮重新评估** — 同上，优先级低

## 假设与决策

| 决策 | 理由 |
|------|------|
| 用 `uuid.uuid4().hex` 生成 job_id | 与项目已有的 `_AI_JOB_ABORT_EVENTS` 路径（app.py:4135）保持一致 |
| 通过 SSE 首事件推送 job_id 而非响应头 | SSE 流式响应不适合设置自定义头（已开始发送 body 后无法改头） |
| 用 `AbortController` 中断 SSE 流 | Web 标准 API，fetch 原生支持，无需引入新依赖 |
| 降级模式直接报错而非生成伪步骤 | 伪数据比报错更糟糕——用户以为有结果但实际无用 |
| 不删除 logs 桩接口 | 前端可能有调用，删除会 404；保持返回空数组无害 |

## 验证步骤

1. `python -m py_compile app.py hermes_gateway_client.py ai_chat_tool_loop.py` — 语法检查
2. 启动应用，在 AI 自主测试页执行任务
3. 验证 SSE 首事件包含 `job_id`
4. 验证任务执行中点击停止按钮 → 任务立即停止，SSE 流中断
5. 验证 AI 输出中文不再乱码
6. 验证浏览器未连接时不再显示"浏览器已启动"伪成功
7. 验证 Hermes 不可用时不再生成伪 case_steps，而是给出明确错误提示
