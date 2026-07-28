# MCP / 工具连接层契约（R12）

> 版本：2026-07-24  
> 上级：[semifinal_backlog.md](./semifinal_backlog.md)  
> 实现：`testory_mcp/` · 离线样例：`demos/goai-mcp-adapter/`

Skill 描述**能力**；MCP / 适配器描述**协议、鉴权、错误与审计**。  
Mock 与真机应共用同一工具 Schema，降低迁移成本。

---

## 1. 完成定义

| # | 门禁 | 验收 |
|---|------|------|
| M1 | 契约文档可独立阅读 | 本文 + 样例 README |
| M2 | 至少一端适配器可演示 | Desktop 工具列表 + 假端口调用 |
| M3 | 错误诚实 | 未知工具 / 无环境 → `error`，不得空成功 |
| M4 | 回归 | `tests/test_mcp_contract.py` 通过 |

---

## 2. 传输模式

| 模式 | 入口 | 说明 |
|------|------|------|
| **stdio 行 JSON** | `python -m testory_mcp.desktop` | 每行一个 JSON：`{"tool","params"}` → `{"result"}` 或 `{"error"}` |
| **Streamable HTTP** | `python -m testory_mcp.transport` / Flask blueprint | JSON-RPC 2.0：`initialize` / `tools/list` / `tools/call` / `ping` |

协议版本常量：`MCP_PROTOCOL_VERSION = "2024-11-05"`（见 `testory_mcp/transport.py`）。

### stdio 请求 / 响应

```json
{"tool": "windows_focus_app", "params": {"app_name": "记事本"}}
{"result": {"ok": true, "...": "..."}}
{"error": "unknown tool foo"}
```

### JSON-RPC tools/call（概念）

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {"name": "desktop_screenshot", "arguments": {}}
}
```

失败使用 JSON-RPC 错误码或 MCP 扩展码（`TOOL_NOT_FOUND=-32000` 等），**禁止** HTTP 200 + 空 body 当成功。

跨端编排结果另见 `ai_modules/execute/result_schema.py`（`testory.stage_result/v1`）：Mock Demo 与真机 `execute_cross_end_plan` 共用同一阶段字段（`ok_assert` / `error_code` / `error_message` / `extracted` / `warnings` / `evidence`）。

---

## 3. Desktop 工具集（摘要）

由 `testory_mcp.kit.mcp_windows_desktop_tools` + vision 工具组成。  
离线枚举：

```bash
python demos/goai-mcp-adapter/run_sample.py --list
```

| 风险默认 | 示例工具 |
|----------|----------|
| L0 | `get_screen_text` / `desktop_screenshot` |
| L1 | `windows_click_element` / `windows_type_text` / `desktop_tap` |
| L2 | 不在 MCP 默认暴露破坏性清数；跨端 L2 走 RiskGuard |

---

## 4. 鉴权与安全

- Desktop Gateway：`X-Desktop-Agent-Secret`（与 Flask 共用密钥）  
- MCP HTTP 若挂在主应用后，沿用平台登录 / 内网策略  
- 适配器**不得**绕过 RiskGuard：高风险动作仍应在编排层持令牌  

### 4.1 实连接探活（可选）

| 入口 | 说明 |
|------|------|
| `testory_mcp.gateway_live` | `probe_gateway_health` / `live_gateway_wait_step` |
| `python demos/goai-mcp-adapter/run_sample.py --live-gateway` | 不可达 → `health.ok=false` |
| `DESKTOP_FARM_GATEWAY=1` | 允许用农场在线节点补全 Gateway URL（仍需 SECRET） |

**诚实：** health 可达 / 单步 wait 成功 ≠ 业务用例通过。

---

## 5. 与 Skill / 编排关系

```text
Agent / Hermes
    → Skill（testory-windows-desktop）说明何时用何工具
    → MCP 适配器（本契约）真正连 UIA / 视觉端口
    → 跨端编排仍做 HITL / RiskGuard / Trace
```

---

## 6. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-24 | 首版契约；Desktop 离线样例与 pytest |
| 2026-07-27 | 实连接探活 + 农场 URL 回退（opt-in） |
