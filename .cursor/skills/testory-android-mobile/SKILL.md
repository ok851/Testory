---
name: testory-android-mobile
description: Testory Android 移动端自动化：Mobile Agent + Recorder Plugin JSON-RPC，adb forward 结构化 dump/tap/scroll/录制。
version: 2.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: mobile
    tags: [android, adb, accessibility, plugin, agent]
---

# Testory Android 移动端自动化

## 架构

```
Web 平台 → Mobile Agent (TestoryMobileGw) → adb forward → Recorder Plugin APK
```

- **禁止** scrcpy / 视频流投屏
- 画面仅通过 **关键帧截图**（adb screencap 或插件 API）
- 录制：手机端物理操作 → AccessibilityService → Agent WebSocket 推送步骤

## 启动 Agent

桌面版自动拉起 `TestoryMobileGw`。开发调试：

```powershell
python -m mobile_automation_gateway
```

环境变量：`MOBILE_AGENT_GATE_PORT=8777`，`MOBILE_AGENT_GATEWAY_SECRET`

## Agent API

| 端点 | 说明 |
|------|------|
| `POST /internal/devices/connect` | 连接设备 |
| `POST /internal/plugin/install` | 安装 Recorder Plugin |
| `POST /internal/recording/start` | 开始录制 |
| `POST /internal/recording/stop` | 停止录制 |
| `WS /internal/events` | 实时 step / screenshot 事件 |
| `POST /internal/replay/run` | 回放用例步骤 |
| `POST /internal/inspect/page-source` | 控件树 |
| `POST /internal/inspect/screenshot` | 关键帧截图 |

## 插件 JSON-RPC（设备 localhost，经 adb forward）

`startRecording` / `stopRecording` / `pollSteps` / `getPageSource` / `takeScreenshot` / `tap` / `swipe` / `input`

## 平台集成

Flask 薄代理：`/api/mobile/*` → `mobile_agent_client.py`

- 连接：`POST /api/mobile/connect`
- 录制：`POST /api/mobile/arm`（start） / `disarm`（stop）
- 执行：`POST /api/mobile/run`

## 排错

- **插件未就绪**：在设备上开启 Testory Assistant 无障碍服务
- **Agent 未启动**：确认 `MOBILE_AGENT_GATEWAY_URL` 可达
- **5 秒断连**：重新开启无障碍或点「安装插件」重装
