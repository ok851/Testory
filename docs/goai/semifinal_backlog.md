# 复赛工程 Backlog：AgentTeams 最小可运行闭环

---

## 里程碑

| 里程碑 | 完成标准 |
|--------|----------|
| **M1 协同骨架** | AgentTeams Spec 可加载；3 角色（Planner/WebApi/Verifier）能派单与回传 |
| **M2 跨端故事** | API→Web→（Desktop 或 Mobile 至少一端）→Verify 全链路可跑 |
| **M3 证据与安全** | Trace/Log 可导出；HITL + L2 审批点可演示；cleanup 可跑 |
| **M4 开源交付** | LICENSE、README、样例 I/O、Demo 脚本、依赖披露齐全 |

---

## Backlog（按优先级）

### P0 — 没有则无法过复赛

| ID | 任务 | 产出 | 依赖 |
|----|------|------|------|
| R01 | 引入 AgentTeams（或官方要求的集成方式），编写 `testory-cross-end-qa-team` Spec | Spec 文件 + 启动入口 | 官方文档/SDK |
| R02 | 实现/适配 **Planner Agent**：包装现有 `CrossEndDecompose` | 接收 NL → 输出 plan 事件 | R01、现有 cross-end API |
| R03 | 实现/适配 **WebApiExecutor Agent**：调用 WebBrowse + ApiHttp Skills | 阶段结果写回共享状态 | R01、Hermes/CDP、api-http |
| R04 | 实现/适配 **Verifier Agent**：断言聚合 + 证据等级报告 | `report.json` + 通过/失败 | R02–R03 |
| R05 | **TestRunState** 共享状态（vars、阶段、证据索引、幂等键） | 状态 Schema + 存取 API | R01 |
| R06 | 端到端 Demo 脚本：合成「下单失败」故事 + 样例输入输出 | `demos/goai-agentteams/` | R02–R05 |
| R07 | 运行证据：日志、阶段时间线、截图索引；打包为评审目录 | `artifacts/` | R06 |

> **Phase A-1：** R01 Spec（JSON）+ R02–R05 本地控制面；入口 `ai_modules/agent_teams/` 与 `/api/ai/agent-teams/*`（官方 AgentTeams SDK 仍属后续）。  
> **Phase A-2（R06）：** `demos/goai-agentteams/run_demo.py` 离线 simulate（`consistent`/`mismatch`）+ `samples/output/`；产物目录 `artifacts/goai-agentteams/`（部分满足 R07）。  
> **Phase B-2（R07/Z2）：** `ai_modules/execute/trace_pack.py` 标准化证据包（目录+ZIP）；`GET /api/ai/trace-packs/export`、`.../agent-teams/runs/<id>/trace`；Demo 自动写入 `trace_pack/`。  
> **Phase B-3（R09 工程半）：** HITL 事件入阶段/`trace_pack`/`meta.hitl`（`agent_hitl` 事件日志 + `tests/test_hitl_trace.py`）。  
> **R08+R09+R10 Demo：** `demos/goai-agentteams --suite guards`（Desktop 闸门模拟 + 真实 HitlGate + RiskGuard L2）；`ai_modules/security/risk_guard.py`；编排 L2 门禁。

### P1 — 显著加分 / 对齐企业级

| ID | 任务 | 产出 |
|----|------|------|
| R08 | ~~Desktop 进主 Demo（模拟+生产闸门）~~ **已关（guards suite）**；~~记事本主路径+预检~~ **已关（enterprise desk-mp）** | `desktop_preflight` + `desktop-mainpath-plan` + 跨端一键模板 |
| R09 | ~~HITL 事件入 Trace + HitlGate 演示~~ **已关（B-3 + guards）** | `hitl_events` / `--variant hitl_timeout\|pass` |
| R10 | ~~RiskGuard L0/L1/L2 + L2 审批演示~~ **已关** | `risk_guard` + 编排门禁 + `--variant l2_denied\|pass` |
| R11 | ~~Skill 包按附录 B 补齐 Schema/失败处理/安全章节~~ **已关** | [SKILL_APPENDIX_B.md](./SKILL_APPENDIX_B.md) + `skills/skill_quality` |
| R12 | ~~MCP 或等价契约 + Desktop 适配器样例~~ **已关** | [MCP_CONTRACT.md](./MCP_CONTRACT.md) + `demos/goai-mcp-adapter/` |
| R13 | ~~Trace 看板或导出~~ **已关（B-2 + 客户审计包）** | 单次 `trace_pack` + 批量 `customer_audit_pack` ZIP |
| R14 | ~~Apache-2.0 LICENSE + NOTICE + 根 README~~ **已关** | 根目录 `LICENSE` / `NOTICE` / `README.md` |

### P2 — 有余力再做

| ID | 任务 | 产出 |
|----|------|------|
| R15 | RunbookRag / IncidentMemory（向量或轻量检索） | RAG 2 项中再落 1 项 |
| R16 | 失败重规划：Verifier → Planner 回边 | 异常分支 |
| R17 | Golden 案例集（3–5 条）与简单回归脚本 | 评测证据 |
| R18 | 阿里云用云 Skills 择 1 个非关键路径演示（可选） | 生态对齐说明 |
| R19 | Nacos 仅作 Spec/Skill 配置托管（可选，勿堆料） | 治理叙事 |

---

## 建议排期（3 周示意）

| 周 | 焦点 |
|----|------|
| W1 | R01–R05：AgentTeams + 三 Agent + 共享状态 |
| W2 | R06–R10：主 Demo + 第二端 + HITL/审批 |
| W3 | R11–R14：Skill/MCP 文档、Trace、开源交付与录像 |

---

## Demo 验收清单（复赛自测）

- [x] 从样例输入启动，无需口播改代码  
- [x] 日志中可见 ≥3 个 Agent 的派单/完成事件  
- [x] 至少一次跨 Skill 工具调用成功（API+Web；guards 另含 Desktop 闸门模拟）  
- [x] 输出明确 pass/fail 与证据文件  
- [x] 演示一次 HITL 或 L2 审批（`--suite guards`）  
- [x] 演示 cleanup 或失败分支之一（`mismatch` / `hitl_timeout` / `l2_denied` / `desktop_softfail`）  
- [x] README 按步骤外人可复现（根 README + demos README）  

---

## 与产品北极星对齐

| 比赛交付 | 产品沉淀 |
|----------|----------|
| 多 Agent 角色 | 企业级协同控制面 |
| TestRunState | 跨端联动会话模型 |
| RiskGuard/HITL | 生产安全边界 |
| 开源 Skills | 生态与社区复用 |
| Trace | 审计与质量评估 |

不做与北极星无关的「纯运维告警自愈」分叉，除非作为对比附录。
