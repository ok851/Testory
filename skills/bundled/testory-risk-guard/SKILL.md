---
name: testory-risk-guard
description: Testory RiskGuard：L0/L1/L2 风险分级与 L2 审批令牌；跨端编排在执行前强制门禁。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: security
    risk_default: L2
    tags: [risk, approval, L0, L1, L2, security]
---

# Testory RiskGuard

实现位于 `ai_modules/security/risk_guard.py`，由跨端编排在阶段执行前调用。  
本 Skill 说明**契约与边界**，不另起第二套审批逻辑。

## 输入 / 输出 Schema

### 分级输入（阶段字段）

| 字段 | 类型 | 说明 |
|------|------|------|
| `risk_level` / `risk` | `L0\|L1\|L2` | 显式优先；缺省按动作关键词与 HTTP method 推断 |
| `risk_action` | string | 动作标识（如 `clear_data`） |
| `risk_reason` | string | 展示给审批人的原因 |
| `approval_token` | string | 已批准令牌；也可写在 `plan.approvals[stage_id]` |

### 决策输出（`evaluate_stage_risk` → `RiskDecision`）

| 字段 | 说明 |
|------|------|
| `ok` | 是否允许执行 |
| `level` | 实际等级 |
| `decision` | `allow` \| `require_approval` \| `denied` |
| `error_code` | `RISK_APPROVAL_REQUIRED` / `RISK_TOKEN_INVALID` / … |
| `approval_id` | pending 或已用审批 ID |
| `events` | 可供 Trace 的事件列表 |

### 审批 API

```python
from ai_modules.security.risk_guard import (
    request_approval, approve_risk, deny_risk, evaluate_stage_risk
)

rec = request_approval(stage_id="stage-l2", level="L2", reason="清演示数据")
ok, token = approve_risk(rec["approval_id"], approver="lead")
stage["approval_token"] = token
decision = evaluate_stage_risk(stage, plan=plan)
```

## 失败处理（诚实）

| 情况 | 结果 | 不得 |
|------|------|------|
| L2 无令牌 | `ok=False`，`RISK_APPROVAL_REQUIRED`，自动创建 pending | 静默放行 |
| 令牌无效/未批 | `RISK_TOKEN_INVALID` | 当成功 |
| 令牌绑定其他 stage | `RISK_TOKEN_STAGE_MISMATCH` | 跨阶段复用假装合法 |
| 审批拒绝后仍带旧语义 | 无效令牌路径失败 | 假绿 |

编排：`orchestrator` 在同步门禁后、业务步骤前调用；失败阶段写入 `risk_events`，进入 Trace `risk_events.json` 与审计 `meta.risk`。

Demo：`python demos/goai-agentteams/run_demo.py --suite guards --variant l2_denied`

## 安全边界

- **L0**：只读探测（如 GET API、screenshot/inspect 类标记）— 默认可自动  
- **L1**：常规点击/断言/查询 — 白名单自动  
- **L2**：清数据、卸装 APK、写生产、显式 `risk_level: L2` — **必须**审批令牌  
- `cleanup: true` ** alone 不自动升 L2**（兼容旧计划）；破坏性清理请显式 `risk_level: L2`  
- 与 HITL 分工：HITL=人机操作门禁；RiskGuard=策略/审批门禁，二者可同计划并存  

## 触发词

- L2 审批 / 高风险动作 / clear_data / RiskGuard  
- 审批令牌 / approval_token  

## 不适用

- 替代操作系统权限或云 IAM  
- 替代产品商业 `license.key` 授权  
