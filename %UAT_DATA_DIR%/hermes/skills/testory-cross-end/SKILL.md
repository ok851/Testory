---
name: testory-cross-end
description: Testory 跨端联动编排引擎：将自然语言描述的端到端测试场景分解为 API → Web → Mobile → Desktop 多阶段执行计划，支持变量透传、同步点管理、断言与恢复策略。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: cross-platform
    risk_default: L1
    tags: [orchestration, e2e, api, web, mobile, desktop, sync-points, variable-passing]
---

# Testory 跨端联动编排

## 输入 / 输出 Schema

### 计划输入（CrossEndPlan）

| 字段 | 说明 |
|------|------|
| `stages[]` | 每项含 `id` / `layer` / `depends_on` / 端侧配置 |
| `cross_end_assertions` | 计划级断言 |
| `approvals` | 可选 `stage_id → approval_token`（L2） |
| `allow_skipped_failures` | 显式才允许 skip 当绿 |

### 执行输出

| 字段 | 说明 |
|------|------|
| `success` / `gate_passed` | 门禁与断言均通过才为 true |
| `stage_results[]` | 含 `ok_assert` / `error_code` / `hitl_*` / `risk_*` |
| `variables` | 跨端 vars |
| 审计 / Trace | `run_history` + `trace_pack` |

## 失败处理（诚实）

| 情况 | `error_code` 示例 |
|------|-------------------|
| 依赖未满足 | `DEPENDS_ON_UNSATISFIED` |
| 同步超时 | `SYNC_*_TIMEOUT` |
| HITL 超时/取消 | `HITL_TIMEOUT` / `HITL_CANCELLED` |
| L2 无令牌 | `RISK_APPROVAL_REQUIRED` |
| 断言失败 | `CROSS_END_ASSERT_FAILED` |
| RECOVERY_SKIP 默认 | `RECOVERY_SKIP_BLOCKS_SUCCESS`（挡总成功） |

禁止：无 page/设备仍 success；软失败当绿；Skip 默认当绿。

## 安全边界

- 编排本身默认 **L1**；阶段可声明 `risk_level`。  
- L2 阶段必须带审批令牌（见 `testory-risk-guard`）。  
- HITL 与 RiskGuard 可共存：先人机、再策略，或按计划顺序。  
- 场景 JSON 落在 `UAT_DATA_DIR` 或仓库 `data/`，勿泄露密钥到 Git。

## 核心铁律

1. 跨端计划必须包含明确的 `stages` 列表，每个 stage 指定 `layer` (api/web/mobile/desktop) 和 `stage_id`。
2. 变量透传使用 `{{stage-N.var_name}}` 语法，通过 `vars_to_read` 和 `vars_to_store` 声明依赖。
3. 所有跨端场景必须注册清理阶段 (`cleanup: true`)，确保测试数据自动清理。
4. API stage 使用嵌套格式 `request: {method, url, headers, body}` 和 `assert: {status}`。

## 使用入口

访问 `/cross-end` 页面，输入自然语言跨端场景，点击 "AI 分解"。

或通过 API：

```bash
# 场景分解
curl -X POST /api/ai/cross-end/decompose \
  -H "Content-Type: application/json" \
  -d '{"description": "先通过API创建用户，再在浏览器中验证登录"}'

# 执行计划
curl -X POST /api/ai/cross-end/execute \
  -H "Content-Type: application/json" \
  -d '{"plan": {...}}'
```

## 变量系统

| 语法 | 说明 |
|------|------|
| `{{stage-1.token}}` | 读取 stage-1 输出的 body.token 字段 |
| `{{stage-1.body.user.id}}` | 嵌套路径读取 |
| `vars_to_store: {token: "$.body.token"}` | JSONPath 提取并存储 |
| `vars_to_read: ["stage-1.token"]` | 声明依赖，确保执行顺序 |

## 同步点类型

| 类型 | 说明 | 超时 |
|------|------|------|
| `data_sync` / `vars_to_read` | 等待上下文变量就绪（轮询） | 默认 30s；缺失/空串失败 |
| `state_sync` | 等待变量条件或浏览器 selector | 默认 60s；无 page 不得绿 |
| `api_state_sync` | 轮询 HTTP + json_path 直至目标状态 | 默认 30s |
| `time_sync` | 固定等待 | 配置 seconds/ms |
| `human_sync` / HITL | 人工确认（编排 `hitl` 层） | 可配置；超时非 success |

阶段执行前由 `SyncPointManager.run_pre_stage_syncs` 统一门禁；失败 `error_code` 形如 `SYNC_DATA_TIMEOUT` / `SYNC_API_TIMEOUT` / `SYNC_UI_NO_PAGE`。
`depends_on` 仍按上游 `sync_point` 且必须真实通过（`RECOVERY_SKIP` 不满足依赖）。

## 清理阶段

标记 `cleanup: true` 的阶段将在所有正常阶段完成后执行（无论成功或失败），确保：
- API 测试数据清理 (DELETE)
- 浏览器/移动端登出
- 状态重置

## 执行器

| 模式 | 如何启用 | 说明 |
|------|----------|------|
| **classic（默认）** | 无需配置 | Web/Mobile/Desktop 走平台步骤执行器，缺 selector 直接失败 |
| **Hermes（可选）** | 阶段 `executor: "hermes"` / `use_hermes: true`，或计划 `default_ui_executor: "hermes"` | 需 Gateway 可用；不可用返回 `HERMES_UNAVAILABLE`，**不会**静默改回 classic |

Hermes 成功条件：回复含 `[RESULT] ok` 或 JSON `ok:true`；否则默认失败。

## 跨端断言

计划级 `assertions` / `cross_end_assertions` 在全部阶段后执行；失败则整体 `success=false`（`CROSS_END_ASSERT_FAILED`）。

```json
{
  "field": "balance",
  "sources": {
    "api": "stage-1.balance",
    "web": {"selector": ".wallet-balance", "source": "text"}
  },
  "tolerance": 0.01
}
```

也支持 `left`/`right`、`api`/`web` 简写，以及 `expected` 单源期望比对。来源可为上下文变量、`{{var}}`、或 UI `selector`（需浏览器页面）。缺源或仅单源无 expected 一律失败，禁止「跳过比较」假绿。

## 不适用场景

- 单端纯 UI 测试（用 ai-design / ai-test）
- 不需要跨端数据一致性验证的场景

## 维护

- 场景可保存/加载：`POST/GET /api/ai/cross-end/scenario`
- 执行结果包含 `summary: {passed, failed, skipped}` 和 `stage_results`
