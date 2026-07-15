# Demo 模拟 vs 平台真实能力 —— 深度对比分析

## 一、核心差异总览

| 维度 | Demo 模拟 | 平台真实能力 | 差距 |
|------|----------|------------|------|
| AI 用例生成 | 预设 4 个场景的固定代码片段 | 真实 LLM 推理 + 页面探测 + DOM 快照 | **Demo 大幅简化** |
| AI 自主测试 | 固定 6 步时间线动画 | Hermes Agent 真实浏览器探索 + 异常检测 | **Demo 大幅简化** |
| AI 用例自愈 | 预设日志动画 | LLM + VLM + 向量记忆 + 验证回环 | **Demo 大幅简化** |
| 页面探测 | 无 | 40+ UI 框架 + Shadow DOM + iframe | **Demo 完全缺失** |
| 多模型支持 | 无 | Ollama / OpenAI / Anthropic / Gemini | **Demo 完全缺失** |
| 跨端编排 | 无 | API + Web + Desktop + Mobile 联动 | **Demo 完全缺失** |

---

## 二、逐模块详细对比

### 1. AI 用例生成

**Demo 做了什么：**
- 用户输入「测试电商登录」→ 预设好的 Python 代码片段逐字出现
- 4 个固定场景，每个对应一段手写代码
- 本质是「打字机动画」

**平台实际做了什么：**
1. 用户输入需求后，先用 Playwright **无头浏览器**打开目标页面
2. 执行 `_COLLECT_INTERACTIVE_JS` 扫描页面所有可交互元素（支持 Element UI、Ant Design、Arco、Naive UI、MUI、Vuetify、TDesign 等 40+ UI 框架）
3. 构建「DOM 快照」（probe registry），包含每个元素的推荐选择器、优先级评分
4. 将快照 + 用户需求 + 记忆上下文一起发送给 LLM
5. LLM 生成结构化 JSON 测试计划
6. `_normalize_output()` 规范化步骤、映射 probe_index 到真实选择器
7. `clamp_plan_steps_to_probe_registry()` 将选择器约束为页面上真实存在的元素
8. `heuristic_repair_plan_selectors_from_registry()` 修复常见错误（密码字段名不匹配、登录按钮 XPath 问题等）
9. `ground_plan_assertions_with_replay()` 在无头浏览器中回放所有步骤，自动修正断言

**关键差距：**
- Demo 是**静态预设**，平台是**动态生成**
- Demo 没有页面探测，平台会先扫描真实页面再生成用例
- Demo 的选择器是手写的，平台的选择器经过 6 层修正
- Demo 不支持多轮优化，平台支持 `refine_case_and_steps()` 多轮对话调整

### 2. AI 自主测试

**Demo 做了什么：**
- 左侧模拟电商页面，右侧 6 步时间线
- 按钮点击后按固定时间依次高亮元素 + 显示思考气泡
- 最后弹出一个预设的 Bug
- 本质是「CSS 动画 + 定时器」

**平台实际做了什么：**
- **Hermes Agent**：通过 `ai_chat_tool_loop.py` 实现最多 18 轮的 tool calling 循环
  - `hermes_execute` 工具：将自然语言指令发送给 Hermes Agent Gateway
  - Agent 在真实浏览器中导航、点击、输入、截图
  - 支持多种 scope：smoke / module / e2e / explore / regression / integration
- **探索引擎** (`ai_modules/explore/exploration_engine.py`)：
  - `WebExplorer`：发现页面所有可交互元素 → 按优先级逐一点击 → 截图记录 → 检测异常
  - `DesktopExplorer`：将屏幕划分为 4×3 网格 → 随机点击各区域
  - `AnomalyDetector`：用 NumPy + PIL 检测白屏和低对比度
- **跨端编排** (`ai_modules/execute/orchestrator.py`)：
  - 将自然语言业务流程分解为 API + Web + Desktop + Mobile 多层计划
  - `CrossEndContext` 在不同层之间传递变量（如 `{{auth_token}}`）
  - `SyncPointManager` 管理层间依赖
  - `RecoveryEngine` 处理失败（重试/跳过/中止）

**关键差距：**
- Demo 是**预设动画**，平台是**真实浏览器操作**
- Demo 只模拟了一个电商场景，平台可以测试**任意网站**
- Demo 无法处理异常，平台有异常检测 + 重试机制
- Demo 只能 Web，平台支持**跨端编排**（API + Web + 桌面 + 移动）

### 3. AI 用例自愈

**Demo 做了什么：**
- 左侧代码面板高亮失败行 → 删除旧代码 → 显示新代码
- 右侧日志面板逐行显示预设的自愈过程
- 底部显示修复前后对比
- 本质是「CSS 类名切换 + 定时器」

**平台实际做了什么：**
平台有 **4 层自愈体系**：

**第 1 层：执行前修复（无需 LLM）**
- `heuristic_repair_plan_selectors_from_registry()`：修复密码字段名不匹配、登录按钮定位、Toast 断言选择器等常见错误
- `resolve_plan_steps_locators_with_snapshot()`：将模糊选择器映射到真实 DOM 元素

**第 2 层：运行时 LLM 恢复**
- `try_recover_selector_with_llm()`：步骤失败时，重新探测当前页面，将元素列表发送给 LLM，让 LLM 选择正确元素
- 降级链：Hermes Agent → LLM → VLM

**第 3 层：VLM 视觉定位**
- `try_recover_selector_with_vision()`：截取当前页面截图，发送给多模态视觉模型（如 llava:7b），返回元素坐标
- 支持 0-1 比例坐标、像素坐标、Qwen-VL 的 0-1000 网格格式

**第 4 层：向量记忆 + 验证回环**
- `ai_memory_store.py`：用 Ollama 嵌入模型生成向量，存储到 SQLite，支持余弦相似度 Top-K 检索
- `ingest_repair_case()`：将成功的修复记录到向量记忆中，下次遇到类似问题时自动参考
- `heal_verifier.py`：修复后**重新执行**步骤，确认修复有效，无效则回滚

**关键差距：**
- Demo 只展示了「选择器替换」这一种修复，平台有 4 层修复策略
- Demo 没有视觉模型，平台有 VLM 视觉定位作为最后防线
- Demo 没有记忆，平台有向量 RAG 记忆，越用越准
- Demo 没有验证，平台修复后会重新执行确认

---

## 三、Demo 完全缺失的能力

以下能力在 Demo 中**完全没有展示**：

### 1. 页面探测（Page Probing）
- 2399 行代码的 `ai_page_probe.py`
- 支持 40+ UI 框架的元素收集
- Shadow DOM 穿透
- iframe 扫描
- 元素优先级评分系统

### 2. 多模型支持
- Ollama（本地）
- OpenAI 兼容（MiniMax、小米 MiMo、自定义）
- Anthropic Claude
- Google Gemini
- 自动数据脱敏的云网关

### 3. 视觉 AI
- VLM 元素定位（截图 → 坐标）
- 视觉断言（截图 → 自然语言 YES/NO）
- OCR 区域识别
- 验证码视觉求解

### 4. 跨端编排
- API + Web + Desktop + Mobile 四层联动
- 变量传递（`{{auth_token}}`）
- 同步点管理
- 清理阶段自动注入

### 5. 对话式测试
- AI Chat with Tool Calling（最多 18 轮）
- 愤怒/焦虑/友好/专业 4 种人格模拟
- 聊天机器人安全检测

### 6. 需求文档解析
- 上传需求文档 → 结构化场景 → 测试用例
- 支持分块处理大型文档
- 等价类划分、边界值、决策表等多种测试设计方法

---

## 四、哪一个更好？

### 答案：**平台的真实能力远超 Demo 的模拟**

| 维度 | Demo | 平台 | 评价 |
|------|------|------|------|
| 震撼力 | ⭐⭐⭐⭐ | ⭐⭐ | Demo 更有视觉冲击力 |
| 技术深度 | ⭐ | ⭐⭐⭐⭐⭐ | 平台是真正的 AI 工程 |
| 实用性 | ⭐ | ⭐⭐⭐⭐⭐ | 平台可以真正解决测试问题 |
| 可信度 | ⭐⭐ | ⭐⭐⭐⭐⭐ | 平台每一步都有真实代码支撑 |
| 差异化 | ⭐⭐ | ⭐⭐⭐⭐⭐ | 平台的能力组合是市场唯一的 |

**Demo 的问题：**
- 评委可能觉得「这和其他 AI 测试工具差不多」
- 没有展示平台的独特技术壁垒（页面探测、4 层自愈、跨端编排）
- 预设动画容易被质疑「是不是假的」

**平台的优势：**
- 40+ UI 框架的页面探测 → 竞品做不到
- 4 层自愈体系 → 竞品只做 1-2 层
- 跨端编排 → 竞品没有
- 向量记忆 → 越用越准
- 多模型支持 → 不依赖单一供应商

---

## 五、建议：如何改进 Demo

### 方案 A：保持当前 Demo 不变（快速方案）
- 优点：已完成，视觉效果好
- 缺点：没有展示真实技术壁垒
- 适合：时间紧迫，先交再说

### 方案 B：在 Demo 中增加「技术亮点说明」（推荐）
- 在每个 tab 的描述中加入技术细节
- 例如：「支持 40+ UI 框架自动探测」「4 层自愈：规则 → LLM → VLM → 向量记忆」
- 用数据和细节增强可信度
- 工作量：0.5 天

### 方案 C：录制真实操作视频嵌入 Demo（最佳）
- 录制平台实际生成用例、自愈、探索的屏幕录像
- 嵌入到 Demo 页面的 `<video>` 标签中
- 评委可以看到真实产品运行
- 工作量：1 天

### 方案 D：在 Demo 中嵌入真实产品截图（折中）
- 截取平台的真实 UI（AI 测试页、AI Hub、AI Heal 页）
- 在 Demo 中作为「真实产品界面」展示
- 工作量：0.5 天
