# GOAI AgentTeams Demo

合成故事入口。默认 **R06 下单失败一致性**；`--suite guards` 覆盖 **R08 Desktop 闸门 / R09 HITL / R10 RiskGuard**。

## 一句话

- **order**：业务「下单失败」是被测现象；Demo 验证多 Agent 能否诚实检出跨端一致 / 不一致。  
- **guards**：验证码真实 HitlGate、Desktop 用生产 `validate_desktop_step_result`（模拟结果、不启真机）、L2 清数需 RiskGuard 令牌。

## 快速复现

```bash
# R06
python demos/goai-agentteams/run_demo.py
python demos/goai-agentteams/run_demo.py --variant mismatch

# R08/R09/R10
python demos/goai-agentteams/run_demo.py --suite guards --variant pass
python demos/goai-agentteams/run_demo.py --suite guards --variant hitl_timeout
python demos/goai-agentteams/run_demo.py --suite guards --variant l2_denied
python demos/goai-agentteams/run_demo.py --suite guards --variant desktop_softfail
```

| suite / variant | 期望 status | 要点 |
|-----------------|-------------|------|
| order / consistent | success | API+Web 一致 |
| order / mismatch | failed | UI 文案不一致 → 红灯 |
| guards / pass | success | HITL resume + Desktop ok + L2 已批 |
| guards / hitl_timeout | failed | HITL 事件入 Trace |
| guards / l2_denied | failed | `RISK_APPROVAL_REQUIRED` |
| guards / desktop_softfail | failed | warning 不得假绿 |

## 输入

| 路径 | 说明 |
|------|------|
| `input/order_fail_story.json` | R06 |
| `input/guards_story.json` | R08/R09/R10 |
| `samples/output/` | order+consistent 参考产出 |

## 证据包

每次运行写入 `artifacts/goai-agentteams/<suite>-<variant>-<ts>/trace_pack/`（含 HITL / Risk 事件文件）。
