---
name: testory-web-browser
description: Testory 平台 Web 浏览器自动化：启动用户本机 Edge/Chrome（CDP），与 Hermes Agent 共享同一浏览器，支持人机协作处理验证码/登录。
version: 1.1.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: web
    risk_default: L1
    tags: [cdp, local-browser, edge, chrome, hermes, captcha, human-in-loop]
---

# Testory Web 浏览器自动化

## 核心铁律（最高优先级）

1. **必须使用用户本机浏览器**：通过 `web_capture.cdp_browser.launch_debug_browser` 启动 Edge/Chrome（remote debugging），再 `HERMES_BROWSER_MODE=cdp_attach` 连接 `HERMES_CDP_ENDPOINT`。
2. **启动方式**：浏览器进程只开 `about:blank` 单标签；业务 URL 用 `page.goto` 在该标签内导航。**禁止**把业务 URL 放进启动命令行（否则 Edge 会「新建标签页」+ 目标页双开）。
3. **禁止**依赖已废弃的内嵌画布 Chromium / Browser Runtime 画布会话。
4. **禁止** Agent 另起独立 headless 浏览器（除非显式开启 `AI_ALLOW_PLAYWRIGHT_CHROMIUM_FALLBACK`）。
5. 遇到登录、滑块、验证码、扫码时：**暂停自动化**，提示用户在本机浏览器窗口中手动完成，完成后继续。
6. **禁止**用 `terminal`/`curl` 探测 CDP；平台已打开目标页时**禁止**反复 `browser_navigate`。
7. **DOM 优先**：用页面可交互控件/DOM 结构定位；`browser_snapshot` 是无障碍树/DOM ref（不是视觉截图），仅难定位时兜底一次；视觉截图仅最终兜底。
8. 浏览器以 `--start-maximized` 启动，并尽量 CDP/Win32 最大化窗口。
9. Hermes API Server 工具集须为 `platform_toolsets.api_server: [browser, web, memory]`（**不含** skills/terminal，否则会 skill_view 死循环）。

## 输入 / 输出 Schema

### 阶段输入（跨端 `layer=web`）

| 字段 | 说明 |
|------|------|
| `actions[]` | `navigate` / `click` / `fill` / `assert_text` 等 |
| `vars_to_store` | 从 selector/DOM 抽取写入上下文 |
| `hitl` / 预门禁 | 验证码等可先走 HitlGate |

### 阶段输出

| 字段 | 说明 |
|------|------|
| `ok_assert` | 仅步骤与断言真实通过时为 true |
| `error_code` | 如 `EMPTY_SELECTOR`、`NO_BROWSER_PAGE`、`ASSERT_TEXT_MISMATCH` |
| `extracted` | 变量字典 |
| `screenshot_path` | 可选证据 |

MCP / Agent 侧优先 `testory_mcp.web` 或平台 CDP attach；契约见 `docs/goai/MCP_CONTRACT.md`。

## 失败处理（诚实）

| 情况 | 结果 |
|------|------|
| 无 browser page / CDP 断开 | 阶段失败，不得 warning 后当绿 |
| 空 selector | `EMPTY_SELECTOR`，忽略 allow_skip |
| 断言文案不匹配 | `ASSERT_TEXT_MISMATCH`，挡总成功 |
| HITL 超时/取消 | `HITL_TIMEOUT` / `HITL_CANCELLED` |
| 超时未找到元素 | 失败并保留截图（若有） |

禁止：用散文「看起来成功了」或 Hermes 无 `[RESULT] ok` 默认成功。

## 安全边界

- 默认 **L1**；涉及清 Cookie/改生产配置等应升 **L2** 并走 RiskGuard。  
- 验证码/登录必须 **HITL**，禁止自动撞库或绕过。  
- 截图与 DOM 可能含 PII：报告/Trace 按平台脱敏策略处理。  
- 不在 Skill 内硬编码账号密码；使用密钥或人工输入。

## Testory 架构

| 组件 | 说明 |
|------|------|
| 本机浏览器 | Edge/Chrome + `--remote-debugging-port`（`web_capture/cdp_browser.py`） |
| CDP WebSocket | `launch_debug_browser` / `fetch_cdp_ws` 返回 |
| CDP 同步 | `sync_hermes_cdp_endpoint()` 写入 `HERMES_HOME/.env` |
| AI 执行入口 | `/api/ai/task/execute`，经 `ai_external_browser_bridge.ensure_browser` |

## 人机协作

1. Agent 执行到验证障碍时暂停
2. 提示用户在本机已打开的浏览器中完成验证
3. CDP 确认 URL/标题后继续

## 不适用

- 纯 API / 纯桌面任务
- 未安装 Edge/Chrome 且未允许 Playwright Chromium 兜底
