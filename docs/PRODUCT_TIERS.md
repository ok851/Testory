# 产品档位与能力打包（免费 / 团队 / 企业）

> **叙事对齐（2026-07）：** Testory 以**开源核心**建设执行引擎、Skills 与文档；下表中的免费/团队/企业表示 **能力打包与可选增强交付**，不是「闭源后才能跑基础用例」。  
> 企业级前提：执行结果须符合 [EXECUTION_RELIABILITY_STANDARD.md](./EXECUTION_RELIABILITY_STANDARD.md)；产品方向见 [PRODUCT_NORTH_STAR.md](./PRODUCT_NORTH_STAR.md)。  
> CI 对接见 [CICD_INTEGRATION.md](./CICD_INTEGRATION.md)。

本文档与 `license_manager.LicenseType` 对齐：**枚举值不变**（`free` / `professional` / `enterprise`），避免破坏已有 `license.key`；对外叙事使用「免费版 / 团队版 / 企业版」。

## 三档定位

| 档位 | 部署与获客 | 核心价值 | 说明 |
|------|-------------|----------|------|
| **免费版** | 本机 / 开源试用 | 用例、执行、基础报告，低门槛体验 | 须遵守执行诚实标准；开源用户同等 |
| **团队版**（`professional`） | 协作与配额增强 | 多协作、调度、数据驱动、缺陷、Webhook、跨端编排等 | 建立在可信执行与历史报告之上 |
| **企业版**（`enterprise`） | 私有化 / 专有云 / 内网增强包 | SSO、审计、客户审计包、API、并行节点、CI 深度集成、**私有化部署权益**等 | **单客高价值交付**；不替代开源核心 |

企业版将 **私有化/专有云** 与 **合规审计 / 执行农场** 作为高价值项，与仅协作增强的团队版区分；**均要求执行层无假绿**。

## 功能开关（2026-07 对齐）

| Feature key | 最低档（强制时） | 路由/入口 | 说明 |
|-------------|------------------|-----------|------|
| `test_execution` / `basic_report` | free（**永不因档位锁死**） | 执行与基础报告 | 开源核心 |
| `cross_end` | professional | 跨端编排 | standalone 默认可试用 |
| `sso` | enterprise | `/sso-settings`、`/api/sso/configs*` | 登录回调本身不门禁 |
| `audit_log` | enterprise | `/audit-logs`、`/api/audit-logs*` | 含登录/SSO 审计筛选 |
| `customer_audit_export` | enterprise | `/api/report/customer-audit-pack` | 客户向 ZIP |
| `ci_integration` | enterprise | `POST /api/ci/runs` | CI 触发深度对接 |

**本机开源（`DEPLOYMENT_MODE=standalone`，默认）：** 企业能力默认可试用（`open_core_features_unlocked`）。  
**商业强制门禁：** 设置 `LICENSE_ENFORCE_FEATURES=1` 后，按证书 `features` 拦截；403 返回 `error_code=LICENSE_FEATURE_REQUIRED` 与 `gate` 说明。

`FEATURE_CATALOG` / `describe_feature_gate()` / `get_limits().effective_features` 供升级页与运维展示。

**前端：** `static/js/license_feature_gate.js`（`TestoryLicenseGate`）处理 API 403；浏览器打开 `/audit-logs`、`/sso-settings` 等页面时重定向到 `/license?gate=...&denied=1`。SSO / 定时任务 / 缺陷 / 数据驱动 / 客户审计包列表加载失败时展示横幅与升级链接（不假开能力）。

## 与能力演进方向的对应关系

以下与「从生成步骤到理解意图、视觉 + 结构、运行时自愈」等规划一致，用于分档**打包**，而非一次性全量实现：

- **免费版**：自然语言生成步骤、基础断言与执行、本地/默认模型配额内使用（具体以产品配置为准）。
- **团队版**：对话改步骤、更丰富的用例/协作、**意图侧**增强（澄清问题、多场景扩写等）、DOM/探测增强的优先体验。
- **企业版**：团队版全部能力 + **视觉锚定与自愈**类高级能力、**跨系统集成**、审计与合规、**失败 → 缺陷/工单**类集成；私有化客户可单独签 **air-gap / 驻场** 等条款。

功能颗粒度以 `LicenseInfo.features` 与 `check_feature_available()` 为准；企业专属标识含 `private_deployment`、`dedicated_support`、`customer_audit_export`、`ci_integration` 等。

## API / 实现说明

- `LicenseManager.get_limits()` 会额外返回 `product_display_name`、`offering_summary`、`private_deployment_eligible`、`effective_features`、`open_core_features_unlocked`、`feature_catalog`，供 Web 端展示与升级页使用。
- 新增团队版能力项 `team_collaboration`、`cross_end` 已并入 `FEATURES['professional']`，与已有企业版中的协作能力不冲突。

## License 工具

- `generate_license.py`：`professional` 即 **团队版** SaaS 证书；`enterprise` 即 **企业版**（含私有化等商务权益的授权载体）。

## 与产品交互文档的衔接

- 对话式改步骤、用例步骤页 AI 浮层、禅模式与 `interaction_context` 等前端落地说明见 [UX_AI_INTERACTION_ROADMAP](UX_AI_INTERACTION_ROADMAP.md)（其中 **1、2 项**已按文内「完成标准」在本仓库交付；与档位打包策略配合时以 `LicenseInfo.features` 与部署配置为准）。
