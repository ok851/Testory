# 将 Demo 的交互体验迁移到 Testory 产品中

## 核心思路

Demo 的表现形式（动画统计、逐字打字、时间线可视化、代码高亮实时变化）比当前产品 UI 更有冲击力和沉浸感。将这些 UX 模式迁移到产品的 AI 页面中，可以显著提升用户对 AI 能力的感知。

## 当前产品 vs Demo 的 UX 差距

| 体验维度 | 当前产品 | Demo | 差距 |
|---------|---------|------|------|
| AI Hub 首页 | 静态卡片 + 文字描述 | 动画数字统计 + 渐变背景 | **产品缺乏数据冲击力** |
| AI 生成用例 | 结果以纯文本 JSON 显示在 textarea | 逐字打字动画 + 语法高亮代码块 | **产品缺乏科技感** |
| AI 自主测试 | 聊天界面 + 嵌入式浏览器 | 时间线可视化 + AI 思考气泡 + 元素高亮 | **产品缺乏过程可视化** |
| AI 自愈 | JSON 预览 + 纯文本诊断 | 代码对比（删除线→新代码）+ 逐行日志动画 | **产品缺乏修复过程展示** |

## 具体改动方案

### 改动 1：AI Hub 页面增加动画统计

**文件**: `templates/ai_hub.html`, `static/js/ai_hub.js`

**当前**: 三个静态卡片（用例设计、自主测试、自愈优化），无数据展示。

**改为**: 在卡片上方增加一行动画统计数字，仿照 Demo 的 Hero Stats：
```
📊 已生成 12,847 个用例 · ⚡ 效率提升 80% · 🎯 覆盖率 95%
```
- 数字从 0 递增到目标值（缓动动画，2 秒）
- 数据从后端 API 读取（`/api/ai/stats`），无数据时显示默认值
- 使用与 Demo 相同的 `animateCounter()` 逻辑

**改动范围**: 
- `static/js/ai_hub.js`：增加统计动画逻辑
- `templates/ai_hub.html`：增加统计行 HTML
- `app.py`：新增 `/api/ai/stats` 路由（可选，也可硬编码默认值）

### 改动 2：AI Design 页面生成结果增加打字动画 + 代码高亮

**文件**: `templates/ai_design.html`, `static/js/ai_design.js`

**当前**: 生成的用例以 checkbox 卡片列表展示，每个卡片显示纯文本名称和描述。

**改为**: 
- 生成过程中，结果区域使用**逐行打字动画**展示（仿照 Demo 的 AI Chat）
- 生成的测试步骤使用**语法高亮**（关键词紫色、字符串绿色、注释灰色）
- 增加一个 `typing-cursor` 光标动画

**改动范围**:
- `static/js/ai_design.js`：修改 `renderDrafts()` 函数，增加打字动画
- `templates/ai_design.html`：增加代码高亮 CSS

### 改动 3：AI Test 页面增加 AI 思考过程可视化

**文件**: `templates/ai_test.html`

**当前**: 左侧聊天区域显示用户消息和 AI 结果，但 AI 的"思考过程"不可见。

**改为**: 在聊天区域增加 AI 思考气泡（仿照 Demo 的 `auto-thought`）：
- 当 AI 正在工作时，显示 `💭 正在分析页面结构...`、`💭 发现 15 个可交互元素...`、`💭 生成测试计划...` 等思考步骤
- 思考气泡使用紫色半透明背景 + 紫色边框
- 每个步骤带有淡入动画

**改动范围**:
- `templates/ai_test.html`：增加思考气泡 CSS + HTML 模板
- `static/js/ai_assistant.js`：在 AI 任务执行期间注入思考步骤

### 改动 4：AI Heal 页面增加代码对比可视化

**文件**: `templates/ai_heal.html`, `static/js/ai_heal.js`

**当前**: 定位器预览以 JSON 文本展示，诊断结果也是纯文本。

**改为**:
- 定位器预览使用**代码对比视图**（仿照 Demo 的 `heal-line`）：
  - 旧选择器：红色删除线 + 红色左边框
  - 新选择器：绿色背景 + 绿色左边框
  - 未变更行：青色高亮
- 诊断过程使用**逐行日志动画**（仿照 Demo 的 `heal-log-line`）：
  - `[INFO]` 青色
  - `[ERROR]` 红色
  - `[AI]` 紫色
  - `[PASS]` 绿色
  - 每行带有滑入动画（`translateX(-10px) → 0`）

**改动范围**:
- `static/js/ai_heal.js`：修改预览渲染函数，增加代码对比 + 日志动画
- `templates/ai_heal.html`：增加对比视图 CSS
- `static/css/ai_hub.css`：增加 `.heal-line` 系列样式

### 改动 5：全局增加 Demo 的 CSS 动画库

**文件**: `static/css/testory-brand.css` 或新建 `static/css/testory-animations.css`

从 Demo 中提取可复用的 CSS 动画：
- `fadeIn`：淡入上移动画
- `typing-cursor`：打字光标闪烁
- `heroGlow`：背景光晕动画
- `tapPulse`：点击脉冲动画
- 代码高亮色系（`.kw` 紫色、`.str` 绿色、`.comment` 灰色、`.fn` 黄色）

---

## 实施优先级

| 优先级 | 改动 | 工作量 | 效果 |
|-------|------|--------|------|
| P0 | 改动 1：AI Hub 动画统计 | 0.5 天 | 首页第一印象大幅提升 |
| P0 | 改动 4：AI Heal 代码对比 | 0.5 天 | 自愈过程更直观可信 |
| P1 | 改动 2：AI Design 打字动画 | 0.5 天 | 生成过程更有科技感 |
| P1 | 改动 3：AI Test 思考气泡 | 1 天 | 自主测试过程更透明 |
| P2 | 改动 5：动画 CSS 库 | 0.5 天 | 全局统一动画语言 |

**总工作量: 3 天**

---

## 技术约束

1. **不影响现有功能**: 所有改动都是纯前端视觉增强，不修改后端 API
2. **渐进增强**: 如果 JS 未加载或动画失败，页面仍能正常工作
3. **性能**: 动画使用 CSS `transition` 和 `requestAnimationFrame`，不使用 `setInterval`
4. **兼容性**: 所有动画在 WebView2 (Chromium) 中正常工作
5. **可选后端统计**: 改动 1 的统计数据可先硬编码，后续再接 API

## 验证方式

1. 打开 AI Hub 页面 → 统计数字从 0 递增
2. 打开 AI Design → 生成用例 → 结果逐行出现 + 语法高亮
3. 打开 AI Test → 点击生成 → 聊天区域出现思考气泡
4. 打开 AI Heal → 预览定位器 → 红绿代码对比视图
5. 所有页面在桌面客户端（WebView2）中正常运行
