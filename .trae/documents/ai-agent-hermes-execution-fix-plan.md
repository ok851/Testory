# AI Agent Hermes 执行链路修复计划

## Summary

修复 AI Agent 工作流中的 4 个关键问题，确保 Hermes 智能体**只在前台浏览器中执行**，消除后台独立浏览器 fallback，过滤对用户无用的元动作输出，并优化系统提示词以支持自然对话。

---

## Current State Analysis

### 1. 浏览器执行环境：前台与后台"双轨并行"

**现状**：
- `hermes_execute_allowed()`（`ai_chat_tool_loop.py:94-120`）在 Web 平台下的逻辑：
  1. 如果 `embedded_gateway_enabled()` 为 False → 允许执行
  2. 如果 `_ai_allow_main_playwright_fallback()` 为 True → 允许执行
  3. 如果 `hermes_cdp_attached()` 为 True → 允许执行
  4. 如果 Hermes Gateway 健康检查通过 → **也允许执行**
- 第 4 条是问题的根源：即使 CDP **未 attach** 到前台浏览器，只要 Hermes Gateway 进程在运行，就允许调用 `hermes_execute`
- Hermes Gateway 是一个独立的 Agent 服务，它**有自己的浏览器管理能力**。当没有收到 CDP 端点时，它会自己启动一个浏览器（通常是无头的）来执行指令
- 这导致用户看到的现象：前台有头浏览器窗口打开了，但没有任何操作痕迹；而 Hermes 返回"执行成功"的结果，实际上是在**后台独立浏览器**中完成的

**关键文件**：
- `ai_chat_tool_loop.py:94-120` — `hermes_execute_allowed()` 的宽松逻辑
- `app.py:5994-6028` — `_gen()` 中启动浏览器并同步 CDP 的逻辑
- `hermes_config.py` — CDP 端点状态管理

### 2. "hermes_execute → Hermes 执行完成"充斥对话框

**现状**：
- `ai_chat_tool_loop.py` 中每次 `hermes_execute` 完成后都会 yield `tool_call_result` 事件
- 前端 `handleStreamEvent`（`ai_test.html:1496`）将 `tool_call_result` 事件原样显示在对话框中
- 对于用户来说，这条信息没有任何价值——它只是在说"Hermes 这个工具调用完成了"
- 类似的，思考流程卡片中也显示了 "Hermes 正在浏览器中执行操作" 等元动作

**关键文件**：
- `ai_chat_tool_loop.py:688-693` — `tool_call_result` 事件生成
- `templates/ai_test.html:1511-1528` — 前端处理 `tool_call_result`

### 3. 系统提示词过度约束，Agent 丧失对话能力

**现状**：
- `_build_system_prompt()`（`ai_chat_tool_loop.py:203`）构建的系统提示词长达数千字
- 提示词核心导向："你是资深 QA / 自动化架构师"、"必须调用 hermes_execute"、"最终必须输出且仅输出一个 JSON 对象"
- 这导致 Agent 将**任何输入**都视为测试任务：
  - 用户问"你是谁" → Agent 调用 `hermes_execute` 去"探索当前浏览器页面"
  - 用户问"打开控制面板" → Agent 调用 `hermes_execute` 去尝试操作系统命令
- 系统提示词完全没有区分"测试任务"和"普通对话"的边界

**图片证据**：
- 用户发送"你是谁"，Agent 回复"hermes_execute → 探索当前浏览器中打开的页面..."
- 这说明 Agent 把对话意图误判为测试探测任务

**关键文件**：
- `ai_chat_tool_loop.py:203-310` — `_build_system_prompt()` 完整实现

### 4. 实时用例与真实执行脱节

**现状**：
- "实时用例"卡片的数据来自 `plan_update` 事件中的 `steps`
- 这些步骤是 AI 根据 Hermes 返回的文本**生成的**，不是真实执行动作的镜像
- 当 Hermes 在后台独立浏览器执行时，AI 仍然会根据返回的文本生成 steps，但这些 steps 与前台浏览器状态无关
- 用户看到的"实时用例"实际上是 AI 的"推测"，而非"已验证的执行记录"

**关键文件**：
- `ai_chat_tool_loop.py:566-584` — 模型返回内容解析为 plan
- `app.py:6064-6068` — `current_plan` 初始化

---

## Proposed Changes

### 问题 1：强制只在前台浏览器执行

#### 文件 1：`ai_chat_tool_loop.py`

**What**：修改 `hermes_execute_allowed()`，Web 平台下**严格限制**：只有 `hermes_cdp_attached()` 为 True 时才允许执行。

**Why**：当前第 4 条 fallback（Hermes Gateway 健康检查通过就允许）是后台独立浏览器执行的根源。必须切断这条路径，确保 Hermes 只能操作我们启动并同步了 CDP 的前台浏览器。

**How**：
```python
def hermes_execute_allowed(*, embedded_session_id: str = "", platform_type: str = "web") -> bool:
    plat = (platform_type or "web").strip().lower()
    if plat == "desktop":
        from agent_gateway_client import agent_gateway_configured
        return agent_gateway_configured()
    if plat == "android":
        return False
    # Web 平台：严格限制——只有 CDP attach 到前台浏览器后才允许 hermes_execute
    # 禁止 Hermes 在独立后台浏览器中执行
    return hermes_cdp_attached()
```

#### 文件 2：`app.py`

**What**：在 `_gen()` 中，如果 CDP 未 attach，不进入 Hermes 工具循环，而是直接提示用户。

**Why**：当 `hermes_execute_allowed()` 返回 False 时，AI 不会调用 `hermes_execute`，但整个工具循环仍然会进行。需要在前端给出明确的阻塞提示，让用户知道必须先确保浏览器 CDP 连接成功。

**How**：
在 `_gen()` 的 `browser_ready` 判断后增加：
```python
if not hermes_cdp_attached():
    yield send('error', error='浏览器 CDP 未连接，Hermes 无法在前台浏览器中执行。请等待浏览器启动完成后再重试。')
    return
```

### 问题 2：过滤 "hermes_execute → Hermes 执行完成" 元动作输出

#### 文件 1：`ai_chat_tool_loop.py`

**What**：`tool_call_result` 事件不再发送给前端对话框，只保留 `action_records` 用于右侧执行动作卡片。

**Why**：`tool_call_result` 描述的是"工具调用完成"这个元动作，对用户理解执行进度没有帮助。用户只需要看到"实际做了什么"（action_records）和"AI 在思考什么"（think）。

**How**：
删除或注释掉 `tool_call_result` 事件的 yield：
```python
# 移除：yield ("tool_call_result", {...})
# 保留：yield ("action_records", [...]) 用于右侧卡片
```

#### 文件 2：`templates/ai_test.html`

**What**：前端 `handleStreamEvent` 中删除对 `tool_call_result` 事件在对话框中显示的处理。

**Why**：即使后端不再发送 `tool_call_result`，前端也应该增加防御性过滤，防止历史事件或异常事件污染对话框。

**How**：
```javascript
} else if (ev.type === 'tool_call_result') {
    // 元动作：不显示在对话框中，只更新报告统计
    updateReportCard();
    // 不再调用 addChatMessage
}
```

### 问题 3：优化系统提示词，恢复 Agent 对话能力

#### 文件 1：`ai_chat_tool_loop.py`

**What**：重构 `_build_system_prompt()`，区分"测试任务模式"和"对话模式"，减少强制 JSON 输出要求。

**Why**：当前提示词把 Agent 锁定为"只能执行测试任务"的角色，丧失了基础对话能力。需要在提示词中增加意图判断指导，让 Agent 知道：
- 如果用户只是闲聊/询问身份/询问功能 → 直接回答，不要调用任何工具
- 如果用户要求执行具体操作/测试任务 → 才调用 hermes_execute

**How**：
```python
parts = [
    "你是 Testory 平台的 AI 测试助手，可以帮助用户进行自动化测试任务，也可以进行日常对话。",
    "",
    "## 任务判断",
    "请根据用户输入判断意图：",
    "- 如果用户在闲聊、询问你的身份/能力、表达感谢或抱怨 → 直接自然语言回答，不要调用任何工具。",
    "- 如果用户要求你执行具体的测试操作（如打开网站、点击按钮、输入内容、验证结果） → 可以调用 hermes_execute。",
    "- 如果用户要求修改用例步骤、调整选择器、增加断言 → 调用 refine_test_plan。",
]
```

同时，将"最终必须输出且仅输出一个 JSON 对象"的要求，改为：
```python
"当用户明确要求生成测试用例时，最终输出一个 JSON 对象（case_name, case_url, description, steps）。"
"日常对话不需要输出 JSON。"
```

### 问题 4：实时用例只展示真实执行过的动作

#### 文件 1：`ai_chat_tool_loop.py` + `app.py`

**What**：彻底改造"实时用例"的数据来源。当前实时用例来自 AI 生成的 plan（`plan_update` 事件），这些步骤是 AI 的"推测"而非真实执行结果。改为：**实时用例只从 `action_records`（实际执行的动作）中生成**，过滤掉所有未经验证的计划步骤。

**Why**：用户明确指出"计划生成"的来源是虚假的、对用户无效的。实时用例应该反映"Agent 真正做了什么"，而不是"Agent 计划做什么"。

**How**：
1. 后端 `app.py` 中，当收到 `action_records` 事件时，将动作转换为 `case_step` 格式并发送给前端
2. 不再将 AI 生成的 plan steps 直接发送到"实时用例"卡片
3. 只有在任务完成且用户确认保存后，才将最终 plan 作为完整用例展示

```python
# 在 action_records 事件处理中，同时生成 case_step 事件
if new_recs:
    yield ("action_records", [...])
    # 将实际动作同步到实时用例卡片
    for r in new_recs:
        yield ("case_step", {
            "action": r.action_type,
            "target": r.target,
            "verified": True,
        })
```

#### 文件 2：`templates/ai_test.html`

**What**：移除对 `plan_update` 中 steps 的实时用例展示，只接收来自 `case_step` 的真实执行动作。

**Why**：防止 AI "胡思乱想"的 plan steps 污染实时用例卡片。

**How**：
- `handleStreamEvent` 中保留 `case_step` 处理逻辑
- 如果 `case_step` 包含 `verified=True`，显示为可信步骤
- 任务完成后，如果用户保存用例，再展示完整的 plan

---

## Assumptions & Decisions

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Web 平台 hermes_execute 权限 | **严格限制为 CDP attach 后才允许** | 彻底消除后台独立浏览器执行的可能性 |
| 后台浏览器 fallback | **完全移除** | 用户明确要求"只让 agent 在前台已打开的浏览器中执行" |
| tool_call_result 事件 | **不再发送到对话框** | 属于元动作，对用户无价值 |
| 系统提示词角色定义 | **增加"对话模式"判断** | 恢复 Agent 的基础对话能力，避免所有输入都被当作测试任务 |
| JSON 强制输出 | **仅在明确要求生成用例时要求** | 日常对话不需要 JSON，减少约束 |
| 实时用例标识 | **增加"已验证/计划中"区分** | 提升用户对用例准确性的信任度 |

---

## Verification Steps

1. **强制前台浏览器执行**：
   - 不启动浏览器直接发送任务，确认 Agent **不调用** hermes_execute，而是提示 CDP 未连接
   - 启动浏览器后发送任务，确认 Agent 调用 hermes_execute，且前台浏览器有可见操作
   - 手动关闭浏览器后再次发送任务，确认 Agent 不调用 hermes_execute

2. **过滤元动作输出**：
   - 执行任务后，确认对话框中**不再出现** "hermes_execute → Hermes 执行完成"
   - 确认右侧执行动作卡片仍然正常显示实际动作

3. **恢复对话能力**：
   - 发送"你是谁"，确认 Agent **直接回答**，不调用 hermes_execute
   - 发送"打开 example.com 并搜索商品"，确认 Agent 正常调用 hermes_execute

4. **实时用例标识**：
   - 执行任务后，确认实时用例卡片中已执行的步骤显示 ✅，计划中的步骤显示 📝
