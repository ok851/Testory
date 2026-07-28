# Golden 案例集（R17）

离线、可重复的诚实性回归入口。不依赖本机桌面/浏览器；失败即非零退出。

```bash
python demos/golden/run_golden.py
```

| ID | 覆盖 |
|----|------|
| G1 | HITL 超时不得 success |
| G2 | 跨端结果 Schema 归一（Mock↔live） |
| G3 | Desktop 有限 UIA 自愈提案 + 失败不假绿 |
| G4 | 企业档能力门禁（旧证合并目录） |
| G5 | RECOVERY_SKIP 默认挡总成功 |
| G6 | IncidentMemory / Runbook 检索（建议不判绿） |
| G7 | Verifier→Planner 重规划后仍失败不假绿 |

禁止把本脚本结果宣传为「生产自愈已完成」或「多 Agent SDK 已对接」。
