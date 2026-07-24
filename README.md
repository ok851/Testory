# Testory

多端联动自动化测试平台（Web / API / Windows Desktop / Android），强调**执行诚实**：无环境、超时、人机门禁与高风险动作不得假绿。

## License

Apache License 2.0 — see [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE).

> 根目录 `license.key` / `license_manager.py` 属于**产品授权**机制，与开源 SPDX 许可证文件无关。

## 产品主线文档

| 文档 | 说明 |
|------|------|
| [docs/PRODUCT_NORTH_STAR.md](./docs/PRODUCT_NORTH_STAR.md) | 北极星与阶段顺序 |
| [docs/EXECUTION_RELIABILITY_STANDARD.md](./docs/EXECUTION_RELIABILITY_STANDARD.md) | 执行可信标准 S1–S4 |
| [docs/LINKAGE_DEFECT_BACKLOG.md](./docs/LINKAGE_DEFECT_BACKLOG.md) | 跨端联动缺陷债 |
| [docs/CICD_INTEGRATION.md](./docs/CICD_INTEGRATION.md) | CI / Jenkins 对接 |
| [docs/goai/README.md](./docs/goai/README.md) | 多 Agent / 复赛材料入口 |
| [docs/goai/SKILL_APPENDIX_B.md](./docs/goai/SKILL_APPENDIX_B.md) | Skill 附录 B 质量标准 |
| [docs/goai/MCP_CONTRACT.md](./docs/goai/MCP_CONTRACT.md) | MCP 工具连接层契约 |

## 快速复现（离线 Demo，无需浏览器 / LLM）

在仓库根目录：

```bash
# R06：下单失败一致性（consistent 绿 / mismatch 红）
python demos/goai-agentteams/run_demo.py
python demos/goai-agentteams/run_demo.py --variant mismatch

# R08 + R09 + R10：Desktop 闸门模拟 + HITL + L2 审批
python demos/goai-agentteams/run_demo.py --suite guards --variant pass
python demos/goai-agentteams/run_demo.py --suite guards --variant hitl_timeout
python demos/goai-agentteams/run_demo.py --suite guards --variant l2_denied
python demos/goai-agentteams/run_demo.py --suite guards --variant desktop_softfail

# R11：Skill 附录 B 校验
python -m skills.skill_quality

# R12：Desktop MCP 适配器离线样例
python demos/goai-mcp-adapter/run_sample.py --out artifacts/goai-mcp-adapter
```

产物目录：`artifacts/goai-agentteams/`（含 `trace_pack/` 证据包）。

## 关键回归（抽样）

```bash
python -m pytest tests/test_risk_guard.py tests/test_hitl_trace.py tests/test_goai_guards_demo.py tests/test_skill_appendix_b.py tests/test_mcp_contract.py -q
```

## 本地平台（可选）

```bash
# 依赖按项目惯例安装后
python app.py
```

跨端页可导出证据包；HITL / RiskGuard 失败会显示友好错误提示。

## 诚实约束（摘要）

- HITL 超时/取消 → 失败  
- L2 无审批令牌 → 失败（`RISK_APPROVAL_REQUIRED`）  
- Desktop `warning` / 软失败 → 跨端不得当绿  
- 证据包与 `run_history` 与终态一致  

## 企业运营（跨端页）

跨端执行默认 **异步**：页面不阻塞，可在「人机确认与风险审批」面板：

- HITL：**继续** / **取消**（`/api/ai/agent/hitl/resume|cancel`）  
- L2：**批准并重试** / **拒绝**（`/api/ai/risk/approve|deny`，令牌写入 `plan.approvals`）  
- 状态：`GET /api/ai/cross-end/runs/<run_id>` · `GET /api/ai/ops/gates`  
- **Desktop 主路径**：一键「桌面主路径（记事本）」；**ERP 桌面样例**（Fake ERP 订单号断言）→ `GET /api/ai/cross-end/erp-desktop-plan`  
- 真机验收：`python demos/desktop_notepad_mainpath_accept.py` · `python demos/erp_desktop_sample/run_accept.py`  

## 运行历史（审计视角）

打开 `/run-history`：

- 列表：跨端类型徽章、CI `build` 徽章、一键「证据」导出  
- 详情：HITL/Risk 门禁摘要、audit / CI 链接、导出 Trace ZIP  

## 测试报告（治理看板）

打开 `/test-report`（或导航「测试报告」）：

- **概览/趋势/项目统计**已含无绑定用例的跨端运行；失败计入失败，不假绿  
- **治理与证据**卡片：跨端次数、HITL/Risk、门禁阻断、证据/CI 覆盖  
- **导出客户审计包**：按当前筛选下载 ZIP（`index` + `governance` + `auth_events` + 失败/门禁 Trace）  
- 近期门禁事件可跳转历史或导出证据  
- 筛选支持「跨端 / AgentTeams」  
