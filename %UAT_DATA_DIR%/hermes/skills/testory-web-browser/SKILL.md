---
name: testory-web-browser
description: Testory 平台 Web 浏览器自动化：通过 embedded browser 画布 CDP attach 与 Hermes Agent 共享同一 Chromium，支持人机协作处理验证码/登录。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: web
    tags: [cdp, chromium, canvas, embedded-browser, captcha, human-in-loop]
---

# Testory Web 浏览器自动化

## 核心铁律（最高优先级）

1. **必须使用 Testory 画布 Chromium**：通过 `HERMES_BROWSER_MODE=cdp_attach` 连接平台 embedded browser gateway（默认 `:8765`）返回的 `cdp_browser_ws`。
2. **禁止** Agent 自带 headless 浏览器或临时拉起其他 Chromium/Playwright 实例，否则与用户画布画面不同步。
3. 遇到登录、滑块、验证码、扫码时：**暂停自动化**，提示用户在 Testory 实时画布中手动完成，完成后继续。

## Testory 架构

| 组件 | 说明 |
|------|------|
| Embedded Browser Gateway | [`browser_runtime/main.py`](../../browser_runtime/main.py)，端口 `8765` |
| CDP WebSocket | 会话创建后返回 `cdp_browser_ws` |
| CDP 同步 | `sync_hermes_cdp_endpoint()` 写入 `HERMES_HOME/.env` 的 `HERMES_CDP_ENDPOINT` |
| 探索入口 | `/api/ai/agent/gateway-stream`，意图 `hermes_explore` + `embedded_session_id` |
| 步骤执行 | `POST /internal/session/{id}/run-steps` 与 Hermes CDP **共用同一会话** |

## 使用前检查

```bash
# 确认 Hermes 已 attach CDP（环境变量或 .env）
echo $HERMES_CDP_ENDPOINT
# 或读取 HERMES_HOME/.env 中 HERMES_CDP_ENDPOINT

# 验证 CDP 可达
curl -s http://127.0.0.1:9222/json/version
# 注：实际端口以 cdp_browser_ws 为准，画布网关动态分配
```

## CDP 操作参考

连接时使用 Origin header 避免 403：

```python
import json, websocket, urllib.request

tabs = json.load(urllib.request.urlopen('http://127.0.0.1:PORT/json'))
page = next(t for t in tabs if t.get('type') == 'page')
ws = websocket.create_connection(
    page['webSocketDebuggerUrl'],
    header=['Origin: http://127.0.0.1:PORT'],
    timeout=20,
)
```

详见 `references/cdp-patterns.md`。

## 人机协作流程

1. Agent 执行 navigate/click/input 直到遇到验证障碍
2. 向用户说明：「请在 Testory 左侧/画布浏览器中完成验证，完成后回复继续」
3. 用户手动操作后，Agent 通过 CDP 读取当前 URL/标题确认状态
4. 继续后续步骤

## 截图确认

优先使用 CDP `Page.captureScreenshot`；或通过平台画布 WebSocket 已有 screencast 帧。

## 不适用场景

- 纯 API 任务（无需浏览器）
- 未连接画布且未配置 CDP 时强行浏览器自动化（应提示用户先打开 AI 测试画布）

## 维护

- 用例步骤可导出为 Skill：`POST /api/ai/skills/export-from-plan`
- UI 变更自愈：`POST /api/ai/skills/update`
