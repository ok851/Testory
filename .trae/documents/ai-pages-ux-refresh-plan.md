# AI 页面 UX 刷新计划 v2（增强版）

## 摘要

本计划针对用户反馈的三大核心问题，并额外强化「高级感」「思考流程」「任务完成流程列表」三个视觉/交互维度：

1. **FOUC 缺陷**：进入/切换页面时仍出现无样式闪烁
2. **AI 自主测试页面过度复杂**：功能冗余，只保留浏览器画面 + 底部指令输入框
3. **AI 生成用例 & AI 自愈**：参考 demo 对话式交互，自愈去代码化
4. **【新增】高级感视觉**：毛玻璃、光晕、脉冲、渐变边框等现代 AI 产品质感
5. **【新增】思考流程可视化**：AI 工作时的思维链实时展示（类似 ChatGPT o1 推理过程）
6. **【新增】任务完成流程列表**：任务结束后展示带步骤状态、耗时、成功率的精美总结面板

本次刷新只改前端展示层，后端 API 100% 保持兼容。

---

## 当前状态分析

### FOUC 根因

`base.html` 中 Tailwind 通过 CDN 以 `defer` 方式加载。Tailwind JIT 编译器需要在 DOM 构建完成后扫描类名并生成 CSS。`<body class="bg-gray-50 min-h-screen text-gray-900 ...">` 完全依赖 Tailwind utility classes。在编译完成前，body 没有任何背景色/文字色样式，导致每次页面跳转都出现白色无样式页面。

### AI 自主测试页面现状

`ai_test.html` 当前 3500+ 行内联 JS/CSS，功能堆砌：顶栏、工作流步骤条、上下文选择区、AI 聊天区、浏览器嵌入区、用例预览区、设置抽屉。Demo 中仅展示：中间大画幅浏览器 + 底部简单指令输入框 + 右侧时间线。

### AI 生成用例页面现状

`ai_design.html` 为表单式后台管理风格：项目选择、模型选择、URL、需求文本、文件上传、平台 tab、草案列表。Demo 中为对话式交互：AI 助手气泡 → 快捷示例芯片 → 单一输入框 → 生成按钮。

### AI 自愈页面现状

`ai_heal.html` 有 5 个 section 纵向堆叠：使用说明、定位器预修复、失败诊断、步骤审查助手、Skills 维护、健康检查。定位器预修复直接展示 CSS/XPath 代码，违背"无代码"定位。

### 已有可复用资产

- **思考气泡**：`ai_assistant.js` 中 `showThinkingBubble`/`updateThinkingStep`/`markLastStepDone`，`ai_hub.css` 中 `.ai-thinking-bubble` 样式
- **打字动画**：`ai_design.js` 中 `typeText`
- **日志动画**：`ai_heal.js` 中 `renderAnimatedLog`
- **Diff 视图**：`ai_heal.js` 中 `renderDiffView`
- **工作流条**：`ai_test.html` 中 `.ai-workflow-bar`
- **渐变按钮/卡片**：`ai_hub.css` 中 `.ai-btn--primary`、`.ai-module-shell`

---

## 决策与假设

1. **Tailwind 加载策略**：去掉 `defer`，改为同步加载。彻底消除 FOUC。
2. **AI Test 简化范围**：保留浏览器画面 + 底部指令输入框 + 极简顶栏。隐藏聊天历史面板、工作流步骤条、上下文选择区、用例预览区、设置抽屉。
3. **主题风格**：保持现有产品浅色渐变主题，但添加 `dark` 模式适配。
4. **AI 自愈"无代码"**：定位器预修复结果改用自然语言描述，不展示原始选择器代码。
5. **高级感实现**：使用 CSS `backdrop-filter`、渐变边框、脉冲动画、`box-shadow` 光晕。不引入新依赖。
6. **思考流程**：复用现有 `showThinkingBubble` 基础设施，但增强视觉效果（脉冲圆点、渐变边框、毛玻璃背景）。
7. **任务完成流程列表**：任务结束后动态生成一个"任务总结卡片"，展示步骤时间线、状态图标、耗时统计。

---

## 具体改动方案

### 改动 1：彻底修复 FOUC

**文件**: `templates/base.html`

**What**: 去掉 Tailwind CDN 的 `defer` 属性，改为同步加载。

**How**:
```html
<!-- 修改前 -->
<script src="https://cdn.tailwindcss.com" defer></script>
<!-- 修改后 -->
<script src="https://cdn.tailwindcss.com"></script>
```
本地 vendors 路径同理去掉 `defer`。

**Why**: Tailwind JIT 编译必须在 body 渲染前完成，否则 `bg-gray-50`、`text-gray-900` 等 utility classes 在初始 paint 时失效。`defer` 导致编译在 DOMContentLoaded 之后才开始，这是 FOUC 的根因。

---

### 改动 2：AI 自主测试页面大幅简化 + 高级感 + 思考流程 + 任务完成列表

**文件**: `templates/ai_test.html`、`static/js/ai_assistant.js`、`static/css/ai_hub.css`

#### 2a 布局简化

**What**: 将当前复杂的多区域布局简化为「中间浏览器 + 底部指令输入」的 demo 风格。

**How**:
1. **隐藏/移除以下元素**（加 `display:none` 或注释掉，便于后续恢复）：
   - `.ai-workflow-bar`（工作流步骤条）
   - `.ai-test-context`（项目选择 + URL 输入区）
   - `.ai-stack-ai` 中的聊天历史面板（保留但折叠/隐藏，仅保留输入区）
   - `.ai-preview-section`（用例预览区）
   - `.ai-settings-panel` 触发按钮及抽屉
   - `.ai-browser-toolbar` 中的大部分元信息展示

2. **保留的核心元素**：
   - `.ai-test-topbar`：保留返回按钮 + 标题 "AI 自主测试"，模型芯片简化为一个小图标
   - `.ai-browser-panel`：保留地址栏 + iframe/画布 + 前进/后退/刷新
   - 底部固定输入栏：一个全宽输入框 + 发送按钮

#### 2b 思考流程可视化（新增）

**What**: AI 执行过程中，在浏览器右侧（或底部折叠面板）实时展示思维链。

**How**:
1. **思维链数据结构**（前端静态定义，根据 SSE/轮询状态推进）：
   ```js
   var AI_THINKING_CHAIN = [
     { id: 'understand', text: '正在理解测试目标…', icon: '🧠' },
     { id: 'navigate', text: '正在导航到目标页面…', icon: '🌐' },
     { id: 'probe', text: '正在探测页面元素…', icon: '🔍' },
     { id: 'plan', text: '正在制定执行计划…', icon: '📋' },
     { id: 'execute', text: '正在执行测试步骤…', icon: '▶️' },
     { id: 'verify', text: '正在验证结果…', icon: '✅' }
   ];
   ```

2. **思维链 UI 组件**：
   - 在浏览器右侧悬浮一个窄面板（宽度 260px），背景使用毛玻璃效果：`background: rgba(255,255,255,0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.3);`
   - 面板标题："AI 思考中"，带脉冲动画圆点
   - 每个步骤一行：图标 + 描述文字 + 状态指示器
   - 状态指示器：
     - 等待中：灰色空心圆
     - 进行中：渐变脉冲圆点（`animation: pulse-dot 1.5s infinite`）
     - 已完成：绿色对勾 + 文字变灰
   - 当前进行中的步骤文字使用渐变色彩动画

3. **CSS 新增**（`ai_hub.css`）：
   ```css
   .ai-thinking-chain {
     background: rgba(255,255,255,0.75);
     backdrop-filter: blur(16px);
     border: 1px solid rgba(255,255,255,0.4);
     border-radius: 16px;
     padding: 16px;
     box-shadow: 0 8px 32px rgba(79,70,229,0.12);
   }
   .ai-thinking-chain__title {
     font-size: 13px; font-weight: 700; color: #3730a3;
     display: flex; align-items: center; gap: 8px; margin-bottom: 12px;
   }
   .ai-thinking-chain__pulse {
     width: 8px; height: 8px; border-radius: 50%;
     background: linear-gradient(135deg, #4f46e5, #a78bfa);
     animation: aiPulse 1.4s ease-in-out infinite;
   }
   @keyframes aiPulse {
     0%,100% { transform: scale(1); opacity: 1; box-shadow: 0 0 0 0 rgba(79,70,229,0.4); }
     50% { transform: scale(1.3); opacity: 0.8; box-shadow: 0 0 0 8px rgba(79,70,229,0); }
   }
   .ai-thinking-chain__step {
     display: flex; align-items: center; gap: 10px;
     padding: 8px 0; font-size: 13px; color: #64748b;
     transition: color 0.3s;
   }
   .ai-thinking-chain__step--active { color: #3730a3; font-weight: 600; }
   .ai-thinking-chain__step--done { color: #94a3b8; }
   .ai-thinking-chain__dot {
     width: 18px; height: 18px; border-radius: 50%;
     display: flex; align-items: center; justify-content: center;
     font-size: 10px; flex-shrink: 0;
   }
   .ai-thinking-chain__dot--wait { border: 2px solid #cbd5e1; background: #fff; }
   .ai-thinking-chain__dot--active {
     background: linear-gradient(135deg, #4f46e5, #6366f1);
     color: #fff;
     animation: aiPulse 1.4s ease-in-out infinite;
   }
   .ai-thinking-chain__dot--done { background: #34d399; color: #fff; }
   ```

4. **JS 适配**（`ai_assistant.js`）：
   - 在 `aiGeneratePreview` 中，job 开始后初始化思维链面板
   - 根据轮询返回的进度状态推进步骤（如 `planning` → `probing` → `executing`）
   - job 完成后移除思维链面板，触发「任务完成总结」展示

#### 2c 任务完成流程列表（新增）

**What**: AI 任务完成后，展示一个精美的「任务总结卡片」，包含步骤时间线、状态、耗时。

**How**:
1. **数据结构**（基于 `latestAiPlan` 和 `execution` 结果）：
   ```js
   // 从 latestAiPlan.steps 和 execution.results 生成
   var taskSummary = {
     caseName: latestAiPlan.case_name,
     totalSteps: latestAiPlan.steps.length,
     okSteps: execution.results.filter(r => r.status === 'ok').length,
     failSteps: execution.results.filter(r => r.status === 'fail').length,
     duration: execution.duration_ms,
     steps: latestAiPlan.steps.map((s, i) => ({
       description: s.description,
       status: execution.results[i]?.status || 'unknown',
       duration: execution.results[i]?.duration_ms
     }))
   };
   ```

2. **UI 组件**：
   - 在浏览器下方或聊天区域展示一个「任务总结」卡片
   - 卡片顶部：用例名称 + 总耗时 + 成功率环形图（CSS `conic-gradient` 实现）
   - 卡片中部：折叠式步骤列表，每行展示：
     - 步骤序号（圆形徽章）
     - 步骤描述
     - 状态图标（✅ 绿色 / ❌ 红色 / ⏭️ 灰色跳过）
     - 单步耗时
   - 卡片底部：操作按钮（「重新执行」「查看详情」「保存用例」）

3. **CSS 新增**（`ai_hub.css`）：
   ```css
   .ai-task-summary {
     background: rgba(255,255,255,0.85);
     backdrop-filter: blur(20px);
     border: 1px solid rgba(255,255,255,0.5);
     border-radius: 20px;
     padding: 24px;
     box-shadow: 0 12px 40px rgba(79,70,229,0.1);
     animation: aiTaskSummaryIn 0.5s ease both;
   }
   @keyframes aiTaskSummaryIn {
     from { opacity: 0; transform: translateY(20px) scale(0.96); }
     to { opacity: 1; transform: translateY(0) scale(1); }
   }
   .ai-task-summary__header {
     display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px;
   }
   .ai-task-summary__ring {
     width: 64px; height: 64px; border-radius: 50%;
     background: conic-gradient(#34d399 var(--pct), #e2e8f0 0);
     display: flex; align-items: center; justify-content: center;
     position: relative;
   }
   .ai-task-summary__ring::after {
     content: ''; position: absolute; width: 48px; height: 48px; border-radius: 50%; background: #fff;
   }
   .ai-task-summary__ring span { position: relative; z-index: 1; font-size: 13px; font-weight: 700; color: #3730a3; }
   .ai-task-summary__step {
     display: flex; align-items: center; gap: 12px;
     padding: 10px 12px; border-radius: 10px; margin-bottom: 6px;
     background: rgba(241,245,249,0.6);
     transition: background 0.2s;
   }
   .ai-task-summary__step:hover { background: rgba(226,232,240,0.8); }
   .ai-task-summary__badge {
     width: 28px; height: 28px; border-radius: 50%;
     display: flex; align-items: center; justify-content: center;
     font-size: 12px; font-weight: 700; flex-shrink: 0;
   }
   .ai-task-summary__badge--ok { background: #dcfce7; color: #166534; }
   .ai-task-summary__badge--fail { background: #fee2e2; color: #991b1b; }
   .ai-task-summary__badge--skip { background: #f1f5f9; color: #64748b; }
   ```

4. **深色模式**：所有新增组件添加 `html.dark` 适配。

---

### 改动 3：AI 生成用例页面改为对话式交互 + 思考流程 + 任务完成列表

**文件**: `templates/ai_design.html`、`static/js/ai_design.js`、`static/css/ai_hub.css`

#### 3a 对话式布局

**What**: 将表单式布局改为 demo 风格的对话式交互界面。

**How**:
1. **HTML 结构调整** (`ai_design.html`)：
   - 移除：项目选择下拉框（后台默认选中当前项目）、模型选择下拉框（复用全局默认模型）、基础 URL 输入框（改为可选的高级设置折叠区）、文件上传（改为可选）
   - 新增：
     - AI 助手气泡区域："你好！我是 Testory AI 助手。告诉我你的测试需求，我会自动生成完整的测试用例。"
     - 快捷示例芯片按钮：「电商登录测试」「支付安全测试」「APP 注册测试」「API 异常测试」
     - 单一输入框：placeholder="描述你的测试需求，例如：测试购物车的增删改查功能"
     - 「生成用例」主按钮（渐变 + 光晕）

2. **平台 Tab 处理**：
   - Web / API / Android 的平台选择改为输入框上方的小图标切换（🌐 / 🔌 / 📱），默认自动推断

#### 3b 思考流程可视化（新增）

**What**: 生成过程中展示 AI 的思维链。

**How**:
1. **思维链步骤**：
   ```js
   var DESIGN_THINKING_CHAIN = [
     { id: 'parse', text: '正在解析测试需求…', icon: '📝' },
     { id: 'probe', text: '正在探测目标页面元素…', icon: '🔍' },
     { id: 'analyze', text: '正在分析业务场景…', icon: '🧩' },
     { id: 'generate', text: '正在生成测试用例…', icon: '✨' },
     { id: 'validate', text: '正在验证步骤可行性…', icon: '🔒' }
   ];
   ```

2. **UI 展示**：
   - 点击「生成用例」后，在输入框下方展开思维链面板
   - 使用与 AI Test 相同的 `.ai-thinking-chain` 组件样式
   - 每个步骤推进时伴随打字机动画更新状态文字
   - 生成完成后，思维链面板折叠，展示「任务完成总结」

#### 3c 任务完成流程列表（新增）

**What**: 生成完成后展示草案总结卡片。

**How**:
1. **UI 组件**：
   - 生成完成后，在页面中部展示「生成总结」卡片
   - 卡片顶部：「生成完成」+ 用例数量 + 成功率环形图
   - 卡片中部：草案列表（复用现有 draft card 但增强视觉效果）
     - 每个草案卡片增加毛玻璃背景、hover 光晕、入场动画（`draftCardIn` 已存在，增强为 stagger 延迟）
   - 卡片底部：「全选并保存」按钮

2. **草案卡片增强 CSS**：
   ```css
   .ai-design-draft-card {
     background: rgba(255,255,255,0.8);
     backdrop-filter: blur(8px);
     border: 1px solid rgba(255,255,255,0.5);
     border-radius: 14px;
     padding: 16px;
     box-shadow: 0 4px 20px rgba(79,70,229,0.08);
     transition: transform 0.2s, box-shadow 0.2s;
   }
   .ai-design-draft-card:hover {
     transform: translateY(-2px);
     box-shadow: 0 8px 30px rgba(79,70,229,0.15);
   }
   ```

---

### 改动 4：AI 自愈页面简化 + 去代码化 + 思考流程 + 任务完成列表

**文件**: `templates/ai_heal.html`、`static/js/ai_heal.js`、`static/css/ai_hub.css`

#### 4a 布局简化 + 去代码化

**What**: 精简为 2 个核心功能（定位器预修复 + 失败诊断），移除代码展示，改用自然语言描述。

**How**:
1. **移除以下 section**：使用说明、步骤审查助手、Skills 维护、健康检查
2. **两个功能改为 Tab 切换**：「定位器修复」/「失败诊断」
3. **定位器预修复改造**：
   - 结果不再使用 `renderDiffView` 的代码对比视图
   - 改为：每个步骤一行，展示自然语言描述变更：
     - 🟢 「登录按钮」定位方式已优化（从 XPath 改为 data-testid）
     - ⚪ 「用户名输入框」定位方式保持不变
     - 🟡 「提交表单」建议增加等待条件
   - 用 `.heal-diff-line` 动画样式但内容改为描述文本

#### 4b 思考流程可视化（新增）

**What**: 修复过程中展示 AI 的思维链。

**How**:
1. **思维链步骤**：
   ```js
   var HEAL_THINKING_CHAIN = [
     { id: 'scan', text: '正在扫描用例步骤…', icon: '🔍' },
     { id: 'probe', text: '正在探测当前页面元素…', icon: '🌐' },
     { id: 'compare', text: '正在对比定位器有效性…', icon: '⚖️' },
     { id: 'repair', text: '正在生成修复方案…', icon: '🔧' },
     { id: 'verify', text: '正在验证修复效果…', icon: '✅' }
   ];
   ```

2. **UI 展示**：复用 `.ai-thinking-chain` 组件。

#### 4c 任务完成流程列表（新增）

**What**: 修复完成后展示修复总结卡片。

**How**:
1. **UI 组件**：
   - 修复完成后展示「修复总结」卡片
   - 顶部：修复步骤数 + 成功率环形图
   - 中部：每个修复项的状态（成功/失败/无需修复）
   - 底部：「应用修复」按钮

2. **失败诊断改造**：
   - 结果使用 `renderAnimatedLog`，但日志内容去技术化
   - 不再输出原始 JSON（除非用户主动展开「查看技术详情」）

---

### 改动 5：全局高级感样式增强

**文件**: `static/css/ai_hub.css`

**What**: 为三个 AI 页面统一增加高级感视觉元素。

**How**:
1. **毛玻璃面板增强**：
   ```css
   .ai-glass-panel {
     background: rgba(255,255,255,0.72);
     backdrop-filter: blur(20px) saturate(1.2);
     -webkit-backdrop-filter: blur(20px) saturate(1.2);
     border: 1px solid rgba(255,255,255,0.5);
     border-radius: 20px;
     box-shadow: 0 8px 32px rgba(79,70,229,0.08), inset 0 1px 0 rgba(255,255,255,0.6);
   }
   ```

2. **渐变边框按钮**：
   ```css
   .ai-glow-btn {
     position: relative;
     background: linear-gradient(135deg, #4f46e5, #7c3aed);
     color: #fff;
     border: none;
     border-radius: 12px;
     padding: 12px 24px;
     font-weight: 700;
     box-shadow: 0 4px 20px rgba(79,70,229,0.35), 0 0 0 1px rgba(255,255,255,0.1) inset;
     transition: transform 0.2s, box-shadow 0.2s;
   }
   .ai-glow-btn:hover {
     transform: translateY(-2px);
     box-shadow: 0 8px 30px rgba(79,70,229,0.45), 0 0 20px rgba(124,58,237,0.2);
   }
   .ai-glow-btn:active { transform: translateY(0); }
   ```

3. **脉冲光晕动画**：
   ```css
   @keyframes aiGlowPulse {
     0%,100% { box-shadow: 0 0 0 0 rgba(79,70,229,0.3); }
     50% { box-shadow: 0 0 0 12px rgba(79,70,229,0); }
   }
   .ai-glow-pulse { animation: aiGlowPulse 2s ease-in-out infinite; }
   ```

4. **悬浮粒子背景（可选，纯 CSS）**：
   ```css
   .ai-particle-bg {
     position: fixed; inset: 0; pointer-events: none; z-index: 0; overflow: hidden;
   }
   .ai-particle-bg::before, .ai-particle-bg::after {
     content: ''; position: absolute; border-radius: 50%;
     filter: blur(80px); opacity: 0.35;
   }
   .ai-particle-bg::before {
     width: 400px; height: 400px; background: #a78bfa;
     top: -100px; right: -100px; animation: aiFloat1 20s ease-in-out infinite;
   }
   .ai-particle-bg::after {
     width: 300px; height: 300px; background: #60a5fa;
     bottom: -80px; left: -80px; animation: aiFloat2 25s ease-in-out infinite;
   }
   @keyframes aiFloat1 { 0%,100% { transform: translate(0,0); } 50% { transform: translate(-30px,40px); } }
   @keyframes aiFloat2 { 0%,100% { transform: translate(0,0); } 50% { transform: translate(40px,-30px); } }
   ```

5. **深色模式适配**：所有新增组件添加 `html.dark` 规则。

---

## 验证步骤

1. **FOUC 验证**：
   - 禁用缓存，多次切换页面，确认无白色闪烁
   - Performance 面板确认 First Paint 时 body 已有正确背景色

2. **高级感视觉验证**：
   - 确认毛玻璃面板有 backdrop-filter blur 效果
   - 确认主按钮有渐变 + hover 光晕
   - 确认 AI 工作中脉冲动画正常
   - 确认深色模式下所有效果正确切换

3. **思考流程验证**：
   - AI Test：输入指令后，右侧出现思维链面板，步骤依次高亮
   - AI Design：点击生成后，输入框下方出现思维链面板
   - AI Heal：点击修复后，出现思维链面板
   - 所有思维链：进行中的步骤有脉冲动画，已完成的步骤有绿色对勾

4. **任务完成流程列表验证**：
   - AI Test：任务完成后出现「任务总结」卡片，含环形图、步骤列表、状态图标
   - AI Design：生成完成后出现「生成总结」卡片，含草案列表
   - AI Heal：修复完成后出现「修复总结」卡片
   - 所有总结卡片：有入场动画（scale + fade + slide），hover 有反馈

5. **页面简化验证**：
   - AI Test：仅显示顶栏 + 浏览器 + 底部输入框
   - AI Design：显示 AI 气泡 + 示例芯片 + 输入框 + 生成按钮
   - AI Heal：仅显示 2 个 Tab（定位器修复 / 失败诊断）

---

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `templates/base.html` | 修改 | 去掉 Tailwind defer |
| `templates/ai_test.html` | 大幅修改 | 简化布局，隐藏冗余控件，新增思维链面板 + 任务总结卡片 |
| `templates/ai_design.html` | 大幅修改 | 改为对话式交互布局，新增思维链 + 生成总结 |
| `templates/ai_heal.html` | 大幅修改 | 精简为 2 个 Tab，去代码化，新增思维链 + 修复总结 |
| `static/js/ai_assistant.js` | 修改 | 适配简化布局，新增思维链推进逻辑 + 任务总结渲染 |
| `static/js/ai_design.js` | 修改 | 添加示例芯片、思维链、生成总结卡片渲染 |
| `static/js/ai_heal.js` | 修改 | 改造结果渲染为自然语言，新增思维链 + 修复总结 |
| `static/css/ai_hub.css` | 大幅修改 | 新增毛玻璃、光晕按钮、脉冲动画、思维链、任务总结等全部高级感样式 |
