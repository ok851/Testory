# 附录 B：Skill 工程质量标准（R11）

> 版本：2026-07-24  
> 上级：[semifinal_backlog.md](./semifinal_backlog.md) · [PRODUCT_NORTH_STAR.md](../PRODUCT_NORTH_STAR.md)  
> 源码：`skills/bundled/*/SKILL.md` · 校验：`skills/skill_quality.py`

每个对外 Skill **必须**同时具备：**输入输出 Schema、调用条件、失败处理、安全边界**。  
不得只写「怎么点按钮」而省略失败语义与风险等级。

---

## 1. 完成定义

| # | 门禁 | 验收 |
|---|------|------|
| B1 | YAML frontmatter 完整 | `name` / `description` / `version` / `format` |
| B2 | 平台元数据 | `metadata.testory.platform`；建议 `risk_default` ∈ L0\|L1\|L2 |
| B3 | **输入输出** 专章 | 请求字段、成功响应、关键错误码可对照 |
| B4 | **失败处理** 专章 | 超时/无环境/软失败 → 不得假绿；给出 `error_code` 或等价约定 |
| B5 | **安全边界** 专章 | 与 RiskGuard / HITL 关系；禁止事项；敏感数据 |
| B6 | 校验可自动化 | `python -m skills.skill_quality` 或 pytest 通过 |

**门禁 Skill（本仓强制）：**  
`testory-web-browser` · `testory-api-http` · `testory-windows-desktop` · `testory-android-mobile` · `testory-cross-end` · `testory-risk-guard`

其余 bundled Skill（如 ui-design）建议对齐，不阻塞 R11。

---

## 2. Frontmatter 模板

```yaml
---
name: testory-example
description: 一句话能力说明（给 Agent 路由用）
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: web          # web | api | desktop | mobile | cross-platform | security
    risk_default: L1       # L0 只读 / L1 常规 / L2 需审批
    tags: [example]
---
```

---

## 3. 正文必含章节（标题可同义）

校验器识别（不区分大小写）：

| 主题 | 标题关键词（任一） |
|------|-------------------|
| 输入输出 | `输入` `输出` `Schema` `I/O` `契约` |
| 失败处理 | `失败` `错误` `超时` `诚实` |
| 安全边界 | `安全` `风险` `边界` `RiskGuard` `HITL` |

章节内容最低要求：

1. **输入输出**：列出关键字段表或 JSON 示例；成功/失败形状不同。  
2. **失败处理**：至少覆盖「无环境 / 超时 / 断言失败」；写明不得 `ok=true`。  
3. **安全边界**：默认风险级；何种动作升 L2；是否需 HITL；敏感字段脱敏。

---

## 4. 与执行诚实标准对齐

Skill 文档不得与 [EXECUTION_RELIABILITY_STANDARD.md](../EXECUTION_RELIABILITY_STANDARD.md) 冲突：

- 无 page / 无设备 / 无审批 → 失败  
- Desktop `warning` → 跨端不得绿  
- HITL 超时/取消 → 失败  
- L2 无令牌 → `RISK_APPROVAL_REQUIRED`

---

## 5. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-24 | 首版附录 B；配套 `skill_quality` 与门禁 Skill 专章 |
