# 对话、预览与智能测试：产品交互与能力路线图

本文档将「流式产品设想」与 **当前可落地的后端能力** 对齐，便于分阶段实现。实现重心：**对话与用例预览无缝一体**、**全平台统一上下文**、**嵌入式浏览器沉浸式操作**。

---

## 已实现或可立即使用的后端

| 能力 | 位置 | 说明 |
|------|------|------|
| 多轮改步骤 | `POST /api/ai/task/chat` | `message` + `current_plan` + `history`；见下表 **交互情境** |
| 页面结构进提示 | 同上 + `embedded_session_id` / 本地快照 | 与 `ai_page_probe`、DOM pack、memory 一致 |
| 结构化 UI 情境 | `interaction_context`（见下） | 高亮步骤、划词、意图类型注入 refine 提示 |

**`/api/ai/task/chat` 扩展字段（可选，向后兼容）**

- `focus_step_index`：单步聚焦（1-based，与 `current_plan.steps` 顺序一致）。
- `focus_step_indices`：多步（合并、批量修改）。
- `browser_selection_text` 或 `selection_text`：划词内容 → 用于生成 **verify** 或定位语义。
- `action_kind` 或 `intent`：如 `optimize_step`、`merge_steps`、`assert_from_selection`（供模型在 `_format_interaction_context` 中理解）。

**前端对接要点：** 左栏/浮球/右键只负责把上述字段与 `message` 一并 POST；**同一 `current_plan` + `history` 即跨端一致**。

---

## I. 对话与用例预览一体化

### 1. 任务与推理从「独立左栏」→「随处唤起的气泡 / 侧栏」

- **产品：** 主界面任意时刻打开同一对话态；状态含 `current_plan`、游标、聚焦步骤（见 API 字段）。
- **实现：** 将现有 `ai_test.html` 中规划/聊天逻辑抽成可复用模块（如 `static/js/ai_assistant.js`），多页嵌入同一组件；样式用固定底角浮球 + 可拖拽面板。
- **状态：** 存 `sessionStorage` 或短链 `?ai_session=…` 以便刷新不丢；与登录态绑定。

### 2. 右键「优化本步」预填

- **产品：** 在步骤行右键 → 打开聊天并预填/自带 `message` + `focus_step_index` + `action_kind=optimize_step` + 可编辑模板（如「为第 2 步增加重试」）。
- **实现：** 仅前端；调用已有 chat API。

### 3. 自然语言合并步骤

- **产品：** 「把这两步合并成一步」→ 列表刷新、无需理解底层。
- **实现：** `message` 描述 + `focus_step_indices` + `action_kind=merge_steps`；后端已注入合并提示（见 `ai_local_inference._format_interaction_context`）。

### 4. 划词 → 断言（全平台同上下文）

- **产品：** 浏览器划词后发言「断言这段内容可见」。
- **实现：** `browser_selection_text` 必填子串 + `action_kind=assert_from_selection`；若存在 LIVE snapshot，模型应绑定 `probe_index` 或 `verify` 的 `input_value`。

---

## II. 嵌入式浏览器增强

| 需求 | 现状 / 方向 |
|------|-------------|
| 录制 → 自然语言 + 步骤 | 需 **Recorder**：监听 Playwright 事件，映射为 `steps` JSON 草稿；可复用 `playwright` trace 或 CDP 包装。 |
| 悬停高亮推荐 selector | 已有元素探测能力可扩展为 **hover 时** `element_info` + 候选列表；前端 overlay 展示。 |
| 单步执行 + 高亮 + 右侧统计 | 执行引擎已有分步；需 **单步 API** 与 **前端** 高亮 `locator`、耗时、截屏、网络 summary。 |

---

## III. 拖拽、分组与流图

- **步骤排序/折叠/分组：** 纯前端（Sortable / dnd）+ 保存时 PUT `test_steps` 顺序与 `group` 元数据（可存 `description` 前缀或新列 `step_group`）。
- **流图 if/else：** 需 **步骤模型扩展**（分支 ID、条件）；生成侧需 JSON Schema 升级；与现有线性 `steps` 数组为较大改动，建议单独立项。

---

## IV. 反馈不打扰

| 能力 | 方向 |
|------|------|
| 底角一句状态 | 客户端根据 `current_plan` 与最后响应 `meta` 拼一句；不强制新 API。 |
| 保存时 selector 风险 | 用 **probe 校验** + 历史失败记忆（`ai_memory_store` / 运行结果）在保存前打标签。 |
| 页面变化静默提示 | 轮询或 Mutation + 与上次 `probe` hash 对比 → Toast「可能有弹窗」+ 快捷「加一步关闭」 |

---

## V. 禅模式（Control the situation）

- **产品：** 一键隐藏左右栏，仅留页面 + 浮层输入（语音/快捷键可二期）。
- **实现：** 纯 CSS/布局开关 + 本地 preference。

---

## VI. 逻辑严谨用例（生成侧，与后端的衔接）

1. **链式思考 + 结构化 JSON**  
   在 `generate_case_and_steps` 的 system/prompt 中增加可选 **scenario / teardown / 断言块**（需产品定 Schema），与现有 `case_url` / `steps` 并存，分阶段上。

2. **实时页语义**  
   已具备 snapshot + DOM pack + 可选 vision；续接：生成前强制拉一次 `get_interactive_page_snapshot`（与现有一致）。

3. **记忆与自愈闭环**  
   已有 `ai_memory_store`、selector 恢复与 vision 兜底；**成功修复写入 memory** 可在运行完成后异步 ingest（需挂钩）。

4. **对抗 / 负向**  
   prompt 中增加 **negative / boundary** 段落（可开关 `LOCAL_AI_ADVERSARIAL=1`）。

5. **业务知识图谱**  
   以 **文档块或 JSON 域规则** 注入 `memory_context` 或独立 `domain_pack` 文件（如 `ai_domain_packs/ecommerce.json`）后期接入。

6. **人机共审 diff**  
   返回 `plan` 时附带 `meta.diff_against_previous` 或在第二次 refine 时由前端做 JSON diff 展示；后端可提供 `previous_plan` 的 hash 比对接口（可选）。

---

## 建议落地顺序

1. **聊天气泡 + `interaction_context` 全站打通**（后端已支持；补 `ai_test` 与用例编辑页）。  
2. **右键/划词/合并** 的 UI 与固定模板文案。  
3. **单步执行 + 高亮 + 侧栏** 执行视图。  
4. **录屏/录制 → 步骤草稿**。  
5. 分支流图、领域包、对抗生成（依赖 Schema 与算力策略）。

### 本迭代完成标准（对 **1、2** 项）

- **1** 视为 **已落地**：`static/js/ai_assistant.js` 为共享入口；`ai_test` 与**用例步骤页**均能与 `/api/ai/task/chat` 对齐 `interaction_context`；`sessionStorage` 会话恢复、**本机 `localStorage` 草稿**含 **`chatHistory`（含 `warningsList`）**，与**撤销/重做**栈一致。  
- **2** 视为 **已落地**：步骤行 **右键 / Shift+右键**、模板与划词、追加步骤 **warnings 反馈**、**清空对话**、双端 **禅模式** 与底栏快速输入。  
- **3–5** 仍为**后续独立迭代**（录制器、流图/领域包等；**单步试跑**见下）。

### 单步执行（§II 方向，MVP）

- **API**：`POST /api/cases/<case_id>/run-one-step`，body：`{ "step_id", "navigate_first"? }` 或 `step_order`；与整例运行互斥（整例进行中返回 409）；`playwright_automation.sync_execute_single_db_step` 复用 `execute_single_step`。  
- **UI**：用例步骤列表每行 **「单步」**、可选 **单步前打开用例 URL**、结果 JSON 与当前行 **高亮**。  
- **限制**：`assert` / `enter_iframe` / `date` 等未在 `execute_single_step` 覆盖的类型返回 400；**单步不计入**每日整例执行次数（便于调试，可后续改为与整例一致）。

### 实现进展（与上表对应）

- **1（已完成，本标准）** — 见上节。技术要点：`appendInteractionToPayload`、`formatPlanStatusLine`、步骤页 `mountStepsPageAssistant`；`ai_test`：`aiIx*` 情境、**恢复会话**、`#aiStatOneLiner`、**Alt+Z** 与禅 `localStorage`、归一化 **`<details>`**、**`getDraftState` / `aiSaveDraftLocal` 含完整对话**。**IV 底角状态**：`formatPlanStatusLine` + 统计区。  
- **2（已完成，本标准）** — 步骤行 **右键** / **Shift+右键**、**Toast/控制台** `warnings`、面板 **hydrate** 与 **清空对话**、历史条数限制。  
- **3（单步 MVP）** — `POST .../run-one-step` + 步骤页「单步」与高亮；完整「高亮 DOM + 网络 summary 侧栏」仍属增强项。  
- **4–5** — 未实现（录制草案、流图等），见上。

本文件为活文档，随迭代更新；与 [PRODUCT_TIERS](PRODUCT_TIERS.md) 中的档位能力打包策略配合使用。
