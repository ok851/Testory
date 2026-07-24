# GOAI AgentTeams Demo — 下单失败：API 与 Web 状态一致

- run_id: `run-e1b442314fb5`
- status: **success**
- agents: system, Planner, WebApiExecutor, Verifier
- evidence_level: strong
- reason: 全部阶段与断言通过

## Events

- `2026-07-24T09:54:28.701511+00:00` **system**/note: TestRunState created
- `2026-07-24T09:54:28.701511+00:00` **system**/note: 启动 Agent 团队闭环
- `2026-07-24T09:54:28.701511+00:00` **Planner**/dispatch: 规划跨端任务图
- `2026-07-24T09:54:28.701511+00:00` **Planner**/complete: 使用调用方提供的 plan（跳过 LLM）
- `2026-07-24T09:54:28.702507+00:00` **WebApiExecutor**/dispatch: 执行跨端阶段
- `2026-07-24T09:54:28.702507+00:00` **WebApiExecutor**/complete: 阶段执行完成
- `2026-07-24T09:54:28.703643+00:00` **Verifier**/dispatch: 聚合断言与证据
- `2026-07-24T09:54:28.703643+00:00` **Verifier**/complete: 验证通过: 全部阶段与断言通过
- `2026-07-24T09:54:28.750391+00:00` **system**/note: 已写入运行历史

## Evidence kinds

- hitl: 0
- risk: 2
- total_evidence: 6
