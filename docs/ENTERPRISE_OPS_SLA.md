# 企业运营与 SLA 叙事（轻量）

> 配套实现：`ai_modules/enterprise/readiness.py`、`GET /api/enterprise/ops-readiness`、农场 `dispatch-readiness`。

## 结论

Testory **不在产品内输出「SLA 已达标」绿灯**。  
商务上的可用性、响应时效、驻场支持由**合同 / `dedicated_support`** 约定；平台提供的是：

1. 执行诚实门禁（不假绿）  
2. 审计 / CI / Trace / 客户审计包等可核查能力  
3. 执行农场节点登记与**调度就绪检查**（前置条件齐备 ≠ 用例已并行通过）

## 平台侧可核查项

| 项 | 含义 |
|----|------|
| `dispatch_ready` | remote 模式 + 在线节点 + 网关密钥等前置齐备 |
| 农场任务队列 | `noop`/`probe`/`live_health`；`case_pass_claimed=false` |
| fan-out 探测 | `POST /api/enterprise/farm/fanout-probe`；`parallel_suite_pass_claimed=false` |
| remote 跨端门禁 | `DESKTOP_FARM_DISPATCH_GATE` + `FARM_DISPATCH_NOT_READY`（Desktop 预检） |
| 农场 Worker | `POST /api/enterprise/farm/jobs/drain`；drain ≠ 用例通过 |
| SLA 证据 | `GET /api/enterprise/sla-evidence`；样本/p50/p95，**非**达标证明 |
| SLA 告警 | `GET /api/enterprise/sla-alerts`；`sla_met` 恒 false |
| SLA Webhook（可选） | `SLA_ALERT_WEBHOOK_URL` + `POST .../sla-alerts/webhook`；通知≠达标 |
| 收口说明 | [PHASE_BC_COMPLETE.md](./PHASE_BC_COMPLETE.md) |
| `DESKTOP_FARM_GATEWAY=1` | 允许用农场在线节点补全 Gateway URL（仍需 SECRET） |
| 能力键 | `parallel_execution` / `audit_log` / `ci_integration` / `customer_audit_export` / `sso` |
| 多 Agent | Spec ≥5 角色；官方 SDK 可选 |
| 证据 | Trace Hub、`trace_pack`、客户审计 ZIP |
| MCP live | `gateway_live` health / 可选 wait；可达 ≠ 用例通过 |

## 明确不做

- 不把探测成功写成并行用例通过  
- 不把就绪清单写成 uptime SLA  
- 不自动改写客户 `.env`（仅给出 `env_suggestions`）
