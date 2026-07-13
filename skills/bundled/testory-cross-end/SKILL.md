---
name: testory-cross-end
description: Testory 跨端联动编排引擎：将自然语言描述的端到端测试场景分解为 API → Web → Mobile → Desktop 多阶段执行计划，支持变量透传、同步点管理、断言与恢复策略。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: cross-platform
    tags: [orchestration, e2e, api, web, mobile, desktop, sync-points, variable-passing]
---

# Testory 跨端联动编排

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
| `data_sync` | 等待某端数据就绪 | 30s |
| `state_sync` | 等待 UI 状态 (OCR 轮询) | 60s |
| `api_state_sync` | 等待 API 状态变更 | 30s |
| `time_sync` | 固定等待 | 配置 |
| `human_sync` | 人工确认 | 无限 |

## 清理阶段

标记 `cleanup: true` 的阶段将在所有正常阶段完成后执行（无论成功或失败），确保：
- API 测试数据清理 (DELETE)
- 浏览器/移动端登出
- 状态重置

## 不适用场景

- 单端纯 UI 测试（用 ai-design / ai-test）
- 不需要跨端数据一致性验证的场景

## 维护

- 场景可保存/加载：`POST/GET /api/ai/cross-end/scenario`
- 执行结果包含 `summary: {passed, failed, skipped}` 和 `stage_results`
