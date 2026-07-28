# GOAI 参赛材料与产品主线

> 比赛是催化剂；**产品主线**以执行可信为地基，见上级文档。

## 产品主线（优先阅读）

| 文档 | 说明 |
|------|------|
| [../PHASE_BC_COMPLETE.md](../PHASE_BC_COMPLETE.md) | **B/C 收口与业务优先级（必读）** |
| [../PRODUCT_NORTH_STAR.md](../PRODUCT_NORTH_STAR.md) | 北极星、阶段顺序、市场判断 |
| [../EXECUTION_RELIABILITY_STANDARD.md](../EXECUTION_RELIABILITY_STANDARD.md) | **企业流水线级执行标准（S1–S4）** |
| [../CICD_INTEGRATION.md](../CICD_INTEGRATION.md) | Jenkins 等 CI 与 Testory 分工 |
| [../ENTERPRISE_OPS_SLA.md](../ENTERPRISE_OPS_SLA.md) | 企业运营就绪 / SLA 叙事边界 |
| [../LINKAGE_DEFECT_BACKLOG.md](../LINKAGE_DEFECT_BACKLOG.md) | 跨端联动缺陷债（Phase 0b） |

## 本目录

| 文件 | 说明 |
|------|------|
| [semifinal_backlog.md](./semifinal_backlog.md) | 复赛 AgentTeams 工程 backlog（**依赖**执行 0a/0b 完成） |
| [SKILL_APPENDIX_B.md](./SKILL_APPENDIX_B.md) | **R11** Skill Schema / 失败 / 安全标准 |
| [MCP_CONTRACT.md](./MCP_CONTRACT.md) | **R12** MCP 工具连接层契约 |
| [out/](./out/) | 初赛 PPT 等产出 |

## Phase A 工程入口（已落地骨架）

| 路径 | 说明 |
|------|------|
| `ai_modules/agent_teams/` | ≥5 角色：Planner / RiskAdvisor / DesktopExecutor / WebApiExecutor / Verifier；R16 重规划 |
| `ai_modules/agent_teams/sdk_bridge.py` | 官方 AgentTeams SDK 可选映射（本地事件导出） |
| `ai_modules/agent_teams/specs/testory-cross-end-qa-team.json` | 团队 Spec（本地控制面，可映射官方 AgentTeams） |
| `ai_modules/memory/` | **R15** IncidentMemory / Runbook 轻量检索 |
| `POST /api/ai/agent-teams/runs` | 启动多角色闭环（可传 `plan` 跳过 LLM） |
| `GET /api/ai/agent-teams/runs/<run_id>` | 读取共享 TestRunState |
| `GET /api/ai/agent-teams/runs/<run_id>/report` | Verifier 报告 |
| `GET/POST /api/ai/incident-memory/search` | 事故/Runbook 建议（**不判绿**） |
| `PUT /api/desktop/aliases/<alias>` | 真实客户 ERP 别名持久化 |
| [`../demos/goai-agentteams/`](../demos/goai-agentteams/README.md) | **R06** order 故事；**R08/R09/R10** `--suite guards` |
| `templates/cross_end.html` | **A-3** 时间线 / Verifier；**B-2** 导出证据包；HitlGate/RiskGuard 标签 |
| `ai_modules/execute/trace_pack.py` | **B-2/B-3** Trace + HITL/Risk 事件文件 |
| `agent_hitl.py` | **B-3** gate 事件日志 |
| `ai_modules/security/risk_guard.py` | **R10** L0/L1/L2 + 审批令牌；编排门禁 |

## Phase B/C 企业沉淀切片

| 路径 | 说明 |
|------|------|
| `ai_modules/skills/promote_from_run.py` | 成功运行 → Skill 草稿；失败默认拒绝 |
| `ai_modules/enterprise/execution_farm.py` | 节点登记/探测（探测 ≠ 并行已通） |
| `ai_modules/config_registry/` | **R19** 本地 Spec 注册中心 |
| `/trace-hub` · `/execution-farm` | Trace 运营页 · 执行农场页 |
| [CLOUD_SKILLS_ALIGN.md](./CLOUD_SKILLS_ALIGN.md) | **R18** 云 Skills 生态对齐（非关键旁路） |
| [NACOS_CONFIG_REGISTRY.md](./NACOS_CONFIG_REGISTRY.md) | **R19** Nacos 叙事说明 |
| `demos/golden/run_golden.py` | **R17** Golden G1–G15 |
| `testory_mcp/gateway_live.py` | Desktop Gateway 实连接探活（health ≠ 用例绿） |
| `ai_modules/enterprise/farm_batch.py` | 多节点 probe fan-out（≠ 并行套件通过） |
| `ai_modules/agent_teams/sdk_runtime.py` | 官方 SDK 运行时探测（未安装诚实失败） |

**诚实约束：** 终态 `success` 仅当执行门禁通过且 Verifier 判定通过；不得把单 Hermes 对话成功表述为多 Agent 完成。

初赛简介 / Identity / Skill 等文稿若需恢复，可从 git 历史检出，或按 `PRODUCT_NORTH_STAR` 与执行标准重生成。

## 硬约束

复赛 Demo 与多 Agent 叙事 **不得**建立在假绿执行上。先过 `EXECUTION_RELIABILITY_STANDARD` 门禁，再推进本目录 semifinal backlog。
