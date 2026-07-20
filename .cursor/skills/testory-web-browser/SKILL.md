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
2. **禁止**依赖已废弃的内嵌画布 Chromium / Browser Runtime 画布会话。
3. **禁止** Agent 另起独立 headless 浏览器（除非显式开启 `AI_ALLOW_PLAYWRIGHT_CHROMIUM_FALLBACK`）。
4. 遇到登录、滑块、验证码、扫码时：**暂停自动化**，提示用户在本机浏览器窗口中手动完成，完成后继续。

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

1. Agent 执行 navigate/click/input 直到遇到验证障碍
2. 向用户说明：请在本机已打开的浏览器窗口中完成验证，完成后回复继续
3. 用户手动操作后，Agent 通过 CDP 读取当前 URL/标题确认状态
4. 继续后续步骤

## 不适用场景

- 纯 API / 纯桌面任务（无需浏览器）
- 未安装 Edge/Chrome 且未允许 Playwright Chromium 兜底

## 维护

- 用例步骤可导出为 Skill：`POST /api/ai/skills/export-from-plan`
- UI 变更自愈：`POST /api/ai/skills/update`
