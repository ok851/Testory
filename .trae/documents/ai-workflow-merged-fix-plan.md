# AI 自主测试工作流整合修复计划（合并版）

## 摘要

本计划合并了两份方案：(A) 基于代码审计的 bug 修复方案（修复断裂的 job_id 链路、停止按钮、乱码、伪成功等），(B) 基于现有模块整合的架构方案（外部浏览器桥接、动作记录器、规范化管线接入、视觉 stub 实现、前端中栏替换）。

**核心原则**：先复用后新增、修复优先于扩展、每一步对应到具体文件和函数。

---

## Phase 0：核心 Bug 修复（前置条件）

> 这些是我之前修改引入的断裂问题，必须先修复，否则后续所有功能都建立在空转的基础上。

### 0.1 后端生成 job_id 并通过 SSE 推送

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py)

**问题**: `api_ai_task_execute()` 函数体（第 5939-5956 行）从未生成 `job_id`，但 `_gen()` 第 5969 行引用了它 → `NameError` → 整个 abort 机制空转。

**修改**:

1. 在 `def _gen():` 之前（第 5956 行之后）添加：
```python
import uuid
job_id = uuid.uuid4().hex
```

2. 在 `_gen()` 内部，`abort_event = threading.Event()` 之后，添加首事件推送：
```python
abort_event = threading.Event()
with _ai_task_abort_lock:
    _ai_task_abort_events[job_id] = abort_event
yield send('job_started', job_id=job_id)
```

### 0.2 前端捕获 job_id + AbortController + 停止时回传

**文件**: [ai_test.html](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/templates/ai_test.html)

**问题**: 前端不传 job_id（第 1463 行 fetch 无 body）、不捕获 job_id、无 AbortController、`pump()` 不检查 isExecuting。

**修改**:

1. `AI_TEST_STATE` 添加 `jobId` 和 `abortController` 字段（第 1016 行附近）
2. `handleStreamEvent` 添加 `job_started` 事件处理（第 1389 行附近）
3. `aiStreamExecute` 添加 `AbortController`（第 1313 行附近）
4. `aiStopExecution` 传递 job_id + 调用 abort（第 1461 行）
5. `readSSEStream` 的 `pump()` 添加 `isExecuting` 检查（第 1356 行附近）
6. `finishExecution` 清理 jobId 和 abortController（第 1449 行附近）

### 0.3 后端 `_gen()` 关键阻塞点检查 abort_event

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py)

**问题**: `_gen()` 内部从不检查 `abort_event.is_set()`，bootstrap、init、降级 start_browser 期间均无法中止。

**修改**: 在以下位置添加 `if abort_event.is_set(): yield send('error', error='任务已取消'); return`：
- bootstrap 之后（第 5983 行之后）
- 自动启动浏览器之前（第 5990 行之前）
- 工具循环流式调用之前（第 6004 行之前）

### 0.4 `_handle_agent_execute` 增加 abort 检查

**文件**: [ai_chat_tool_loop.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_chat_tool_loop.py)

**问题**: `_handle_agent_execute`（第 360 行）不接收 abort_event，`execute_user_instruction` 同步阻塞数十秒到数分钟，期间无法中止。

**修改**:
1. 函数签名增加 `abort_event: Optional[threading.Event] = None` 参数
2. 在 `execute_user_instruction` 调用前检查 `abort_event.is_set()`
3. 调用处（第 540 行、第 742 行）传参 `abort_event=_abort`

### 0.5 修复乱码问题

**文件**: [hermes_gateway_client.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/hermes_gateway_client.py)

**问题**: 我之前添加的 `resp.encoding = 'utf-8'` 在 `resp.json()` 之前设置，但 `requests` 的 `json()` 内部调用 `json.loads(resp.text)`，当响应头未声明 charset 时 `text` 仍按 ISO-8859-1 解码。

**修改**（第 107-111 行）：
```python
resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
if not resp.ok:
    raise ValueError(_http_error_detail(resp))
raw_text = resp.content.decode('utf-8', errors='replace')
data = json.loads(raw_text) if raw_text.strip() else {}
```

### 0.6 修复自动启动浏览器"伪成功"

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) 第 5988-5998 行

**问题**: `init_result` 赋值后从未检查，失败也报"已启动"。

**修改**: 检查返回结果，失败时 yield warning 而非 done。

### 0.7 删除降级模式伪 case_steps

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) 第 6138-6175 行

**问题**: Hermes 不可用时硬编码"点击/输入/等待/断言"四步，与实际任务无关。

**修改**: 替换为明确错误提示，引导用户启动 Hermes 或连接浏览器。

### 0.8 清理 `hermes_execute_allowed()` 死代码

**文件**: [ai_chat_tool_loop.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_chat_tool_loop.py) 第 93-94 行

**修改**: 删除 `if embedded_session_id and hermes_cdp_attached()` 死代码分支（L84 已判断过，走到这里必为 False）。

---

## Phase 1：外部浏览器桥接（核心基础设施）

> 替代嵌入式画布，提供有头浏览器 + CDP 连接 + 页面快照 + DOM 探测，让 Hermes 通过 CDP attach 操作同一浏览器。

### 1.1 新建 `ai_external_browser_bridge.py`

**职责**: 启动有头 Playwright Chromium + 暴露 CDP + 页面快照 + 截图 + DOM 探测对接

**CDP 获取方案**（不用私有属性，用 DevTools HTTP API）：
```python
def ensure_browser(*, headless: bool = False, url: str = "") -> bool:
    """启动有头浏览器，通过 DevTools /json/version 获取 CDP WebSocket URL"""
    global _browser, _context, _page, _cdp_ws
    with _bridge_lock:
        if _page and not _page.is_closed():
            if url:
                try: _page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception: pass
            return True
        try:
            from playwright.sync_api import sync_playwright
            import requests
            _pw = sync_playwright().start()
            _browser = _pw.chromium.launch(
                headless=headless,
                args=["--remote-debugging-port=9222", "--disable-blink-features=AutomationControlled"],
            )
            _context = _browser.new_context(viewport={"width": 1280, "height": 800})
            _page = _context.new_page()
            # 通过 DevTools HTTP API 获取 CDP WebSocket URL（不用私有属性）
            resp = requests.get("http://127.0.0.1:9222/json/version", timeout=5)
            _cdp_ws = resp.json().get("webSocketDebuggerUrl", "")
            if _cdp_ws:
                from hermes_config import sync_hermes_cdp_endpoint
                sync_hermes_cdp_endpoint(_cdp_ws, restart_gateway=False)
            if url:
                _page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return True
        except Exception as e:
            from logger import uat_logger
            uat_logger.warning("ExternalBrowserBridge 启动失败: %s", e)
            return False
```

**页面快照/DOM 探测适配**：
`ai_page_probe.collect_page_controls(url)` 接受 URL 而非 Page 对象。桥接层提供适配函数：
```python
def get_page_snapshot() -> str:
    """复用 ai_page_probe 的快照逻辑，但操作已启动的 Page 对象"""
    if not _page or _page.is_closed():
        return ""
    try:
        # 直接在已有 Page 上执行 JS 收集交互元素（复用 ai_page_probe._COLLECT_INTERACTIVE_JS）
        from ai_page_probe import _COLLECT_INTERACTIVE_JS, _format_summary_lines
        snap_data = _page.evaluate(_COLLECT_INTERACTIVE_JS)
        title = _page.title()
        url = _page.url
        # 构建注册表
        registry = _build_registry_from_snap(snap_data)
        text = _format_summary_lines(title, url, registry, max_lines=90, max_chars=18000)
        return text or ""
    except Exception:
        try: return f"URL: {_page.url}\nTitle: {_page.title()}"
        except Exception: return ""
```

**导出函数**:
- `ensure_browser(headless, url) -> bool` — 启动浏览器 + CDP 同步
- `get_cdp_ws() -> str` — 获取 CDP WebSocket URL
- `get_page_snapshot() -> str` — 页面快照（供 `_build_system_prompt` 使用）
- `get_probe_registry() -> list` — probe 注册表（供 `ai_locator_resolution` 使用）
- `get_dom_context_pack() -> str` — DOM 上下文（供 `_build_system_prompt` 使用）
- `capture_screenshot() -> bytes` — 截图 PNG bytes
- `cleanup()` — 关闭浏览器 + 清除 CDP 配置

### 1.2 修改 `/api/ai/task/execute` SSE 集成桥接层

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) 第 5985-6002 行

**修改**: 替换 Phase 0.6 的"自动启动浏览器"逻辑为 ExternalBrowserBridge：
```python
if hermes_available:
    yield send('think', text='Hermes 智能体已就绪', status='done')
    from ai_external_browser_bridge import ensure_browser, get_page_snapshot, get_probe_registry, get_dom_context_pack
    from hermes_config import hermes_cdp_attached
    if not hermes_cdp_attached():
        yield send('think', text='正在启动有头浏览器...', status='running')
        browser_ready = ensure_browser(headless=False, url=url or "")
        if browser_ready:
            yield send('think', text='浏览器已启动，CDP 已同步到 Hermes', status='done')
        else:
            yield send('think', text='浏览器启动失败，使用无画布模式', status='warning')
```

**修改 ChatToolLoopParams 传参**（第 6008-6025 行附近）：
```python
params = ChatToolLoopParams(
    message=task,
    # ...
    page_snapshot=get_page_snapshot() if browser_ready else "",
    probe_registry=get_probe_registry() if browser_ready else None,
    probe_url=url or "",
    dom_context_pack=get_dom_context_pack() if browser_ready else "",
    # ...
)
```

### 1.3 复用的现有模块

| 模块 | 函数 | 用途 |
|------|------|------|
| [hermes_config.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/hermes_config.py#L225) | `sync_hermes_cdp_endpoint(ws, restart_gateway=False)` | CDP 热更新到 Hermes |
| [hermes_config.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/hermes_config.py#L259) | `clear_hermes_cdp_endpoint(restart_gateway=False)` | 清除 CDP 配置 |
| [ai_page_probe.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_page_probe.py#L22) | `_COLLECT_INTERACTIVE_JS` 常量 | 50+ UI 框架的 DOM 探测 JS |
| [ai_page_probe.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_page_probe.py#L910) | `_format_summary_lines()` | 快照摘要格式化 |
| [browser_manager.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/browser_manager.py#L227) | `BrowserManager` 类 | 参考其 Playwright 启动模式 |

---

## Phase 2：动作记录器

> 从 Hermes 返回的文本中提取结构化动作，按需触发视觉分析，维护动作列表。

### 2.1 新建 `ai_action_recorder.py`

**职责**: 解析 Hermes 文本结果 → 结构化动作；按需视觉触发（复用 `ai_vision_local` 熔断器）；转换为原始步骤列表

**核心类**:
```python
@dataclass
class ActionRecord:
    action_id: str = ""
    action_type: str = ""        # navigate / click / input / wait / assert
    target: str = ""
    input_data: str = ""
    result: str = ""
    status: str = "success"     # success / fail / skipped
    timestamp: float = field(default_factory=time.time)
    vision_info: Optional[Dict] = None
    raw_text: str = ""

class ActionRecorder:
    def capture_from_hermes_result(self, result_text: str) -> List[ActionRecord]:
        """从 Hermes 文本中提取结构化动作"""
        # 正则匹配 URL 导航 / 点击 / 输入 / 断言

    def _trigger_vision_for_records(self, records: List[ActionRecord]):
        """按需触发视觉——只在失败或歧义时触发，不是每个动作都做。
        复用 ai_vision_local 的 _VisionCircuitBreaker 熔断器保护。"""

    def to_case_steps(self) -> List[Dict[str, Any]]:
        """将动作记录转换为原始步骤列表（供 ai_step_normalization 处理）"""
```

### 2.2 集成到工具循环

**文件**: [ai_chat_tool_loop.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_chat_tool_loop.py)

**修改**: 在 `run_ai_chat_with_tools_stream` 的 `tool_call_result` 事件处理中（第 779 行附近），调用 recorder：
```python
elif evt_type == "tool_call_result":
    tool_name = evt_data.get('tool', '')
    result_preview = evt_data.get('result_preview', '')
    if tool_name == "hermes_execute" and recorder:
        new_records = recorder.capture_from_hermes_result(result_preview)
        for rec in new_records:
            yield ("action_record", {
                "action_type": rec.action_type,
                "target": rec.target,
                "status": rec.status,
                "result": rec.result[:100],
                "has_vision": bool(rec.vision_info),
            })
```

**修改**: `ChatToolLoopParams` 增加 `recorder` 字段；`run_ai_chat_with_tools_stream` 接受 recorder 参数。

### 2.3 修改 SSE 事件处理

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) 第 6004-6119 行

在 SSE `_gen()` 的事件循环中，新增 `action_record` 和 `vision_result` 事件类型的转发。

### 2.4 复用的现有模块

| 模块 | 函数 | 用途 |
|------|------|------|
| [ai_vision_local.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_vision_local.py#L23) | `_VisionCircuitBreaker` | 视觉调用熔断器（threshold=3, recovery=60s） |
| [ai_vision_local.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_vision_local.py) | `ocr_region_png(image_path)` | Tesseract OCR（chi_sim+eng） |

---

## Phase 3：动作转用例管线接入

> 将 `/api/ai/actions/to-case` 的 naive 映射替换为 `ai_step_normalization.py` 的 837 行完整管线。

### 3.1 重写 `/api/ai/actions/to-case`

**文件**: [app.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/app.py) 第 6320-6389 行

**当前问题**: 第 6333-6338 行只做 `action_type→action, target→target` 的 naive 映射，完全忽略规范化管线。

**修改方案**:
```python
from ai_step_normalization import (
    normalize_ai_step,
    repair_raw_ai_steps_for_platform,
    dedupe_and_validate_ai_steps,
    apply_step_normalization_to_plan,
)

# Step 1: 原始动作 → 步骤格式
raw_steps = []
for action in actions:
    raw_steps.append({
        'action': action.get('action_type', '操作'),
        'target': action.get('target', ''),
        'input_value': action.get('input_data', ''),
        'automation_layer': platform,  # 供 normalize_ai_step 读取
    })

# Step 2: 逐条规范化（复用 837 行管线）
normalized_steps = [normalize_ai_step(step) for step in raw_steps]

# Step 3: 平台修复（复用：URL断言修正、toast断言改regex等）
# 注意：repair_raw_ai_steps_for_platform 返回 warnings，就地修改 steps
repair_warnings = repair_raw_ai_steps_for_platform(normalized_steps)

# Step 4: 去重 + 验证（复用）
clean_steps, validate_warnings = dedupe_and_validate_ai_steps(normalized_steps, platform=platform)

# Step 5: 构造 plan + 应用规范化（复用）
plan = {'case_name': case_name, 'case_url': case_url, 'steps': clean_steps}
plan, plan_warnings = apply_step_normalization_to_plan(plan)

# Step 6: 选择器恢复（复用 4 层降级，如有 probe_registry）
try:
    from ai_external_browser_bridge import get_probe_registry
    registry = get_probe_registry()
    if registry and clean_steps:
        from ai_locator_resolution import resolve_plan_steps_locators_with_snapshot
        plan = resolve_plan_steps_locators_with_snapshot(plan, registry)
except Exception:
    pass

# Step 7: 保存到数据库（复用现有逻辑）+ Skill 沉淀（复用现有逻辑）
```

### 3.2 复用的现有模块

| 模块 | 函数 | 行号 | 用途 |
|------|------|------|------|
| [ai_step_normalization.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_step_normalization.py#L245) | `normalize_ai_step(step)` | 245 | 单步规范化（读 `step["automation_layer"]` 判断平台） |
| [ai_step_normalization.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_step_normalization.py#L220) | `repair_raw_ai_steps_for_platform(steps)` | 220 | 智能修复（返回 warnings，就地修改 steps） |
| [ai_step_normalization.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_step_normalization.py#L558) | `dedupe_and_validate_ai_steps(steps, platform)` | 558 | 去重 + 验证（返回 `(clean_steps, warnings)`） |
| [ai_step_normalization.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_step_normalization.py#L662) | `apply_step_normalization_to_plan(plan)` | 662 | 计划级规范化（返回 `(plan, warnings)`） |
| [ai_locator_resolution.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_locator_resolution.py#L51) | `resolve_plan_steps_locators_with_snapshot(steps, snap, force)` | 51 | 选择器预解析 |
| [ai_selector_recovery.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_selector_recovery.py#L231) | `try_recover_selector_with_vision(page, ...)` | 231 | 4 层选择器恢复（async） |

---

## Phase 4：视觉/截屏 Stub 实现

> 将 3 个返回"开发中"的桩接口替换为真实实现，复用 `ai_vision_local` + `ai_external_browser_bridge`。

### 4.1 `/api/ai/vision/capture`（第 6392 行）

**当前**: `return jsonify({'success': True, 'message': '屏幕捕获功能开发中'})`

**修改**: 调用 `ai_external_browser_bridge.capture_screenshot()` 或 `mss` 屏幕截图，保存临时文件，返回路径。

### 4.2 `/api/ai/vision/snapshot`（第 6399 行）

**当前**: `return jsonify({'success': True, 'message': '屏幕快照功能开发中'}), 501`

**修改**: 返回最新截图缓存 + 按需 OCR（复用 `ai_vision_local.ocr_region_png`）。

### 4.3 `/api/ai/screen-share/toggle`（第 6406 行）

**当前**: `return jsonify({'success': True, 'enabled': enabled})` — 无实际逻辑

**修改**: 记录共享状态 + 定期截图间隔配置 + 隐私提示。

### 4.4 复用的现有模块

| 模块 | 函数 | 用途 |
|------|------|------|
| [ai_vision_local.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_vision_local.py) | `ocr_region_png(image_path)` | Tesseract OCR |
| [ai_vision_local.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_vision_local.py) | `vision_describe(image_bytes, instruction)` | 本地 Ollama VLM |
| [ai_vision_local.py](file:///d:/mkst_baixiang/Python_Code/NewUITestPlatform/NewUITestPlatform/ai_vision_local.py) | `vision_describe_cloud(...)` | 云端 VLM 回退 |

---

## Phase 5：前端中栏替换

> 保留三列布局骨架，将中栏 iframe 画布替换为 AI 对话面板。

### 5.1 中栏改动

**移除**: iframe 画布区域 + 画布控制按钮

**新增**: 对话面板（消息气泡 + 输入框 + 执行/停止/动作转用例按钮）

### 5.2 右栏扩展

现有右栏 320px，改为可折叠卡片组：
- 卡片1: 思考流程
- 卡片2: 执行动作（实时更新，来自 `action_record` 事件）
- 卡片3: 实时用例
- 卡片4: 屏幕快照

### 5.3 SSE 事件处理新增

```javascript
if (data.type === 'action_record') {
    appendActionRecord(data.action_type, data.target, data.status, data.result);
}
if (data.type === 'vision_result') {
    appendVisionResult(data.ocr_text, data.screenshot_url);
}
if (data.type === 'job_started') {
    AI_TEST_STATE.jobId = data.job_id;
}
```

### 5.4 桌面壳约束处理

| 约束 | 处理方式 |
|------|----------|
| `testory-desktop-shell.css` 的 `!important` | 新增 CSS 用 `:not(.testory-frameless-shell)` 限定 |
| `body` 有 `testory-frameless-shell` 类 | 确保新布局在 frameless 模式下正常 |
| CSS/JS 版本号缓存控制 | 修改后更新 `?v=X` 版本号 |
| 浅色主题 | 保留现有渐变 `--primary-gradient`，不改为深色 |

---

## 不做的事情（避免过度工程）

1. **不重构 `ai_chat_tool_loop.py` 的整体架构** — 工具循环逻辑本身是完整的，只增加 abort 传递和 recorder 集成
2. **不实现 `/api/ai/task/execute/logs` 的真实日志** — 前端不依赖此接口返回数据
3. **不修改 system prompt 的动态构建逻辑** — CDP 状态快照问题影响有限，优先级低
4. **不修改 `allow_agent` 为每轮重新评估** — 同上
5. **不为每个动作都做 OCR+VLM** — 视觉按需触发（失败/歧义时），避免执行速度下降 5-10 倍
6. **不另造动作数据模型** — 接入 `ai_step_normalization.py` 三平台管线，与现有用例格式兼容

---

## 假设与决策

| 决策 | 理由 |
|------|------|
| 用 `uuid.uuid4().hex` 生成 job_id | 与项目已有 `_AI_JOB_ABORT_EVENTS` 路径（app.py:4135）一致 |
| 通过 SSE 首事件推送 job_id | SSE 流式响应已开始发送 body 后无法改响应头 |
| 用 `AbortController` 中断 SSE 流 | Web 标准 API，fetch 原生支持 |
| 用 DevTools `/json/version` 获取 CDP URL | 不依赖 Playwright 私有属性，更稳定 |
| 用 `--remote-debugging-port=9222` 固定端口 | 简化实现，避免动态端口管理 |
| 视觉按需触发而非每步触发 | 避免 5-10 倍性能下降，复用现有 `_VisionCircuitBreaker` |
| 降级模式直接报错 | 伪数据比报错更糟糕 |
| `normalize_ai_step(step)` 不传 platform 参数 | 函数从 `step["automation_layer"]` 读取平台 |

---

## 验证步骤

### Phase 0 验证
1. `python -m py_compile app.py hermes_gateway_client.py ai_chat_tool_loop.py` — 语法检查
2. 启动应用，执行 AI 任务，验证 SSE 首事件包含 `job_id`
3. 任务执行中点击停止 → 任务立即停止，SSE 流中断
4. AI 输出中文不再乱码
5. 浏览器未连接时不显示"浏览器已启动"伪成功

### Phase 1 验证
6. Hermes 可用时，有头浏览器自动启动，CDP 同步成功
7. 页面快照和 DOM 探测正常工作

### Phase 2 验证
8. Hermes 执行后，动作列表实时显示在右栏

### Phase 3 验证
9. 动作转用例后，步骤格式与平台 runner 兼容（三平台 action 映射正确）

### Phase 4 验证
10. 视觉分析按需触发，不阻塞主执行流

### Phase 5 验证
11. 三列布局在 frameless 模式下正常显示，无 CSS 冲突
