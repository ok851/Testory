# Testory 产品北极星与目标体系

> 版本：2026-07-24  
> 状态：正式产品目标（与 GOAI 参赛同向，不以比赛结束而回退）  
> 关联：[执行可靠性标准](./EXECUTION_RELIABILITY_STANDARD.md) · [CI/CD 对接](./CICD_INTEGRATION.md) · [跨端缺陷债](./LINKAGE_DEFECT_BACKLOG.md) · [GOAI 材料](./goai/README.md)

---

## 1. 北极星（North Star）

**Testory：开源的企业级多端联动质量保障多 Agent Infra**

让同一条业务流在 **Web + Windows 桌面 + Android（+ API）** 上被：

1. 自动拆解与派单  
2. 在真实环境中协同执行  
3. **诚实**验证并留下可审计证据  
4. 沉淀为可复用 Skill / 用例  

### 1.1 准入条件（不满足则不得宣称「企业级」）

在对外或对内宣称「企业级 / 流水线就绪」之前，必须同时满足：

| 条件 | 说明 |
|------|------|
| **执行可信** | 符合 [EXECUTION_RELIABILITY_STANDARD](./EXECUTION_RELIABILITY_STANDARD.md) 的 S1–S4 |
| **历史报告同源** | 运行历史、测试报告、关键日志与真实执行结论一致，无假绿美化 |
| **可被 CI 门禁** | 外部流水线能触发运行并以机器可读结果判红/绿灯（见 [CICD_INTEGRATION](./CICD_INTEGRATION.md)） |

**多 Agent、炫酷 AI 演示，均排在上述条件之后。**

---

## 2. 为什么这个北极星可靠、有价值、可竞争

### 2.1 可靠性（方向是否站得住）

| 维度 | 判断 |
|------|------|
| 问题真实性 | 高。制造/金融/政务常见 Web + Windows ERP/胖客户端 + App 混合流，单端工具测不全。 |
| 技术可达性 | 中高。已有四端执行面、HITL、Skills、桌面产品形态；缺口在结果语义、联动编排、协同控制面。 |
| 最大风险 | 不是方向错，而是**宣称能力超过执行诚实度**（假绿/假传）。 |
| 用法 | 1–2 年航向；近几个迭代的门禁是「执行可信 + 可审计」，不是 Agent 数量。 |

### 2.2 商业价值

- **ROI**：混合流回归从专家串测小时级 → 可重复自动化复现与留证。  
- **客群**：有 Windows 客户端的企业 IT（高客单、私有化、合规意愿强）。  
- **开源飞轮**：核心与 Skills 开源获客；企业向增强（审计、SSO、并行节点、SLA）可选交付。  
  - License 门禁见 [`PRODUCT_TIERS.md`](./PRODUCT_TIERS.md)：standalone 默认可试用企业能力；商业部署用 `LICENSE_ENFORCE_FEATURES=1` 按档位拦截（**不锁** `test_execution` / 基础报告）。
- **非价值**：纯「又一个 Chat 测开」红海；溢价锚在 **Windows 混合流 + 同屏 HITL + 证据链 + CI 门禁**。

### 2.3 竞争力（差异化，非全面碾压）

| 竞品类型 | 我们怎么打 |
|----------|------------|
| Midscene 等视觉 SDK | 不拼 star；打 **测开平台化、用例/历史/报告、Windows ERP 深度、HITL** |
| Testim / Mabl 等 SaaS | 对方 Web/移动成熟；窗口在 **Windows 原生客户端 + 本地/私有化** |
| 裸 Playwright/Appium 栈 | 我们提供 NL→计划→执行→证据→CI 的产品层 |

**定位一句话：** Windows 混合流优先的开源质量执行平台，可被 Jenkins/GitLab CI 门禁；演进为多 Agent 质量 Infra。

---

## 3. 三层目标

| 层级 | 目标 | 成功标准 |
|------|------|----------|
| 北极星 | 企业级多端联动质量 Infra | 一次触发 → 多端协同 → 诚实证据 → 沉淀 |
| 一年 | 多 Agent 控制面 + 企业治理 | ≥5 角色；L0–L2；Trace；开源 Skill/MCP |
| 近季度 | **执行可信 + 跨端可信 + CI 最小对接** | S1–S4 门禁通过；变量真透传；JUnit/状态可门禁 |
| 基线（保留） | 四端执行面与产品化 | CDP / UIA / adb / API、桌面壳、bundled Skills |

---

## 4. 目标修改清单（优先级）

| ID | 目标 | 优先级 |
|----|------|--------|
| **G0a** | 用例执行达企业流水线级（不假绿/不假传）+ 历史报告日志同源 | **P0 最先** |
| **G0b** | 跨端编排正确（变量/HITL/锁/持久化） | P0 |
| **G0c** | CI 触发 + JUnit/状态回调 + 构建号关联历史 | P0（紧随 G0a） |
| G1 | 多职能 Agent（Planner / Executors / Verifier） | P0（依赖 G0a） |
| G2 | 协同控制面（赛期对齐 AgentTeams） | P0（依赖 G0） |
| G3 | TestRunState 共享状态 | P0 |
| G4–G9 | 审批、Trace、沉淀、MCP、审计、开源工程化 | P1–P2 |

---

## 5. 阶段顺序（强制）

```text
Phase 0a  执行可信（含历史/报告/日志诚实）          ✅ 已落地门禁与回归
    ├─► Phase 0b  跨端联动可信                      ✅ L1–L7 / 见 LINKAGE_DEFECT_BACKLOG
    └─► Phase 0c  CI 最小对接（可与 0b 并行，依赖 0a） ✅ 触发+JUnit+回调 / 见 CICD_INTEGRATION
         └─► Phase A  多 Agent 最小闭环              ✅ TestRunState+≥5 角色+Demo+跨端页时间线
              └─► Phase B/C  企业安全、沉淀、开源生态   ✅ **已收口**（见 [PHASE_BC_COMPLETE.md](./PHASE_BC_COMPLETE.md)）
```

**硬原则：** 没有 Phase 0a，不宣称企业级，不把比赛/销售 Demo 建立在假绿之上。  
**Phase A 说明：** 本地控制面 **≥5 角色** + Spec + SDK bridge/probe；官方 AgentTeams **完整 runtime 非阻塞延期**（未安装 → `SDK_NOT_INSTALLED`）。  
**Phase B/C 收口：** 企业审计/SSO/Trace/Skill/农场运维雏形/R18·R19 已齐；**默认停止继续堆 L2 运维微特性**，下一优先级是客户业务场景验收。详见 [PHASE_BC_COMPLETE.md](./PHASE_BC_COMPLETE.md)。  
**企业可运营（2026-07-24）：** 跨端页异步执行 + HITL 继续/取消 + RiskGuard 批准重试（同页可操作，不假绿）。  
**Trace 运营页：** `/trace-hub` — HITL/Risk/证据/CI 汇总、门禁事件、Skill 沉淀与配置注册中心入口。  
**客户审计包：** `/api/report/customer-audit-pack`；企业能力键 `customer_audit_export`。  
**认证审计：** `audit_logs`（`target_type=auth`）；企业能力键 `audit_log` / `sso`。  
**Skill 沉淀：** 成功运行 → 草稿；失败默认拒绝。  
**执行农场（雏形收口）：** 节点/门禁/队列 drain/fan-out/SLA 证据与可选 Webhook；探测成功 ≠ 用例通过。  
**R18/R19：** [CLOUD_SKILLS_ALIGN](./goai/CLOUD_SKILLS_ALIGN.md) · [NACOS_CONFIG_REGISTRY](./goai/NACOS_CONFIG_REGISTRY.md)。  
**ERP 桌面样例：** Fake ERP + `@erp`；`python demos/erp_desktop_sample/run_accept.py`。

---

## 6. 非目标（防止分心）

- 不做通用运维/客服 Agent 平台（主线是质量保障）。  
- 不与 Midscene 拼纯视觉 SDK 生态位。  
- 不以「导出脚本给 Jenkins 自己点 UI」替代平台执行主路径。  
- iOS / 云真机不进近半年 P0。  
- 不把「单 Hermes 对话成功」表述为多 Agent 已完成。

---

## 7. 架构原则

| 组件 | 定位 |
|------|------|
| Hermes | 端侧工具运行时（挂在 Executor 下） |
| 协同控制面 | 角色编排、派单、共享状态、升级（赛期 AgentTeams） |
| Skill | 任务能力抽象 |
| 适配器 / MCP | 连接真实浏览器/桌面/手机/HTTP |
| Jenkins 等 CI | **流水线编排与门禁**，不是 UI 执行引擎 |

「联动」= 共享状态的一条 TestRun，不是三端各跑各的。  
「企业级」= 结果诚实 + 可审计 + 可 CI 门禁，然后再谈智能程度。

---

## 8. 开源与档位叙事

- 项目以**开源**方式建设核心执行、Skills 与文档（协议见开源计划，拟 Apache-2.0）。  
- [`PRODUCT_TIERS.md`](./PRODUCT_TIERS.md) 中的免费/团队/企业能力，应理解为：**开源核心上的可选增强与交付打包**，而非「闭源锁定才能用基础执行」。  
- 企业增强示例：SSO、细粒度审计、远程执行农场、专有支持——建立在 G0a 可信执行之上。

---

## 9. 文档地图

| 文档 | 用途 |
|------|------|
| 本文 | 北极星与优先级 |
| [PHASE_BC_COMPLETE.md](./PHASE_BC_COMPLETE.md) | **B/C 收口与业务优先级说明（必读）** |
| [EXECUTION_RELIABILITY_STANDARD.md](./EXECUTION_RELIABILITY_STANDARD.md) | S1–S4、假绿清单、历史报告规范、门禁 |
| [CICD_INTEGRATION.md](./CICD_INTEGRATION.md) | Jenkins/GitLab 分工与对接契约 |
| [ENTERPRISE_OPS_SLA.md](./ENTERPRISE_OPS_SLA.md) | 农场/SLA 证据边界 |
| [LINKAGE_DEFECT_BACKLOG.md](./LINKAGE_DEFECT_BACKLOG.md) | 跨端缺陷债（Phase 0b） |
| [goai/](./goai/README.md) | 参赛材料；复赛工程依赖 0a/0b |
