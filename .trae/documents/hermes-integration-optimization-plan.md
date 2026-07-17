# Hermes 智能体集成优化方案（核心版）

## 一、核心问题

当前 AI 自主测试页面的后端执行逻辑**没有真正利用已有的 Hermes 工具链**。项目中已有完整的：
- `ai_chat_tool_loop.py`：多轮工具循环（hermes_execute + refine_test_plan）
- `hermes_skill_loop.py`：执行计数 + Skill 自动沉淀
- `ai_hermes_skills.py`：用例 ↔ Skill 互转
- `hermes_service_bootstrap.py`：Hermes Gateway 自动启停

但 `/api/ai/task/execute` 没有复用这些模块，导致 AI 无法真正操作浏览器/电脑。

---

## 二、核心实施步骤

### Step 1：重构后端执行接口

**文件**：`app.py` → `api_ai_task_execute()`

**修改**：使用 `run_ai_chat_with_tools_stream()` 替代当前逻辑

```python
# 新逻辑
1. 确保 Hermes Gateway 已启动（调用 hermes_service_bootstrap）
2. 构造 ChatToolLoopParams
3. 调用 run_ai_chat_with_tools_stream() 
4. 将流式事件映射为 SSE：
   - "thinking"    → SSE think 事件
   - "tool_call_start"  → SSE action 事件
   - "tool_call_result" → SSE action 结果
   - "plan_update" → SSE case_step 事件
   - "done"        → SSE done 事件
   - "error"       → SSE error 事件
5. 执行完成后 hermes_skill_loop.record_execution_success()
```

**Hermes 未配置降级**：使用 `browser_manager` 直接启动有头浏览器

### Step 2：Hermes Gateway 一键启停

**文件**：`app.py` 新增接口

- `GET /api/ai/hermes/status` → 返回运行状态、CDP连接状态
- `POST /api/ai/hermes/start` → 一键启动（复用 `bootstrap_hermes_services()`）
- `POST /api/ai/hermes/stop` → 一键停止（复用 `stop_hermes_gateway()`）

**前端**：左侧面板顶部增加 Hermes 状态指示器 + 启动/停止按钮

### Step 3：超时时间前端可控

**文件**：`ai_test.html`

- 左侧面板增加"超时时间"输入框，默认 120 秒
- 执行时将 timeout 参数传给后端
- 后端根据 timeout 设置 `HERMES_GATEWAY_TIMEOUT` 环境变量
- 超时后前端停止执行并报错

### Step 4：CDP 浏览器插件方案

**方案**：开发 Chrome 扩展，实现一键安装 + CDP 连接

**参考案例**：Codex Chrome 插件——让 AI Agent 进入用户已登录的浏览器环境，使用用户的 Cookie/Session 操作网页。

**Chrome 扩展功能**：
1. **一键安装**：平台提供 `.crx` 文件下载 + 拖拽安装指引
2. **CDP 连接**：扩展启动 Chrome DevTools Protocol，获取 WebSocket URL
3. **WebSocket 桥接**：扩展将 CDP WebSocket URL 通过 `chrome.runtime.sendMessage` 发送给平台页面
4. **状态指示**：扩展图标显示连接状态（绿色=已连接/灰色=未连接）

**扩展技术方案**：
```
manifest.json:
  - permissions: debuggee, tabs, nativeMessaging
  - background service worker: 管理 CDP 连接
  - content script: 与平台页面通信

工作流程：
  1. 用户点击扩展图标 → 扩展启动 CDP 调试会话
  2. 获取 ws://localhost:XXXX/devtools/browser/... URL
  3. 通过 postMessage 发送给平台页面
  4. 平台页面接收 → 调用 sync_hermes_cdp_endpoint() 同步给 Hermes
  5. Hermes 以 cdp_attach 模式操作用户的 Chrome 浏览器
```

**发布方式**：
- 初期：开发者模式加载（解压扩展文件夹），快速验证
- 后续：发布到 Chrome Web Store，用户一键安装
- 同时放入平台插件市场

### Step 5：前端 SSE 事件映射优化

**文件**：`ai_test.html` → `handleStreamEvent()`

**映射规则**：
- `think`：只在右侧"思考流程"显示，中间对话框不输出
- `action`：右侧"执行动作" + 中间对话框简洁提示（✅/❌）
- `tool_call_start`：中间对话框显示"正在调用 Hermes..."
- `tool_call_result`：右侧"执行动作"更新结果
- `plan_update`：右侧"实时用例"整体更新
- `done`：中间对话框显示完成消息
- `error`：中间对话框显示错误

### Step 6：打通动作转用例 → Skill 沉淀

**文件**：`app.py` → `/api/ai/actions/to-case`

- 将执行的 actions 转为 plan 格式
- 调用 `hermes_skill_loop.record_execution_success()` 记录
- 达到阈值（3次）自动 `export_plan_to_skill()`
- 同时保存为平台用例（写数据库）
- 前端提示"已保存为用例并沉淀为 Skill"

---

## 三、实施优先级

1. **Step 1**（核心）：重构执行接口，让 AI 真正能操作
2. **Step 2**：Hermes 一键启停，简化用户操作
3. **Step 3**：超时时间前端可控
4. **Step 5**：前端 SSE 映射优化（与 Step 1 配套）
5. **Step 6**：动作转用例闭环
6. **Step 4**：CDP 插件（后续迭代）

---

## 四、风险与注意事项

1. **Hermes Gateway 未启动**：执行前自动检查并启动
2. **CDP 连接**：Web 平台需要 CDP Attach，初期提供安装指引
3. **超时**：默认 120 秒，前端可调
4. **降级**：Hermes 未配置时使用 `browser_manager` 直接操作
