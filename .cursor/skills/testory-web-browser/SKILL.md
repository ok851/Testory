---
name: testory-web-browser
description: Testory 平台 Web 浏览器自动化：启动用户本机 Edge/Chrome（CDP），与 Hermes Agent 共享同一浏览器，支持人机协作处理验证码/登录。
version: 1.1.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: web
    tags: [cdp, local-browser, edge, chrome, hermes, captcha, human-in-loop]
---

# Testory Web 浏览器自动化

## 核心铁律（最高优先级）

1. **必须使用用户本机浏览器**：通过 `web_capture.cdp_browser.launch_debug_browser` 启动 Edge/Chrome（remote debugging），再 `HERMES_BROWSER_MODE=cdp_attach` 连接 `HERMES_CDP_ENDPOINT`。
2. **启动方式**：浏览器进程只开 `about:blank` 单标签；业务 URL 用 `page.goto` 在该标签内导航。**禁止**把业务 URL 放进启动命令行（否则 Edge 会「新建标签页」+ 目标页双开）。
3. **禁止**依赖已废弃的内嵌画布 Chromium / Browser Runtime 画布会话。
4. **禁止** Agent 另起独立 headless 浏览器（除非显式开启 `AI_ALLOW_PLAYWRIGHT_CHROMIUM_FALLBACK`）。
5. 遇到**图形验证码 / 滑块 / 扫码登录**时：暂停自动化并说明原因（不要假装已完成）。**短信验证码**若已通过 `mobile_extract_otp` 取得，必须用 `browser_type` / `browser_console` 自动回填，禁止请用户手填、禁止刷新页面。
6. **禁止**用 `terminal`/`curl` 探测 CDP；平台已打开目标页时**禁止**反复 `browser_navigate`。
7. **DOM 优先**：用页面可交互控件/DOM 结构定位；`browser_snapshot` 是无障碍树/DOM ref（不是视觉截图），仅难定位时兜底一次；视觉截图仅最终兜底。
8. 浏览器以 `--start-maximized` 启动，并尽量 CDP/Win32 最大化窗口。
9. Hermes API Server 工具集须为 `platform_toolsets.api_server: [browser, web, memory]`（**不含** skills/terminal，否则会 skill_view 死循环）。

## Testory 架构

| 组件 | 说明 |
|------|------|
| 本机浏览器 | Edge/Chrome + `--remote-debugging-port`（`web_capture/cdp_browser.py`） |
| CDP WebSocket | `launch_debug_browser` / `fetch_cdp_ws` 返回 |
| CDP 同步 | `sync_hermes_cdp_endpoint()` 写入 `HERMES_HOME/.env` |
| AI 执行入口 | `/api/ai/task/execute`，经 `ai_external_browser_bridge.ensure_browser` |

## 使用前检查

```bash
# 确认 Hermes 已 attach CDP
# 读取 HERMES_HOME/.env 中 HERMES_CDP_ENDPOINT

# 验证本机调试端口可达（端口以 launch 返回为准）
curl -s http://127.0.0.1:9222/json/version
```

## 人机协作流程

1. Agent 执行 navigate/click/input 直到遇到**无法自动处理**的障碍（图形验证码/滑块/扫码）
2. 向用户说明真实错误原因；短信 OTP 已取到时不得要求用户手填，应继续 browser_* 回填
3. 用户完成图形验证后，Agent 通过 CDP 读取当前 URL/标题确认状态
4. 继续后续步骤

## 不适用场景

- 纯 API / 纯桌面任务（无需浏览器）
- 未安装 Edge/Chrome 且未允许 Playwright Chromium 兜底

## 维护

- 用例步骤可导出为 Skill：`POST /api/ai/skills/export-from-plan`
- UI 变更自愈：`POST /api/ai/skills/update`
