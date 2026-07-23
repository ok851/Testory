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

### P1 — 显著加分 / 对齐企业级

| ID | 任务 | 产出 |
|----|------|------|
| R08 | 接入 **DesktopExecutor** 或 **MobileExecutor**（至少一端进主 Demo） | 真·多端联动视频 |
| R09 | **HitlGate** 演示：登录/验证码暂停与恢复 | HITL 事件入 Trace |
| R10 | **RiskGuard** L0/L1/L2 + 一次 L2 审批演示 | 审批记录 |
| R11 | Skill 包按附录 B 补齐 Schema/失败处理/安全章节 | 开源 Skill 质量 |
| R12 | MCP 或等价契约文档 + 1 个适配器样例（Desktop 或 CDP） | 工具连接层证据 |
| R13 | Trace 看板或导出（OTel/AgentLoop/自研 JSON Trace 三选一） | 可观测材料 |
| R14 | Apache-2.0 LICENSE + NOTICE + 根 README 复现步骤 | 开源合规 |

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

- [ ] 从样例输入启动，无需口播改代码  
- [ ] 日志中可见 ≥3 个 Agent 的派单/完成事件  
- [ ] 至少一次跨 Skill 工具调用成功  
- [ ] 输出明确 pass/fail 与证据文件  
- [ ] 演示一次 HITL 或 L2 审批  
- [ ] 演示 cleanup 或失败分支之一  
- [ ] README 按步骤外人可复现（或视频完整展示）  

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
