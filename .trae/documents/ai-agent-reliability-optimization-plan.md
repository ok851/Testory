# Testory AI 智能体全面优化方案（v3）

## 一、架构现状与四层协作模型

### 1.1 当前四层架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户界面层                              │
│  ai_test.html (Agent Studio)  │  ai_design / ai_heal     │
└──────────────┬──────────────────────────────────────────-┘
               │ SSE / WebSocket / Polling
┌──────────────▼──────────────────────────────────────────-┐
│                 Agent 编排层                               │
│  ai_chat_tool_loop.py (多轮 tool calling 循环)            │
│  ┌──────────────┐  ┌──────────────────┐                  │
│  │hermes_execute │  │refine_test_plan  │                  │
│  │(浏览器探索)    │  │(用例JSON修改)     │                  │
│  └──────┬───────┘  └────────┬─────────┘                  │
└─────────┼───────────────────┼────────────────────────────-┘
          │                   │
┌─────────▼───────────┐  ┌────▼────────────────────────────-┐
│   Hermes Agent 层    │  │        LLM 推理层                 │
│  (独立进程,端口8642)  │  │  ai_multi_provider.py            │
│  ┌────────────────┐  │  │  cloud_llm_gateway.py            │
│  │ Browser Tools  │  │  │  ┌─────────┐ ┌───────────────┐  │
│  │ (CDP attach)   │  │  │  │Ollama本地│ │OpenAI兼容(云端)│  │
│  │ Memory / Skills│  │  │  │         │ │Anthropic/Gemini│  │
│  └────────────────┘  │  │  └─────────┘ └───────────────┘  │
└──────────────────────┘  └──────────────────────────────────-┘
          │
┌─────────▼───────────────────────────────────────────────-┐
│                  MCP 工具层                                │
│  testory_mcp/web.py │ desktop.py │ mobile.py              │
│  (stdio JSON → VisionActionPort 抽象)                      │
│  web_screenshot / web_tap / web_input / web_assert         │
└──────────────────────────────────────────────────────────-┘
          │
┌─────────▼───────────────────────────────────────────────-┐
│                 Skills 知识层                               │
│  ai_hermes_skills.py │ hermes_skill_loop.py               │
│  SKILL.md 文件 (agentskills.io/v1 格式)                    │
│  3次成功自动导出 → Curator 评估 → 向量记忆                  │
└──────────────────────────────────────────────────────────-┘
```

### 1.2 数据流：一次完整的 AI 用例生成

```
用户输入 "测试登录功能"
  → cloud_llm_gateway 数据脱敏（IP/URL/密码替换为占位符）
  → dispatch_chat_completion_messages() 发送到云端 LLM（OpenAI 兼容）
  → LLM 返回 tool_call: hermes_execute(instruction="导航到登录页...")
  → hermes_gateway_client 发送到 Hermes Agent (POST /v1/chat/completions)
  → Hermes Agent 用 CDP attach 操作浏览器，返回探索结果
  → LLM 看到探索结果，返回 tool_call: refine_test_plan(adjustment="...")
  → local_ai_service.refine_case_and_steps() 生成 JSON 用例
  → LLM 输出最终 JSON → 解析 → 规范化 → 返回前端
  → 用户运行用例 → 3次成功 → 自动导出为 Skill
```

### 1.3 关键认知：云端 LLM 是主力

当前平台支持 4 种 LLM 后端，但**云端 OpenAI 兼容 API 是主力**（用户主要使用 DeepSeek / MiniMax / 小米 MiMo 等国产模型）。Ollama 本地模型是备选方案。这意味着：

- 所有 LLM 调用都需要经过 `cloud_llm_gateway.py` 的数据脱敏
- 网络延迟和 API 限速是主要瓶颈（不是本地推理速度）
- 流式输出对用户体验至关重要（云端 API 普遍支持 streaming）
- 多 provider 降级策略很重要（一个 API 挂了要能切到另一个）

---

## 二、后端问题分析（更新版）

### P0 - 致命问题

#### 2.1 Async Job Store 是进程内字典
- **文件**: `app.py:3372`
- **问题**: `_AI_BG_JOBS = {}` 存在内存中，进程重启丢失所有运行中任务
- **修复**: 迁移到 SQLite（单进程直接用，多进程用 WAL 模式）

#### 2.2 取消操作无法中断 LLM 推理
- **文件**: `app.py:5845`
- **问题**: 设 `cancelled=True` 只是个标志位，实际 LLM 调用不会被中断
- **修复**: 用 `threading.Event` + HTTP 请求 abort 机制

#### 2.3 Hermes Gateway CDP 重连需要完全重启（~30 秒宕机）
- **文件**: `hermes_config.py:193-199`
- **问题**: 每次画布浏览器重连，Hermes 进程被杀死并重新启动
- **修复**: 支持热更新 CDP 端点，不重启进程

#### 2.4 云端 LLM 调用没有重试和熔断
- **文件**: `ai_multi_provider.py`
- **问题**: `openai_compatible_chat_completion()` 和 `dispatch_chat_completion_messages()` 没有任何重试逻辑。网络抖动或 API 限速直接失败
- **影响**: 用户点「生成用例」→ 云端 API 临时 429 → 直接报错，没有自动重试
- **修复**:
  - 增加指数退避重试（最多 3 次，2s/4s/8s）
  - 增加熔断器：连续 5 次失败后熔断 60 秒，期间尝试切换到备用 provider
  - 429 响应自动等待 `Retry-After` 头指定的时间

### P1 - 高优先级

#### 2.5 自愈只在失败时触发，不做预判
- **文件**: `ai_selector_recovery.py`, `playwright_automation.py:9469`
- **修复**: 执行前用页面快照预判选择器健康度，提前准备候选

#### 2.6 自愈只扫描主框架，不处理 iframe/Shadow DOM
- **文件**: `ai_selector_recovery.py:61`
- **修复**: 复用 `ai_page_probe.py` 的多框架扫描逻辑

#### 2.7 自愈结果不跨次持久化
- **文件**: `hermes_heal_bridge.py:99-125`
- **修复**: 恢复成功后自动写入用例步骤的 `locator_candidates`，下次直接使用

#### 2.8 跨端编排的 UI 阶段没有自愈能力
- **文件**: `orchestrator.py:100-171`
- **修复**: 复用 `playwright_automation.py` 的执行引擎

#### 2.9 Anthropic 和 Gemini 不支持 tool calling 循环
- **文件**: `ai_multi_provider.py`
- **问题**: `dispatch_chat_completion_messages()` 对 Anthropic 和 Gemini 直接报错："不支持 AI chat tool loop"
- **影响**: 用户配置了 Claude 或 Gemini → 只能用单轮生成，无法使用 Agent 探索和自愈
- **修复**:
  - Anthropic: 实现 `tool_use` 格式的多轮调用（`content_block_start` → `input_json_delta` → `content_block_stop`）
  - Gemini: 实现 `function_call` / `function_response` 格式的多轮调用

### P2 - 中优先级

#### 2.10 探索引擎时间预算未生效
- **文件**: `explore/__init__.py:26-31`
- **修复**: 在循环中加入时间检查

#### 2.11 探索引擎不重新发现导航后的新页面元素
- **文件**: `exploration_engine.py:47-123`
- **修复**: 每次点击后检测 URL 变化，重新发现元素

#### 2.12 异常检测器定义了但从未在探索循环中调用
- **文件**: `exploration_engine.py:201-237`
- **修复**: 在循环中每步执行后调用检测

#### 2.13 `wait_for_human()` 是空函数
- **文件**: `sync_manager.py:68-70`
- **修复**: 实现基于 SSE 的人工确认机制

#### 2.14 清理阶段只能执行 API 操作
- **文件**: `orchestrator.py:266`
- **修复**: 按 layer 分派

---

## 三、多模态视觉模型集成优化（更新版）

### 3.1 视觉调用无重试、无熔断、180 秒超时
- **文件**: `ai_vision_local.py:87`
- **修复**:
  - 重试（最多 2 次，指数退避 2s/4s）
  - 熔断器（连续 3 次失败后熔断 60 秒）
  - 超时从 180 秒降到 60 秒
  - 云端 VLM 优先（GPT-4o / Claude Vision），本地 llava:7b 作降级

### 3.2 视觉模型只支持 Ollama 本地
- **文件**: `ai_vision_local.py`
- **问题**: 只调用 Ollama `/api/chat`，不支持云端多模态 API
- **修复**: 复用 `ai_multi_provider.py` 的多 provider 架构：
  - 新增 `vision_describe_cloud()` 函数，支持 GPT-4o Vision 和 Claude Vision
  - 配置优先级：云端 VLM（快、准）→ 本地 VLM（免费、离线）
  - 数据脱敏自动应用于云端视觉调用

### 3.3 视觉坐标解析缺少置信度评分
- **文件**: `ai_vision_grounding.py:113`
- **修复**: 增加置信度阈值（< 0.6 时降级为 DOM 选择器）

### 3.4 自愈降级链中 VLM 排在最后但通常更快更准
- **文件**: `ai_selector_recovery.py:299-314`
- **当前链**: Hermes → LLM → VLM
- **优化链**: 预判缓存候选(0ms) → DOM probe_index(100ms) → VLM 快速定位(2-5s) → LLM 精确匹配(5-15s) → Hermes 探索(30-60s)

### 3.5 视觉就绪检查重复调用
- **文件**: `ai_llm_readiness.py`, `vision_platform_readiness.py`
- **修复**: 统一就绪检查服务，结果缓存 30 秒

---

## 四、LLM/Agent/MCP/Skills 协作优化

### 4.1 当前架构的核心问题

| 问题 | 描述 |
|------|------|
| **LLM 调用是纯阻塞的** | `dispatch_chat_completion_messages()` 使用 `stream: False`，等完整响应 |
| **Tool calling 循环没有进度反馈** | `run_ai_chat_with_tools()` 跑 18 轮，前端只看到最终结果 |
| **MCP 只是 stdio JSON 协议** | 没有用标准 MCP JSON-RPC 2.0，外部工具无法直接调用 |
| **Skills 没有版本管理** | SKILL.md 覆盖写入，无法回滚到旧版本 |
| **Hermes Agent 和平台 LLM 共享同一个 provider 配置** | 如果云端 API 挂了，Hermes 也挂了 |

### 4.2 流式 Tool Calling（核心优化）

**目标**: LLM 的每一轮推理和 tool call 都实时推送到前端

**技术方案**:

```
POST /api/ai/task/chat-stream  →  SSE 响应

event: thinking
data: {"round": 1, "content": "我需要先探索登录页面..."}

event: tool_call_start
data: {"round": 1, "tool": "hermes_execute", "args_summary": "导航到登录页..."}

event: tool_call_progress
data: {"round": 1, "tool": "hermes_execute", "status": "Hermes 正在操作浏览器..."}

event: tool_call_result
data: {"round": 1, "tool": "hermes_execute", "result_preview": "发现登录表单..."}

event: thinking
data: {"round": 2, "content": "已获取页面信息，现在生成测试步骤..."}

event: tool_call_start
data: {"round": 2, "tool": "refine_test_plan", "args_summary": "基于探索结果生成用例"}

event: plan_update
data: {"plan": {...}, "step_count": 5}

event: done
data: {"total_rounds": 2, "plan": {...}}
```

**后端改动**:
- `ai_multi_provider.py`: 增加 `stream=True` 模式的 OpenAI 兼容调用
- `ai_chat_tool_loop.py`: 改为 generator，每轮 yield SSE 事件
- `app.py`: 新增 `/api/ai/task/chat-stream` SSE 端点

**前端改动**:
- `ai_test.html`: 用 `EventSource` 或 `fetch + ReadableStream` 消费 SSE
- 解析事件类型，动态插入思考气泡和 tool call 进度

### 4.3 MCP 标准化（对外暴露能力）

**当前**: `testory_mcp/web.py` 使用自定义 stdio JSON 协议
**目标**: 升级为标准 MCP JSON-RPC 2.0 over Streamable HTTP

**好处**:
- Cursor、Claude Desktop、VS Code 等 MCP 客户端可以直接调用 Testory 的测试能力
- 用户可以在 Cursor 中说 "用 Testory 测试这个登录页面"，Cursor 自动调用 Testory MCP 工具
- 生态效应：Testory 成为 MCP 工具生态的一部分

**实现**:
- 将 `testory_mcp/kit.py` 的工具定义转为 MCP 标准 `inputSchema` 格式
- 增加 HTTP+SSE 传输层（`/mcp` 端点）
- 增加 MCP Resources（暴露测试报告、用例列表为可读资源）

### 4.4 Skills 增强

**当前**: SKILL.md 单文件，覆盖写入，3 次成功自动导出
**增强**:

1. **版本管理**: SKILL.md 增加 `version` 字段，每次更新创建 `{skill_id}_v{n}.md`
2. **跨用例学习**: 选择器恢复时，先查询所有 Skill 中是否有相同元素描述的已知选择器
3. **Skill 搜索**: 向量化 SKILL.md 内容，支持语义搜索（"找一个登录测试的 Skill"）
4. **Skill 组合**: 支持 Skill 之间的调用（登录 Skill → 下单 Skill → 支付 Skill）

### 4.5 Hermes Agent 独立性增强

**当前**: Hermes 和平台 LLM 共享同一个 provider 配置
**问题**: 云端 API 挂了 → 平台 LLM 挂了 → Hermes 也挂了 → 整个系统不可用
**修复**:
- Hermes 支持独立的 LLM provider 配置（`HERMES_LLM_PROVIDER` 环境变量）
- 可以配置为：平台用云端 DeepSeek，Hermes 用本地 Ollama
- 这样云端 API 挂了，Hermes 仍然可以独立工作

---

## 五、前端 UI/UX 性能优化

### 5.1 Tailwind CSS 运行时 JIT 是最大的渲染阻塞源
- **文件**: `base.html:29/36`
- **修复**: 预构建 Tailwind CSS，用 `<link>` 加载静态 CSS 文件

### 5.2 base.html 内联 2192 行 JS（翻译字典）
- **文件**: `base.html:115-2307`
- **修复**: 提取为独立 `.js` 文件，用 `<script defer>` 加载

### 5.3 7-8 个渲染阻塞 CSS 文件
- **文件**: `base.html:9-37`
- **修复**: 非关键 CSS 用 `media="print" onload="this.media='all'"` 延迟加载

### 5.4 WebSocket 画布帧渲染性能差
- **文件**: `ai_test.html:1600-1652`
- **问题**: 每帧 `new Image()` + base64 解码，无帧率限制
- **修复**: `createImageBitmap()` + Blob URL + `requestAnimationFrame` 30fps 限制

### 5.5 ai_test.html 3500 行内联 JS
- **文件**: `ai_test.html:1237-4700`
- **修复**: 拆分为 canvas、chat、model、settings 四个独立 `.js` 模块

### 5.6 页面加载时 5-6 个串行 API 请求
- **文件**: `ai_test.html:4051-4110`
- **修复**: `Promise.all()` 并行化

### 5.7 缺少 CSS containment 和 will-change
- **修复**: Sticky nav `will-change: transform`，Canvas `contain: strict`，拖拽面板 `will-change: transform`

### 5.8 移动端截屏 500ms 轮询
- **文件**: `ai_test.html:4383`
- **修复**: 改为 WebSocket 推送或降低到 1000ms

---

## 六、实施路线图

### 阶段 1：后端可靠性 + 云端 LLM 加固（2 周）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 1.1 Job Store 迁移到 SQLite | `app.py` | 2 天 |
| 1.2 LLM 调用支持取消中断 | `app.py`, `ai_multi_provider.py` | 1 天 |
| 1.3 Hermes CDP 热更新 | `hermes_config.py`, `hermes_service_bootstrap.py` | 1 天 |
| 1.4 云端 LLM 重试 + 熔断器 | `ai_multi_provider.py` | 1.5 天 |
| 1.5 自愈结果持久化到 locator_candidates | `hermes_heal_bridge.py`, `ai_selector_recovery.py` | 1 天 |
| 1.6 自愈扫描支持 iframe/Shadow DOM | `ai_selector_recovery.py` | 1 天 |
| 1.7 视觉调用重试 + 熔断 + 超时优化 | `ai_vision_local.py` | 1 天 |
| 1.8 Anthropic tool_use 多轮支持 | `ai_multi_provider.py` | 1.5 天 |
| 1.9 Hermes Agent 独立 LLM 配置 | `hermes_config.py` | 0.5 天 |

### 阶段 2：流式反馈 + 视觉多 provider（1.5 周）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 2.1 OpenAI 兼容 streaming 模式 | `ai_multi_provider.py` | 2 天 |
| 2.2 Anthropic streaming tool_use 模式 | `ai_multi_provider.py` | 1.5 天 |
| 2.3 chat-stream SSE 端点 | `app.py`, `ai_chat_tool_loop.py` | 2 天 |
| 2.4 云端视觉模型（GPT-4o Vision / Claude Vision） | `ai_vision_local.py`, `ai_multi_provider.py` | 2 天 |
| 2.5 自愈降级链调整（VLM 优先） | `ai_selector_recovery.py` | 0.5 天 |
| 2.6 视觉就绪检查统一缓存 | `vision_platform_readiness.py` | 0.5 天 |

### 阶段 3：前端 UI/UX 性能优化（1.5 周）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 3.1 Tailwind 预构建 | `base.html`, 构建脚本 | 2 天 |
| 3.2 翻译字典提取 + 非关键 CSS 延迟加载 | `base.html` | 1 天 |
| 3.3 WebSocket 画布优化 | `ai_test.html` | 1.5 天 |
| 3.4 ai_test.html JS 模块拆分 | `ai_test.html`, `static/js/` | 2 天 |
| 3.5 页面加载并行化 + CSS containment | `ai_test.html`, `testory-brand.css` | 1 天 |
| 3.6 移动端截屏改 WebSocket | `ai_test.html` | 1 天 |

### 阶段 4：前端交互升级 + MCP + 探索引擎（1.5 周）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 4.1 AI Test 思考气泡 + SSE 解析 | `ai_test.html`, `ai_hub.css` | 2 天 |
| 4.2 MCP 标准化（JSON-RPC 2.0 + Streamable HTTP） | `testory_mcp/` | 3 天 |
| 4.3 探索引擎修复（时间预算 + 导航重发现 + 异常检测） | `exploration_engine.py` | 1 天 |
| 4.4 跨端编排复用 Playwright 执行引擎 | `orchestrator.py` | 1.5 天 |
| 4.5 Skills 版本管理 + 跨用例学习 | `ai_hermes_skills.py`, `hermes_skill_loop.py` | 1 天 |

---

## 七、预期效果

| 维度 | 当前 | 优化后 |
|------|------|--------|
| 云端 LLM 可靠性 | 无重试，API 限速直接失败 | 重试 + 熔断 + 备用 provider 自动切换 |
| LLM 多 provider | Anthropic/Gemini 不支持 tool calling | 全部支持 |
| AI 生成反馈 | 等 3 分钟后一次性返回 | 实时 SSE 流式思考 + 逐步生成 |
| 视觉模型 | 仅 Ollama llava:7b | 云端 GPT-4o/Claude + 本地多模型降级 |
| 自愈延迟 | 失败后 10-30 秒 | 缓存候选 0ms + VLM 2-5s |
| MCP 生态 | 自定义 stdio JSON | 标准 MCP JSON-RPC 2.0，外部工具可调用 |
| Skills 知识 | 单文件覆盖写入 | 版本管理 + 跨用例学习 + 语义搜索 |
| Hermes 独立性 | 与平台 LLM 共享 provider | 独立 LLM 配置，云端挂了仍可工作 |
| 页面首次渲染 | Tailwind JIT 阻塞 ~1s | 预构建 CSS，无阻塞 |
| 画布帧率 | 无限制，GC 压力大 | 30fps + Blob URL + createImageBitmap |

---

## 八、风险与约束

1. **Anthropic/Gemini tool calling**: 两者的 tool use 格式与 OpenAI 不同，需要分别实现解析器
2. **SSE 流式 + tool calling**: 需要在流式响应中手动拼接 tool call 的增量 JSON 片段
3. **MCP 标准化**: 需要引入 `mcp` Python 包依赖，当前 `testory_mcp/web.py` 有 stub 但未完成
4. **Tailwind 预构建**: 改变当前零构建部署方式，需要 CI/CD 管道支持
5. **云端视觉模型**: 需要额外 API Key，数据脱敏自动应用（`cloud_desensitizer.py` 已有基础）
6. **ai_test.html 拆分**: 3500 行内联 JS 拆分需要处理 200+ 个全局变量和函数依赖
