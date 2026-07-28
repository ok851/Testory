# 跨端联动缺陷债（Phase 0b）

> 版本：2026-07-24  
> 前置：必须先满足 [EXECUTION_RELIABILITY_STANDARD.md](./EXECUTION_RELIABILITY_STANDARD.md) 的单通道 S1–S4 原则；跨端不得另搞一套「假绿标准」。  
> 上级：[PRODUCT_NORTH_STAR.md](./PRODUCT_NORTH_STAR.md)

跨端的产品承诺是：**同一业务流、共享变量与证据、可暂停 HITL、结果诚实**。下列条目在关闭前，不得宣传「企业级多端联动已就绪」。

---

## 1. 完成定义（Phase 0b Done）

| # | 门禁 | 验收 |
|---|------|------|
| L1 | API→Web（或 Web→API）至少 **1 个变量真实透传** | 上游写入，下游请求/步骤使用非空解析值；报告可见 |
| L2 | 无浏览器 / 无设备时跨端阶段 **失败** | 非 warning 后 success |
| L3 | Mobile/Desktop 步骤软失败 → 阶段失败 | 校验返回值或 `validate_*_step_result` |
| L4 | HITL 可阻塞并超时失败 | `wait_for_human` 非恒 True；超时非 success |
| L5 | 跨端执行走与用例相同的本机锁 | 冲突可预期 |
| L6 | 场景保存→列表→加载→再执行 | ID/字段一致，可运营 |
| L7 | 跨端总结 success 仅当所有必选阶段通过且无未处理失败 | `RECOVERY_SKIP` 须显式策略并在报告披露 |

**状态（2026-07-27）：L1–L7 已满足**（对应 X1–X8、Y1–Y6、Z1–Z3 关闭）。Y5：**已关闭（Done=有限自愈）**——Hub 矩阵 + Desktop 运行时自愈（标题/别名/有限 UIA）；**禁止宣传「通用已自愈」**。

---

## 2. 缺陷清单

### P0 — 阻断联动 / 直接假绿假传

| ID | 缺陷 | 位置（参考） | 修复方向 | 须覆盖的边缘情况 |
|----|------|--------------|----------|------------------|
| X1 | ~~UI 阶段 `extracted` 恒 `{}`，`vars_to_store` 不落地~~ **已关闭（0b-X1）** | `var_extraction` + `_execute_ui_stage` / API extract | Web DOM/`store_as`/API `extract`/`vars_to_store` 写入 context；必选缺失失败；敏感字段脱敏；`$.path` 兼容 | 声明了 var 但选择器失败；部分成功；加密字段脱敏 |
| X2 | ~~无 `page` 仅 warning，`ok_assert` 仍 True~~ **已关闭（0a-3-1）** | 同上 | 无 page ⇒ 阶段失败 | CDP 中途断开；多 tab；锁导致未启动 |
| X3 | ~~Mobile/Desktop 不检查返回值~~ **已关闭（0a-3-1）** | 同上 | `validate_mobile_step_result` / `validate_desktop_step_result`；desktop `warning` 跨端不得绿 | 软 error 字典；部分步骤成功；超时 |
| X4 | ~~HITL `wait_for_human` 恒 `True`~~ **已关闭（0a-3-2）** | `sync_manager` + `agent_hitl` gates | `open/wait/resume/cancel_hitl_gate`；编排 `layer=hitl` / `hitl` 预门禁；超时非 success | 超时；重复 resume；跨端与单用例 HITL 会话隔离 |
| X5 | ~~场景 UUID 存储 vs API `<int:scenario_id>` / UI 字段不一致~~ **已关闭（0b-X5）** | store + `app.py` + `cross_end.html` | 字符串 `scenario_id`；公开字段归一；`UAT_DATA_DIR`；写锁；前端 `JSON.stringify` 加载 | 更新覆盖；删除；并发写 JSON |
| X6 | ~~跨端 execute 不获取 `execution_lock`~~ **已关闭（0a-3-3）** | `orchestrator.execute_cross_end_plan` | `execution_guard`；忙→`lock=busy`；ImportError→`unavailable` 拒绝执行 | 与单用例并行；ImportError 不得静默绕过 |
| X7 | ~~`hermes_execute_stage` 未接入编排主路径~~ **已关闭（0b-X7）** | 显式 `executor`/`use_hermes`/`default_ui_executor`；不可用 → `HERMES_UNAVAILABLE`（不静默回退）；默认仍 classic |
| X8 | ~~Hermes 默认 `ok_assert=True`~~ **已关闭（0a-2 / emit guard）** | `_parse_hermes_result` 默认 False；仅 `[RESULT] ok` / 显式 JSON ok 才 True | 散文成功；部分 JSON；多语言标记 |

### P1 — 削弱可靠性

| ID | 缺陷 | 修复方向 |
|----|------|----------|
| Y1 | ~~`wait_for_data_sync` / UI/API sync 辅助未调用~~ **已关闭（0b-Y1）** | `vars_to_read`/`wait_for`/`data_sync`/`api_state_sync`/`state_sync`/`time_sync` 预门禁；超时失败；`depends_on` 拒绝 skipped 依赖 |
| Y2 | ~~`RECOVERY_SKIP` 可跳过失败且不影响 success~~ **已关闭（0b-Y2）** | Skip 写入 `skipped_failure_stages` / recovery_log；默认挡总成功；`allow_skipped_failures` 显式放行 |
| Y3 | ~~跨端 Web runner 无自愈、空选择器软跳过~~ **已关闭（0b-Y3）** | 空 selector 一律失败（`EMPTY_SELECTOR`，ignore allow_skip）；全跳过需阶段 `allow_skip`；`user_hint` + 前端可读提示 |
| Y4 | ~~场景 JSON `_store_path` 可能落到仓库外目录~~ **已关闭（0b-Y4）** | 修正 `parents[2]`；`scenario_store_info` + 前端备份提示 |
| Y5 | ~~Self-heal Hub + Desktop 有限运行时自愈~~ **已关闭（2026-07-27）** | Hub 矩阵；attach/launch/有限 UIA；**禁宣传通用已自愈**；失败不假绿 |
| Y6 | ~~跨端断言主要吃 API extract，UI 无源~~ **已关闭（0b-Y6）** | 变量/`{{var}}`/UI selector 解析；缺源失败；断言失败挡总成功 |

### P2 — 工程与体验

| ID | 说明 |
|----|------|
| Z1 | ~~跨端运行写入统一 `run_history`/`test_type=cross_end`~~ **已关闭（B-1）**：`cross_end_run_audit`；agent_teams 单记；orphan 保护；步骤级证据 |
| Z2 | ~~Trace 导出（与 Phase B 可观测对齐）~~ **已关闭（B-2）**：`trace_pack` JSON Trace + ZIP；`/api/ai/trace-packs/export`；跨端页「导出证据包」 |
| Z3 | ~~Mock 与真机同一 Schema~~ **已关闭**：`result_schema`（`testory.stage_result/v1` / `cross_end_result/v1`）；orchestrator + Demo simulate 归一化；`tests/test_cross_end_result_schema.py` |

---

## 3. 推荐修复顺序（质量优先，非图快）

1. **契约：** 跨端阶段结果结构 = `{ ok_assert, error_code, error_message, extracted, warnings, evidence[] }`，与 S1–S4 对齐。  
2. **X2/X3/X8：** 先消灭假绿（无环境、软失败、Hermes 默认成功）。  
3. **X1：** 变量抽取（否则「联动」无意义）。  
4. **X4/X6：** HITL + 锁（流水线可预期）。  
5. **X5：** 持久化（可运营）。  
6. **X7/Y1/Y2：** 接线与恢复策略诚实化。  

每关 Critical 附：单元测试（纯函数）+ 至少一条手工/集成验收记录。

---

## 4. 非目标（本阶段不做）

- 不在此阶段引入完整多 Agent / AgentTeams（属 Phase A）。  
- 不把跨端主路径改成「只靠视觉模型散文判定通过」。  
- 不承诺 iOS 跨端。  

---

## 5. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-24 | 首版：P0/P1/P2 与 Done 门禁 |
| 2026-07-24 | **0a-3-1**：`_execute_ui_stage` 默认失败；无 page 失败；mobile/desktop 校验返回值；`web_runner` 空 URL/selector 不得软跳过；`CrossEndContext` 缺省/`all_passed` 收紧。关闭 X2/X3。 |
| 2026-07-24 | **0a-3-2**：`agent_hitl` gate 阻塞等待；`wait_for_human` 仅 resume 为 True；编排 HITL 层/预门禁；`/hitl/resume` 支持 gate_id；`tests/test_hitl_gate.py`。关闭 X4。 |
| 2026-07-24 | **0a-3-3**：跨端 `execution_guard`；API 409/503；ImportError 不绕过；`tests/test_cross_end_execution_lock.py`。关闭 X6。 |
| 2026-07-24 | **0b-X1**：`var_extraction`；Web/API 变量落地与必选门禁；JSONPath `$.` 兼容；`tests/test_cross_end_var_extraction.py`。关闭 X1。 |
| 2026-07-24 | **0b-X5**：场景字符串 ID 路由；字段归一（`scenario_id`/`id`/`name`/`plan`）；写锁与 `UAT_DATA_DIR`；前端保存/加载对齐；`tests/test_cross_end_scenario_store.py`。关闭 X5。 |
| 2026-07-24 | **0b-Y2**：RECOVERY_SKIP 披露与默认挡成功；`allow_skipped_failures`；重试清旧错误；`tests/test_recovery_skip_gate.py`。关闭 Y2。 |
| 2026-07-24 | **0b-Y1**：阶段预同步门禁（`vars_to_read`/`wait_for`/data|api|state|time_sync）；轮询超时失败；`depends_on` 拒绝 skip 依赖；`tests/test_stage_sync_gate.py`。关闭 Y1。 |
| 2026-07-24 | **0b-Y3**：空 selector 不得软跳；全跳过默认失败；`user_facing_errors` + 跨端页阶段 ID/`ok_assert`/友好横幅；`tests/test_cross_end_ui_stage_gate.py` 扩展。关闭 Y3。 |
| 2026-07-24 | **0b-Y4**：场景路径修正为仓库 `data/`（`parents[2]`）；`scenario_store_info` 与前端备份提示。关闭 Y4。 |
| 2026-07-24 | **0b-X7**：Hermes 显式 opt-in 接入 UI 阶段；不可用诚实失败；默认 classic；`tests/test_cross_end_hermes_wiring.py`。关闭 X7。 |
| 2026-07-24 | **0b-Y6**：断言源解析（上下文变量 + UI selector）；禁止单源跳过比较；失败 `CROSS_END_ASSERT_FAILED`；前端展示；`tests/test_cross_end_assertions.py`。关闭 Y6。 |
| 2026-07-24 | **0a-2 / X8**：确认 Hermes `_parse_hermes_result` 默认失败；`tests/test_execution_emit_guard.py`。关闭 X8。 |
| 2026-07-24 | **Phase 0b Done**：L1–L7 门禁达成；P0 缺陷 X1–X8 与阻塞级 P1（Y1–Y4、Y6）已关闭。 |
| 2026-07-24 | **B-1 / Z1**：跨端与 AgentTeams 诚实写入 `run_history`（`test_type=cross_end|agent_teams`）+ 文件审计 + step_results；orphan 清理不误删；`tests/test_cross_end_run_audit.py`。 |
| 2026-07-24 | **B-2 / Z2 / R07**：`trace_pack` 证据包（manifest/trace/report/screenshots index + ZIP）；API 与跨端页导出；Demo 同步产出；`tests/test_trace_pack.py`。 |
| 2026-07-24 | **Y5（半）**：Self-heal Hub 能力矩阵 API/页；Desktop 仅静态扫描、明确无运行时自愈；禁营销完成态。 |
| 2026-07-24 | **Y5（续）**：Desktop 有限运行时自愈（attach 标题放宽 / launch 别名重解析）；orchestrator 接线；失败不假绿；`tests/test_desktop_runtime_heal.py`。 |
| 2026-07-27 | **Y5（UIA 有限）**：click/input 等有限 UIA 放宽（去 automation_id、name contains、清 parent_chain、缩短 path）；仍非通用自愈；`demos/golden` R17。 |
| 2026-07-27 | **Y5 关闭**：Done=Hub+有限 Desktop 自愈；`y5.closed=true`；禁营销「通用已自愈」。 |
| 2026-07-24 | **Z3**：跨端阶段/总结果 Schema 归一（Mock simulate ↔ live）；`ai_modules/execute/result_schema.py`；Demo/orchestrator 接线；`tests/test_cross_end_result_schema.py`。 |
| 2026-07-24 | **R08+R09+R10+R14**：`risk_guard` L2 编排门禁；guards Demo（真实 HITL/Risk + Desktop 生产闸门模拟）；根 `LICENSE`/`NOTICE`/`README.md`；`tests/test_risk_guard.py` + `test_goai_guards_demo.py`。 |
