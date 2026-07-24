# 用例执行可靠性标准（企业流水线级）

> 版本：2026-07-24  
> 目标：使「运行用例」达到 **稳定、可靠、真实不假传**，可供企业 CI/CD 门禁与审计。  
> 上级：[PRODUCT_NORTH_STAR.md](./PRODUCT_NORTH_STAR.md)  
> 配套：[CICD_INTEGRATION.md](./CICD_INTEGRATION.md) · [LINKAGE_DEFECT_BACKLOG.md](./LINKAGE_DEFECT_BACKLOG.md)

本文不是愿景口号，而是 **验收标准 + 已知违规清单 + 门禁用例设计**。未通过本标准，不得宣称企业级 / 流水线就绪。

---

## 1. 四条硬标准（S1–S4）

任一违背 = **未达生产级**。

| ID | 名称 | 定义（必须同时成立） |
|----|------|----------------------|
| **S1** | 不假绿 | 步骤/用例/批次/调度实际失败、未执行、被跳过且无显式「允许跳过」策略时，**不得**报告 `success`。环境不具备（无浏览器/无桌面会话/无设备/锁冲突）必须 **失败或明确阻断**，禁止「警告后当成功」。 |
| **S2** | 不假传 | 变量、截图、日志、断言结果、提取字段必须来自真实执行产物。禁止：空 extract 冒充透传、丢弃失败返回值仍继续绿、LLM 散文当断言通过。 |
| **S3** | 可复现可追溯 | 同输入 + 同环境类别，结果语义一致；失败带稳定错误码/原因；一次运行可在历史中回看；证据（步骤结果、截图、关键日志）可关联到 `run_history_id`。 |
| **S4** | 流水线可接入 | 本机执行互斥语义清晰（锁/忙）；对外可查询终态；可导出机器可读结果（至少 HTTP 状态语义清晰，目标含 JUnit）；CI 可将「非 success」一律判红。 |

### 1.1 状态词汇表（统一门禁映射）

平台历史中曾出现：`success` / `error` / `failed` / `warning` / `stopped` / `skipped`。

**企业门禁默认映射（CI / 调度「是否通过」）：**

| 内部状态 | 是否算通过 | 说明 |
|----------|------------|------|
| `success` | 是 | 唯一默认绿灯 |
| `error` / `failed` / `fail` | 否 | 硬失败 |
| `stopped` | 否 | 中断视为未完成交付 |
| `warning` | **否**（门禁） | 可展示为警告，但 **不得**让调度/CI 记成功 |
| `skipped` | **否**（门禁） | 除非用例显式标记 `allow_skip=true` 且策略批准 |

实现整改时：`evaluate_batch_case_status`、调度 `failed_cases==0`、CI 适配器必须服从上表。

---

## 2. 覆盖的执行通道（必须逐通道达标）

| 通道 | 入口（参考） | 结果落库 |
|------|--------------|----------|
| 单用例 Web/Desktop/混合 | `POST /api/cases/<id>/run` | `run_history` + `step_results` |
| 批量 | `POST /api/execute_multiple_cases` | 同上 + 批次汇总 |
| API 用例 | `POST /api/api-cases/<id>/run` | `run_history`（注意 dry-run 无历史） |
| 调度 | `POST /api/schedules/<id>/run` / APScheduler | `schedule_history` + 用例历史 |
| 数据驱动 | `POST /api/datasets/<id>/run` | 按行结果 |
| 移动（手机同步） | `POST /api/mobile/sync/run` | `run_history`（信任设备载荷时需校验） |
| 跨端计划 | `/api/ai/cross-end/execute` 等 | `run_history`（`test_type=cross_end`）+ `data/cross_end_runs/` 审计文件；多 Agent 为 `agent_teams` |
| AI/Hermes 任务 | `/api/ai/task/execute` | 多数不进 `run_history`；若对外承诺「测试通过」须同等 S1 |

**原则：** 任何对用户或 CI 声称「测试通过」的通道，都服从 S1–S4；不能「UI 用例严、AI 路径松」。

---

## 3. 历史 · 报告 · 日志规范（企业流程）

### 3.1 运行历史（必有字段）

一次运行至少可回答：

| 字段语义 | 要求 |
|----------|------|
| 谁触发 | 用户 / 调度 / CI（`trigger_source`，CI 时含 `build_id`/`git_sha`） |
| 跑了什么 | `case_id` / 批次列表 / 跨端 plan id |
| 环境摘要 | 浏览器类型、桌面会话、设备 id（缺则记「缺失」且不得绿灯） |
| 结论 | 仅用统一词汇表；与步骤聚合一致 |
| 耗时 | 起止时间、duration |
| 错误摘要 | 失败时非空 |
| 证据索引 | `run_history_id` → step_results / screenshots |

**禁止：** 历史显示 success，打开步骤却有未解释的 error；或失败运行无历史行（除明确 dry-run）。

### 3.2 测试报告

| 要求 | 说明 |
|------|------|
| 同源 | 报告结论 = 该次 `run_history.status`（门禁映射后），禁止报告层美化 |
| 步骤级 | 每步：动作、期望、实际、status、耗时、截图/日志引用 |
| 导出 | 保留 HTML/PDF/Excel；**新增** CI 用 JUnit/xUnit（见 CI 文档） |
| 失败可读 | 失败步骤错误信息人类可读 + 机器可解析错误码 |

### 3.3 日志

| 要求 | 说明 |
|------|------|
| 关联 | 关键执行日志行可检索 `run_history_id` 或稳定 `run_token` |
| 级别 | error 对应失败步骤；禁止失败只 info |
| 保留 | 单次运行可打包下载（至少失败包：日志片段 + 截图 + 步骤 JSON） |
| 脱敏 | 密码/token 不入明文报告（云/导出路径遵守现有脱敏策略） |

---

## 4. 场景矩阵（设计与测试须覆盖 ≥98% 常见分支）

下列每条都应有：**期望行为** + **验收方式**（人工或自动化）。整改按通道打勾。

### 4.1 环境不具备

| # | 场景 | 期望 |
|---|------|------|
| E1 | 无可用浏览器 / CDP 断开 | 失败或阻断，非 success |
| E2 | Windows 无交互桌面会话却跑桌面步骤 | 失败或阻断 |
| E3 | 无 adb 设备 / 设备离线 | 失败或阻断 |
| E4 | API 基址不可达 / TLS 失败 | 失败，错误可定位 |
| E5 | 执行锁被占用 | 409/忙；写拒绝历史或明确不建假 success |
| E6 | 锁模块 ImportError | **不得**静默绕过；应失败或硬依赖 |

### 4.2 步骤与断言

| # | 场景 | 期望 |
|---|------|------|
| A1 | 选择器不存在 / 超时 | 步骤 error，用例非 success |
| A2 | 断言期望为空（误配） | 失败或阻断发布（禁止空期望当通过） |
| A3 | 断言失败 | 用例非 success，报告含期望/实际 |
| A4 | 未知 action 类型 | 失败（禁止 fall-through 标 success） |
| A5 | navigate URL 为空 / 特殊跳过标记 | 默认非 success；仅显式 allow_skip 可跳过 |
| A6 | 桌面指针动作未 `verified`/`pointer_executed` | 失败（保持现有强校验） |
| A7 | 步骤返回 `status:error` 但未抛异常 | **必须**计失败（禁止只认异常） |
| A8 | 步骤返回 `warning` | 门禁层非通过；UI 可标警告 |

### 4.3 聚合与批次 / 调度

| # | 场景 | 期望 |
|---|------|------|
| B1 | 批次中 1 条失败 | 批次与调度非 success |
| B2 | 全部为 warning / skipped | 调度非 success（门禁） |
| B3 | 用户中途 stopped | 非 success |
| B4 | 计划步数未跑完 | 非 success |
| B5 | API 用例 0 步骤 | 非 success（或明确 invalid，禁止当通过） |

### 4.4 数据与安全

| # | 场景 | 期望 |
|---|------|------|
| D1 | 数据驱动某行失败 | 该行 failed；汇总诚实 |
| D2 | 密钥出现在步骤参数 | 报告/导出脱敏 |
| D3 | 并发两次抢同一桌面 | 第二份忙失败，不交错假绿 |

### 4.5 AI / Hermes / 跨端

| # | 场景 | 期望 |
|---|------|------|
| H1 | Hermes 未输出明确 RESULT/ok | **默认失败**（禁止默认 ok_assert=True） |
| H2 | 跨端无 page 仍跑 Web 阶段 | 阶段失败 |
| H3 | 跨端 mobile/desktop 软失败字典 | 阶段失败 |
| H4 | 跨端 HITL 需要人工 | 阻塞直至 resume/超时失败（禁止恒 True） |
| H5 | AI 任务宣称完成但未落断言 | 不得写入「用例已通过」类历史，或必须带未验证标记 |
| H6 | 变量约定写出但未抽取 | 下游不得静默用空值当成功透传 |

### 4.6 历史 / 报告 / CI

| # | 场景 | 期望 |
|---|------|------|
| R1 | 失败运行 | 历史 status 非 success，报告同源 |
| R2 | 成功运行 | 每步有 success 记录；关键步骤有证据或明确「无截图策略」 |
| R3 | CI 只读 HTTP `success` 字段（API dry-run） | 文档警告：必须以 `ok_assert`/门禁映射为准 |
| R4 | 导出 JUnit | failures/errors 与门禁一致 |
| R5 | 移动端同步缺 step status | 不得默认 success |

---

## 5. 已知违规清单（审计快照 2026-07）

整改时关闭条目并附 PR/测试证据。分级：Critical / High / Medium。

### Critical

| ID | 问题 | 证据位置（参考） |
|----|------|------------------|
| C1 | ~~Hermes 阶段默认 `ok_assert=True`~~ **已关闭（0a-2）**：默认 False，仅 `[RESULT] ok` / JSON `ok:true` 通过 | `hermes_stage_executor.py` |
| C2 | ~~跨端无 browser page 仅 warning，阶段仍绿~~ **已关闭（0a-3-1）** | `orchestrator.py` `_execute_ui_stage` |
| C3 | ~~跨端 mobile/desktop 不检查步骤返回值~~ **已关闭（0a-3-1）** | `orchestrator.py` |
| C4 | ~~批次 `skipped` 仍可聚合成 success~~ **已关闭**：聚合层（0a-1）+ 发射层空 navigate/`__SKIP_URL__` 默认 error，仅 `allow_skip` 可 skipped（0a-2） | `evaluate_batch_case_status` + navigate |
| C5 | ~~空断言期望可能直接通过~~ **已关闭（0a-2）**：`assert_empty_expected_error` 于 PA/单用例路径 | Playwright / `app.py` 断言分支 |

### High

| ID | 问题 | 证据位置（参考） |
|----|------|------------------|
| H1 | 单用例未知/不完整动作 fall-through 标 success | `app.py` `api_run_case` 尾部 |
| H2 | ~~跨端 web_runner 空选择器软跳过仍 ok~~ **已关闭（0a-3-1）** | `web_runner.py` |
| H3 | ~~API 空用例 → warning；调度不计入失败~~ **部分关闭**：空计划/`warning` 计入 `failed_cases`；调度用 `count_batch_gate_failures`；空 `case_ids` 调度直接 fail。API 仍可能发射 `warning`（展示） | API batch + schedule 汇总 |
| H4 | API dry-run `success` 与 `ok_assert` 不一致 | `app.py` dry-run |
| H5 | 移动同步缺省 step status=success；persist 吞异常 | `mobile_sync_store.py` |
| H6 | 执行锁 ImportError 时静默绕过 | `app.py` |
| H7 | `warning` 在桌面校验中当成功 | `step_executor.validate_desktop_step_result` |
| H8 | ~~跨端 HITL `wait_for_human` 恒 True~~ **已关闭（0a-3-2）** | `sync_manager.py` + `agent_hitl.py` |
| H9 | ~~跨端不走 execution_lock~~ **已关闭（0a-3-3）** | `orchestrator.py` + cross-end API |
| H10 | ~~Hermes stage executor 未接到编排主路径~~ **已关闭（0b-X7）** | 显式 opt-in；不可用不得静默回退 |

### Medium

| ID | 问题 | 说明 |
|----|------|------|
| M1 | 状态词 `error` vs `failed` 分裂 | CI 适配需统一映射 |
| M2 | AI task 成功不进 `run_history` | 与「测试通过」话术易混 |
| M3 | 日志未 FK 绑定 run_history_id | 削弱 S3 |
| M4 | 失败仍可能先扣配额 | 产品策略，非假绿但需披露 |
| M5 | ~~跨端场景 UUID vs int 路由/字段不一致~~ **已关闭（0b-X5）** | 运营可复现 |
| M6 | ~~RECOVERY_SKIP 可能跳过失败阶段且无 final_error~~ **已关闭（0b-Y2）** | 披露 `skipped_failure_stages`；默认非 success |

---

## 6. Phase 0a 工程门禁（可自动化优先）

下列门禁全部通过，才可标记「执行层 0a 完成」：

1. **故意失败 Web 用例**（错误选择器）→ `run_history.status` ∈ {error,failed}，报告非成功，CI 映射为红。  
2. **无浏览器环境**跑 Web 用例 → 非 success。  
3. **桌面步骤**返回未 verified 的失败字典 → 非 success（保持/加强现有校验）。  
4. **API 断言失败** → 非 success；dry-run 文档与字段不以误导性 `success` 对外门禁。  
5. **批次含 1 fail + 1 warning-only 套件** → 调度/批次非 success。  
6. **锁占用**第二次运行 → 忙失败，无交错成功历史。  
7. **历史/报告同源**：随机抽 10 次失败运行，历史结论与报告结论 100% 一致。  
8. 回归测试入库：至少 `tests/` 下锁定「假绿」相关纯函数（如 `evaluate_batch_case_status` 新契约）。

跨端专项门禁见 [LINKAGE_DEFECT_BACKLOG.md](./LINKAGE_DEFECT_BACKLOG.md)。  
CI 门禁见 [CICD_INTEGRATION.md](./CICD_INTEGRATION.md)。

---

## 7. 整改原则（保证质量，禁止片面修补）

1. **先定契约再改代码：** 状态词汇表与门禁映射先合并，再改聚合函数。  
2. **所有对外「通过」口径一致：** UI、API、调度、导出、CI。  
3. **默认失败，显式成功：** 尤其是 AI/Hermes 与「跳过」类路径。  
4. **软返回值与异常同等处理：** `status:error` 与 raise 都要失败。  
5. **修一处测一类：** 每关 Critical/High 附回归用例，避免只修演示路径。  
6. **不靠「提示用户看 warning」逃避门禁：** 企业流水线不会人工读 warning。  

---

## 8. 与单用例路径的既有优点（保留）

- 单用例循环将步骤初值设为 `error`、成功才改 `success`（`api_run_case`）——方向正确，需堵住 fall-through。  
- 桌面指针 `verified` + `pointer_executed` 校验——应推广为「有返回值必校验」的范本。  
- 失败截图写入 `run_history`——保留并扩展到跨端/AI 宣称通过的路径。

---

## 9. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-24 | 首版：S1–S4、场景矩阵、审计违规清单、0a 门禁 |
| 2026-07-24 | **Phase 0a-1**：`evaluate_batch_case_status` / `is_execution_gate_success` / `count_batch_gate_failures`；批次 `process_case_result` 与调度汇总对齐「仅 success 过门」；`tests/test_execution_gate_status.py`（17）。C4/H3 聚合层关闭，发射层待 0a-2。 |
| 2026-07-24 | **Phase 0a-2**：空 navigate/`__SKIP_URL__` 默认失败（`allow_skip` 除外）；空 assert 期望失败；Hermes 默认 `ok_assert=False`；`tests/test_execution_emit_guard.py`。关闭 C1/C4/C5。 |
| 2026-07-24 | **Phase 0a-3-1**：跨端 `_execute_ui_stage` / `web_runner` / `CrossEndContext.all_passed` 防假绿；`tests/test_cross_end_ui_stage_gate.py`。关闭 C2/C3/H2、联动 X2/X3。 |
| 2026-07-24 | **Phase 0a-3-2**：HITL gate 真实阻塞/超时；编排接入；关闭 H8 / X4。 |
| 2026-07-24 | **Phase 0a-3-3**：跨端 execution_lock；关闭 H9 / X6。 |
