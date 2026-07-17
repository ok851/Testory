# AI Agent 工作流优化计划

## Summary

修复并优化 AI 自主测试工作流中的四大问题：
1. **浏览器进程控制**：用户手动关闭浏览器后，新任务无法重新启动（状态残留导致误判）
2. **共享屏幕视觉识别**：当前仅有开关无实际逻辑，需要实现每轮推理前自动截图+多模态分析，让 AI "看到"屏幕
3. **右侧卡片内容过滤**：执行动作和测试报告混入大量元动作（如 "Hermes 执行完成"），需过滤只保留实际平台动作
4. **快捷操作重构**：去掉左下角冗余快捷操作卡片，在执行完成后增加"保存用例到项目"入口

---

## Current State Analysis

### 1. 浏览器进程控制（问题根因已定位）

**现状**：
- `ai_external_browser_bridge.py` 的 `is_browser_alive()` 通过尝试访问 `_page.url` 检测浏览器存活
- 当检测到死亡时，会清理本地 `_browser/_context/_page/_cdp_ws` 引用
- **但 `hermes_config.py` 中的 `_ACTIVE_CDP_ENDPOINT` 内存变量不会被同步清理**
- `app.py` 启动逻辑：`if not hermes_cdp_attached() or not is_browser_alive()`
- 如果 `is_browser_alive()` 返回 False 但 `hermes_cdp_attached()` 仍返回 True，逻辑上仍会进入 `ensure_browser()` 分支
- **真正的问题**：`is_browser_alive()` 中的异常处理可能在某些情况下（如进程被强制终止但 Playwright 连接句柄未超时）不能立即检测到死亡，导致新任务复用已死的浏览器引用

**关键文件**：
- `ai_external_browser_bridge.py` — 浏览器生命周期管理
- `hermes_config.py` — CDP endpoint 状态（`_ACTIVE_CDP_ENDPOINT`）
- `app.py` 第 5999-6018 行 — 浏览器启动决策逻辑

### 2. 共享屏幕视觉识别（当前完全为空壳）

**现状**：
- 前端有 toggle 开关（`toggleScreenShare`），调用 `/api/ai/screen-share/toggle`
- 后端仅设置 `bridge._screen_share_active = True` 和 `_screen_share_interval = 3`，**没有任何代码消费这些变量**
- `ai_chat_tool_loop.py` 的工具循环中完全没有屏幕截图和视觉分析的逻辑
- `ai_vision_local.py` 已具备 `vision_describe()` 能力（支持 Ollama 本地 + 云端多模态），但未被调用
- `ai_external_browser_bridge.py` 有 `capture_screenshot()` 可以截取浏览器页面
- `app.py` 有 `/api/ai/vision/capture` 可以截取浏览器或桌面屏幕

**关键文件**：
- `ai_chat_tool_loop.py` — 工具循环核心，需要注入视觉观察逻辑
- `ai_external_browser_bridge.py` — 截图能力
- `ai_vision_local.py` — 视觉分析能力
- `app.py` 第 6485-6502 行 — 屏幕共享 toggle API（空壳）
- `templates/ai_test.html` — 前端 toggle UI

### 3. 右侧卡片内容过滤

**现状**：
- `AI_TEST_STATE.actions` 收集了所有 `action` 类型事件
- `app.py` 在 `tool_call_start` 时 yield `action`（status=running, target=args_summary）
- `app.py` 在 `tool_call_result` 时 yield `action`（status=success, target="Hermes 执行完成"）
- 前端 `updateActionCard()` 和 `updateReportCard()` 显示所有 actions，包括元动作 "hermes_execute"
- 用户期望只显示实际的平台动作（click, input, navigate, assert, wait 等），不显示工具调用层面的元动作

**关键文件**：
- `app.py` 第 6067-6078 行 — tool_call_start/tool_call_result 转 action 事件
- `templates/ai_test.html` — `updateActionCard()`, `updateReportCard()` 函数

### 4. 快捷操作与保存用例

**现状**：
- 左下角有"快捷操作"卡片，包含：执行任务、连接浏览器、生成用例、动作转用例
- 这些按钮在其他位置已有重复入口（顶部按钮、输入框下方按钮）
- 执行完成后没有"保存用例到项目"的入口
- 后端有 `create_test_case_v2()` 数据库方法和 `/api/ai/cases/append-steps` API，但没有"新建用例并保存完整 plan"的 API

**关键文件**：
- `templates/ai_test.html` 第 873-889 行 — 快捷操作卡片 HTML
- `app.py` 第 6505-6573 行 — `api_ai_append_steps_to_case`
- `database.py` 第 1822 行 — `create_test_case_v2`

---

## Proposed Changes

### 问题 1：浏览器进程控制修复

#### 文件 1：`ai_external_browser_bridge.py`

**What**：增强 `is_browser_alive()` 的可靠性，并增加 CDP 状态同步清理。

**Why**：当前 `is_browser_alive()` 仅检查 `_page.url`，但浏览器进程被用户手动关闭后，Playwright 的连接可能处于"半断开"状态（抛出异常有延迟）。需要更积极的检测手段。

**How**：
1. 在 `is_browser_alive()` 中增加 `_browser.process` 存活检测（如果可用）
2. 当检测到浏览器死亡时，**同步调用 `hermes_config.clear_hermes_cdp_endpoint()`** 清理全局状态
3. 增加一个显式的 `force_cleanup_browser()` 函数，供外部强制重置

```python
def is_browser_alive() -> bool:
    global _browser, _context, _page, _cdp_ws
    with _bridge_lock:
        if _browser is None or _page is None:
            return False
        try:
            # 多层检测：先检查进程是否还在
            if hasattr(_browser, "process") and _browser.process:
                if hasattr(_browser.process, "poll") and _browser.process.poll() is not None:
                    raise RuntimeError("Browser process exited")
            # 再检查 page 是否可交互
            _ = _page.url
            return not _page.is_closed()
        except Exception:
            # 清理所有引用，并同步清理 hermes_config 中的 CDP 状态
            try:
                from hermes_config import clear_hermes_cdp_endpoint
                clear_hermes_cdp_endpoint()
            except Exception:
                pass
            _browser = _context = _page = None
            _cdp_ws = ""
            return False
```

#### 文件 2：`app.py`

**What**：浏览器启动前增加强制清理选项，确保状态重置。

**Why**：如果 `is_browser_alive()` 由于某种原因未能检测到死亡（如锁竞争），启动前应该有一次兜底清理。

**How**：
在 `ensure_browser()` 调用前，如果 `hermes_cdp_attached()` 返回 True 但 `is_browser_alive()` 返回 False，先调用 `force_cleanup_browser()` 强制清理，然后再启动。

---

### 问题 2：共享屏幕视觉识别（核心功能）

#### 文件 1：新增 `ai_screen_observer.py`

**What**：新建 `ScreenObserver` 类，封装"截图 → 变化检测 → 隐私过滤 → 异步视觉分析 → 差异摘要"全链路。

**Why**：需要一个独立的、可复用的模块来封装屏幕观察逻辑，与工具循环解耦。同时必须解决性能、隐私、信息噪声等关键问题。

**How**：

```python
import threading
import time
import hashlib
import re
from typing import Optional

class ScreenObserver:
    """屏幕观察者：支持异步视觉分析、变化检测、隐私过滤。"""

    # 隐私信息正则过滤模式（邮箱、手机号、IP、身份证号）
    _PRIVACY_PATTERNS = [
        (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), '[EMAIL]'),
        (re.compile(r'\b1[3-9]\d{9}\b'), '[PHONE]'),
        (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), '[IP]'),
        (re.compile(r'\b\d{17}[\dXx]|\d{15}\b'), '[ID]'),
    ]

    def __init__(self, platform_type: str = "web", interval_sec: int = 3):
        self.platform_type = platform_type
        self.interval_sec = interval_sec
        self._last_capture_time = 0.0
        self._last_analysis = ""
        self._last_image_hash = ""  # 用于像素级变化检测
        self._pending_result: Optional[str] = None
        self._lock = threading.Lock()
        self._capture_count = 0

    def should_capture(self) -> bool:
        return time.time() - self._last_capture_time >= self.interval_sec

    def _image_hash(self, png_bytes: bytes) -> str:
        """计算图片快速哈希用于变化检测。"""
        return hashlib.md5(png_bytes[::16]).hexdigest()  # 采样降低计算量

    def _has_significant_change(self, png_bytes: bytes) -> bool:
        """检测画面是否发生显著变化。"""
        current_hash = self._image_hash(png_bytes)
        if current_hash == self._last_image_hash:
            return False
        self._last_image_hash = current_hash
        return True

    def _filter_privacy(self, text: str) -> str:
        """过滤敏感信息。"""
        for pattern, replacement in self._PRIVACY_PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def capture_and_analyze_async(
        self,
        instruction_hint: str = "",
        on_result: Optional[callable] = None,
    ) -> None:
        """异步截图并分析，不阻塞调用线程。"""
        def _do():
            result = self._do_capture_and_analyze(instruction_hint)
            with self._lock:
                self._pending_result = result
            if on_result:
                on_result(result)
        threading.Thread(target=_do, daemon=True).start()

    def _do_capture_and_analyze(self, instruction_hint: str = "") -> str:
        """同步执行截图+分析。"""
        # 1. 根据平台类型选择截图来源
        png = None
        if self.platform_type == "web":
            from ai_external_browser_bridge import capture_screenshot
            png = capture_screenshot()
        else:
            import mss
            from mss.tools import to_png
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                shot = sct.grab(monitor)
                png = to_png(shot.rgb, shot.size)

        if not png:
            return ""

        # 2. 变化检测：画面未变则跳过分析
        if not self._has_significant_change(png):
            return ""

        # 3. 构建结构化视觉分析指令（控制输出长度和格式）
        hint = instruction_hint or (
            "Analyze this screenshot for UI testing purposes. "
            "Reply in Chinese with STRICT format (max 300 chars):\n"
            "活跃窗口/页面: <title>\n"
            "关键UI元素: <list of visible interactive elements>\n"
            "异常/弹窗: <any popup, error, or unexpected state>\n"
            "与上一帧变化: <what changed compared to previous state, or '无变化'>"
        )

        # 4. 调用 vision_describe（自动支持本地 Ollama / 云端）
        from ai_vision_local import vision_describe
        try:
            result = vision_describe(png, hint)
            result = self._filter_privacy(result)
            self._last_analysis = result
            self._last_capture_time = time.time()
            self._capture_count += 1
            return result
        except Exception as e:
            uat_logger.warning("ScreenObserver vision analysis failed: %s", e)
            return ""

    def pop_pending_result(self) -> Optional[str]:
        """取出待处理的分析结果（非阻塞）。"""
        with self._lock:
            result = self._pending_result
            self._pending_result = None
            return result

    def get_last_analysis(self) -> str:
        return self._last_analysis

    def get_capture_count(self) -> int:
        return self._capture_count
```

#### 文件 2：`ai_chat_tool_loop.py`

**What**：在 `run_ai_chat_with_tools_stream()` 中注入**异步非阻塞**的屏幕观察逻辑。

**Why**：视觉分析（尤其是本地多模态模型）可能耗时 2~10 秒，同步等待会严重拖慢 Agent 响应。采用异步方式：先触发截图分析，Agent 继续后续工具操作，分析结果以追加消息形式稍后注入。

**How**：
1. 在 `ChatToolLoopParams` 中新增 `screen_observer: Optional[ScreenObserver] = None`
2. 在每轮推理的 `yield ("thinking", ...)` 之后，如果 `screen_observer` 存在且 `should_capture()` 返回 True：
   - 调用 `capture_and_analyze_async()` 触发**后台线程**分析
   - yield `vision_start` 事件通知前端"AI 正在观察屏幕"
3. 在下一轮推理开始前（或当前轮次 tool call 完成后），检查 `pop_pending_result()`：
   - 如果有结果且非空，yield `vision_result` 事件
   - 将结果作为 `role=user` 的 `[Screen Observation]` 消息插入 messages（限制 300 字符）
4. **截图时机优化**：在 Hermes 工具执行完成后、yield `tool_call_result` 前，增加 500ms 等待确保页面稳定，再触发截图

```python
# 在 tool_call_result 之前增加稳定等待
if name in ("hermes_execute", "openclaw_execute"):
    result_text = _handle_agent_execute(...)
    # 等待页面稳定后再截图
    if params.screen_observer:
        time.sleep(0.5)
        params.screen_observer.capture_and_analyze_async(
            instruction_hint=f"Task: {params.message}. Current screen after '{name}' execution.",
        )
```

#### 文件 3：`app.py`

**What**：在 SSE 任务执行流中实例化 `ScreenObserver` 并传给 `ChatToolLoopParams`；完善 `/api/ai/screen-share/toggle` API；处理 `vision_start` / `vision_result` SSE 事件。

**Why**：需要打通"前端开关 → 后端状态 → 工具循环消费 → 前端反馈"的全链路。

**How**：
1. 在 `_gen()` 中，根据 `aiTestPlatform` 创建 `ScreenObserver`，根据前端 toggle 状态决定是否传入
2. 处理新的 SSE 事件类型 `vision_start` / `vision_result`，转发给前端
3. `/api/ai/screen-share/toggle` 将状态持久化到模块级字典 `_screen_share_states[user_id]`，并返回当前捕获次数
4. 任务开始时如果屏幕共享开启，yield 一个 `warning` 事件提醒用户"屏幕共享已开启，AI 将分析屏幕内容"

#### 文件 4：`templates/ai_test.html`

**What**：前端增加视觉观察状态的实时展示，包括观察摘要和缩略图预览。

**Why**：豆包等产品的实时观察有明确的前端反馈，用户需要感知到 AI 正在"看"以及"看到了什么"。

**How**：
1. 在 `handleStreamEvent` 中处理 `vision_start` 和 `vision_result` 事件
2. `vision_start`：在思考流程卡片中插入视觉观察条目（带眼睛图标 + 脉冲动画 + "正在观察..."）
3. `vision_result`：更新该条目为分析结果摘要（前 120 字），并增加绿色完成标记
4. 左侧"共享屏幕"toggle 下方增加状态文本："AI 已观察屏幕 X 次 | 最近一次：XXX"
5. 点击观察条目可展开查看完整摘要（最多 300 字）
6. （可选）如果后端支持，可展示低分辨率缩略图（Base64，200px 宽）

---

### 问题 3：右侧卡片内容过滤

#### 文件 1：`app.py`

**What**：修改 SSE 事件生成逻辑，区分"元动作"（hermes_execute 工具调用本身）和"实际平台动作"（click, input, navigate 等）。

**Why**：当前所有 action 事件都混在一起，用户无法区分哪些是 Hermes 工具调用的元动作，哪些是实际在浏览器中执行的操作。

**How**：
1. 保留现有的 `action` 事件用于聊天区域显示（显示工具调用状态）
2. 新增 `platform_action` 事件，专门用于右侧"执行动作"卡片
3. `platform_action` 从 `action_records`（由 `ActionRecorder` 提取）中生成，只包含实际的平台动作

在 `_gen()` 中：
```python
elif evt_type == "action_records":
    for rec in evt_data:
        # 只转发实际平台动作到右侧卡片
        action_type = rec.get("action_type", "")
        if action_type not in ("hermes_execute", "openclaw_execute", "refine_test_plan"):
            yield send('platform_action', **rec)
```

#### 文件 2：`templates/ai_test.html`

**What**：修改前端状态管理和卡片更新逻辑。

**Why**：需要区分 `actions`（所有动作，用于报告统计）和 `platform_actions`（仅实际动作，用于执行动作卡片）。

**How**：
1. `AI_TEST_STATE` 新增 `platform_actions: []`
2. `handleStreamEvent` 中 `ev.type === 'platform_action'` 时：
   - 推入 `AI_TEST_STATE.platform_actions`
   - 调用 `updateActionCard()`（只显示 platform_actions）
3. `updateActionCard()` 改为从 `AI_TEST_STATE.platform_actions` 读取数据
4. `updateReportCard()` 仍然从 `AI_TEST_STATE.actions` 读取（保留完整统计）
5. 过滤掉 `action_type === 'hermes_execute'` 且 `content === 'Hermes 执行完成'` 的条目不在执行动作卡片显示

---

### 问题 4：快捷操作重构与保存用例

#### 文件 1：`templates/ai_test.html`

**What**：
1. 删除左下角"快捷操作"卡片（第 873-889 行）
2. 在执行完成后（`ev.type === 'done'`），在聊天区域底部显示一个"保存用例到项目"的横幅
3. 在右侧面板执行报告卡片中增加"保存用例"按钮

**Why**：快捷操作与顶部和输入区的按钮功能重复，去掉可以减少视觉噪音。执行完成后没有保存入口是明显的体验断点。

**How**：
1. 删除 HTML 中快捷操作卡片的 DOM
2. 在 `handleStreamEvent` 的 `done` 分支中，如果 `ev.plan` 存在且包含 `steps`，显示保存横幅：
   ```javascript
   if (ev.plan && ev.plan.steps && ev.plan.steps.length > 0) {
       AI_TEST_STATE.lastGeneratedPlan = ev.plan;
       showSaveCaseBanner(ev.plan);
   }
   ```
3. 新增 `showSaveCaseBanner(plan)` 函数，在聊天消息区域底部插入一个可点击的横幅
4. 新增 `saveGeneratedCaseToProject()` 函数，调用后端 API 保存用例

#### 文件 2：新增 API `app.py`

**What**：新增 `/api/ai/cases/create-from-plan` API，接受完整的 plan JSON，创建新用例并写入步骤。

**Why**：现有 `/api/ai/cases/append-steps` 只能追加到已有用例，无法直接新建。

**How**：
```python
@app.route('/api/ai/cases/create-from-plan', methods=['POST'])
@login_required
@role_required('admin', 'tester', 'project_manager', 'test_lead')
@api_error_handler
@log_api_request
def api_ai_create_case_from_plan():
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    plan = data.get('plan') or {}
    
    if not project_id:
        return jsonify({'success': False, 'error': 'project_id 不能为空'}), 400
    if not isinstance(plan, dict):
        return jsonify({'success': False, 'error': 'plan 格式错误'}), 400
    
    # 1. 创建用例
    case_name = plan.get('case_name', 'AI 生成用例')
    case_url = plan.get('case_url', '')
    description = plan.get('description', '')
    
    case_id = db.create_test_case_v2(
        project_id=project_id,
        name=case_name,
        url=case_url,
        description=description,
        created_by=current_user.id,
    )
    
    # 2. 写入步骤
    steps = plan.get('steps') or []
    if steps:
        goal_hint = case_name
        db_local = Database()
        db_local._fill_missing_step_payloads(steps, goal_hint, case_url, None)
        clean_steps, warnings = dedupe_and_validate_ai_steps(steps)
        for idx, step in enumerate(clean_steps, start=1):
            db.create_test_step(**_ai_step_to_db_kwargs(step, case_id, idx))
    
    return jsonify({
        'success': True,
        'case_id': case_id,
        'case_name': case_name,
        'steps_created': len(steps),
    })
```

---

## Assumptions & Decisions

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 视觉分析插入 messages 的方式 | 作为 `role=user` 的 `[Screen Observation]` 前缀消息插入，限制 300 字符 | 最兼容 OpenAI/Anthropic/Ollama 的 tool calling 协议；短摘要避免上下文膨胀 |
| 视觉分析执行方式 | **异步后台线程**执行，主线程继续工具循环，结果以回调/追加消息形式稍后注入 | 本地多模态推理可能耗时 2~10 秒，同步阻塞会严重拖慢 Agent 响应 |
| 变化检测策略 | 图片采样 MD5 哈希比对，无显著变化则跳过视觉分析 | 避免对静态画面重复分析，减少 API 调用和 GPU 占用 |
| 截图时机 | Hermes 工具执行完成后等待 500ms 再截图 | 避免拍到页面过渡动画或未加载完成的状态 |
| 视觉分析指令格式 | 结构化中文摘要（活跃窗口、关键UI元素、异常弹窗、与上一帧变化），限制 300 字 | 减少无关细节（桌面图标、背景），聚焦测试相关元素 |
| 隐私保护 | 本地正则过滤邮箱、手机号、IP、身份证号后再插入 messages | 桌面截图可能包含敏感信息，即使本地模型也需过滤 |
| 截图来源区分 | web → Playwright 页面截图；desktop → mss 全屏截图 | 与平台类型选择器对齐，web 任务不需要桌面上下文 |
| 模型回退策略 | 优先本地 Ollama，若未配置或熔断则回退云端 | 与企业/个人用户双重需求对齐 |
| 执行动作卡片过滤 | 只显示 `action_type` 不在元动作黑名单中的记录 | 最小改动，不破坏后端事件流，仅在前端过滤 |
| 保存用例入口位置 | 执行完成后聊天区域横幅 + 右侧报告卡片按钮 | 双重入口确保用户不会错过保存时机 |
| ScreenObserver 定位 | 设计为通用服务，既能服务工具循环，也能服务独立的"屏幕问答"会话 | 为将来扩展"用户主动共享屏幕提问"场景预留接口 |

---

## Verification Steps

1. **浏览器进程控制**：
   - 启动任务，确认浏览器弹出
   - 手动关闭浏览器窗口
   - 再次执行任务，确认浏览器重新弹出（而不是报错或卡住）
   - 重复 3 次验证稳定性

2. **屏幕共享视觉识别**：
   - 开启"共享屏幕"toggle
   - 执行一个 web 测试任务
   - 观察思考流程卡片中是否出现"AI 正在观察屏幕"条目
   - 观察该条目是否有分析结果摘要
   - 检查后端日志中是否有 `vision_describe` 调用记录
   - 关闭 toggle 后确认不再触发视觉分析

3. **右侧卡片内容过滤**：
   - 执行任务后，检查"执行动作"卡片
   - 确认不出现 "hermes_execute → Hermes 执行完成"
   - 确认只出现实际的平台动作（click, input, navigate 等）
   - 检查"执行报告"卡片统计数字与详情列表是否一致

4. **快捷操作与保存用例**：
   - 确认左下角没有"快捷操作"卡片
   - 执行完成后，确认聊天区域底部出现"保存用例到项目"横幅
   - 点击保存，选择项目，确认用例成功写入数据库
   - 进入项目用例列表，验证步骤完整性

---

## 外部评估风险分析与当前覆盖状态

另一 AI 对 ScreenObserver 方案进行了评估，指出 5 项潜在风险。当前代码覆盖情况如下：

| 风险点 | 当前覆盖状态 | 说明 |
|--------|-------------|------|
| **上下文窗口膨胀** | 部分覆盖 | 已将单条观察摘要限制在 300 字符以内，但未实现"差异对比"（后续只描述变化部分）和"按需观察"模式。若任务轮次很多，累积的观察消息仍可能膨胀。 |
| **性能与实时性** | 已覆盖 | `capture_and_analyze_async()` 采用后台线程非阻塞执行；主线程继续工具循环；结果通过 `pop_pending_result()` 在下一轮注入。已避免同步等待。 |
| **截图与操作的竞态** | 已覆盖 | Hermes 工具执行完成后显式 `time.sleep(0.5)` 再触发截图；同时变化检测会跳过无变化的帧。但未实现"UI Automation 确认控件状态变化后再截屏"的精确等待。 |
| **隐私与数据安全** | 已覆盖 | `_PRIVACY_PATTERNS` 正则过滤邮箱、手机号、IP、身份证号；视觉分析优先走本地 Ollama。但未实现"仅分析测试窗口"（当前 desktop 模式是全屏截图）。 |
| **前端展示过于简陋** | 部分覆盖 | 前端已处理 `vision_start` / `vision_result` 事件，在思考流程卡片中显示眼睛图标和摘要。但未展示最近观察的完整摘要展开区，也未附加缩略图预览。 |
| **"屏幕问答"独立场景** | 已预留 | `ScreenObserver` 设计为独立类，不依赖工具循环内部状态，将来可通过外部实例化直接服务于"用户主动共享屏幕提问"会话。 |

### 本轮计划未覆盖的改进（建议后续迭代）

1. **差异对比观察**：首次截图全量描述，后续仅输出与上一帧显著变化的部分（如弹窗出现、按钮高亮），进一步压缩上下文。
2. **按需观察模式**：Agent 在操作后若预期状态未达成，再主动调用截图分析，而非固定间隔盲采。
3. **仅分析测试窗口**：通过 Windows API 截取指定应用窗口，而非全屏，减少隐私暴露和无关信息。
4. **前端缩略图预览**：在观察条目中附加 200px 宽 Base64 缩略图，让用户直观验证 AI "看到了什么"。
5. **上下文自动清理**：当 messages 长度超过阈值时，自动丢弃早期的 `[Screen Observation]` 消息，防止窗口膨胀。

---

## 剩余待完成任务

基于代码库实际状态审计，以下任务尚未完成，需要继续执行：

1. **右侧执行报告卡片添加"保存用例"按钮**
   - 目标文件：`templates/ai_test.html` 第 964-993 行（`#aiReportCard`）
   - 现状：保存用例入口仅在聊天区域横幅（`showSaveCaseBanner`），右侧报告卡片中缺少按钮
   - 动作：在 `#aiReportCardBody` 底部（统计数字下方）增加一个"保存用例到项目"按钮，绑定 `saveGeneratedCaseToProject()`

2. **Python 语法检查**
   - 目标文件：`ai_screen_observer.py`、`ai_external_browser_bridge.py`、`ai_chat_tool_loop.py`、`app.py`、`ai_multi_provider.py`
   - 动作：运行 `python -m py_compile` 逐个检查，确保无语法错误

3. **运行验证**
   - 启动 Flask 服务，确认新模块无导入错误
   - 执行一轮完整任务，验证 4 个问题均修复
