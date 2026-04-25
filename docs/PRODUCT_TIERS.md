# 产品档位与商业形态（免费 / 团队 SaaS / 企业私有化）

本文档与 `license_manager.LicenseType` 对齐：**枚举值不变**（`free` / `professional` / `enterprise`），避免破坏已有 `license.key`；对外叙事使用「免费版 / 团队版 / 企业版」。

## 三档定位

| 档位 | 部署与获客 | 核心价值 | 利润逻辑 |
|------|-------------|----------|----------|
| **免费版** | 公有云 / 线上 | 用例、执行、基础报告，低门槛体验 | 转化与数据飞轮 |
| **团队版**（`professional`） | **Web 付费 SaaS** | 多协作、高配额、调度、数据驱动、缺陷、Webhook 等 | 订阅收入，单客 ARPU 中 |
| **企业版**（`enterprise`） | **SaaS 协议 + 私有化 / 专有云 / 内网**（商务交付） | SSO、审计、API、并行、集成、**私有化部署权益**等 | **单客高毛利、长周期合同** |

企业版将 **私有化/专有云** 作为高价值交付项，与仅在线订阅的团队版明确区分，便于销售与产研统一话术。

## 与能力演进方向的对应关系

以下与「从生成步骤到理解意图、视觉 + 结构、运行时自愈」等规划一致，用于分档**打包**，而非一次性全量实现：

- **免费版**：自然语言生成步骤、基础断言与执行、本地/默认模型配额内使用（具体以产品配置为准）。
- **团队版**：对话改步骤、更丰富的用例/协作、**意图侧**增强（澄清问题、多场景扩写等）、DOM/探测增强的优先体验。
- **企业版**：团队版全部能力 + **视觉锚定与自愈**类高级能力、**跨系统集成**、审计与合规、**失败 → 缺陷/工单**类集成；私有化客户可单独签 **air-gap / 驻场** 等条款。

功能颗粒度以 `LicenseInfo.features` 与 `check_feature_available()` 为准；企业专属标识含 `private_deployment`、`dedicated_support` 等，用于界面与运营区分。

## API / 实现说明

- `LicenseManager.get_limits()` 会额外返回 `product_display_name`、`offering_summary`、`private_deployment_eligible`（企业版为 `true`），供 Web 端展示与升级页使用。
- 新增团队版能力项 `team_collaboration` 已并入 `FEATURES['professional']`，与已有企业版中的协作能力不冲突。

## License 工具

- `generate_license.py`：`professional` 即 **团队版** SaaS 证书；`enterprise` 即 **企业版**（含私有化等商务权益的授权载体）。

## 与产品交互文档的衔接

- 对话式改步骤、用例步骤页 AI 浮层、禅模式与 `interaction_context` 等前端落地说明见 [UX_AI_INTERACTION_ROADMAP](UX_AI_INTERACTION_ROADMAP.md)（其中 **1、2 项**已按文内「完成标准」在本仓库交付；与档位打包策略配合时以 `LicenseInfo.features` 与部署配置为准）。
