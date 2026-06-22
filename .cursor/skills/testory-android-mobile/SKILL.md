---
name: testory-android-mobile
description: Testory Android 移动端自动化：scrcpy 预览 + Mobile Agent + Recorder Plugin 双通道，Playground 四 Tab，adb forward 结构化 dump/tap/scroll/录制。
version: 2.1.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: mobile
    tags: [android, adb, accessibility, plugin, agent, scrcpy, playground]
---

# Testory Android 移动端自动化

## 双通道架构

```
预览（看）          执行（做）
scrcpy_ws ─────┐    Agent Gateway 8777 ──► Plugin APK (JSON-RPC)
H.264 WebSocket│    录制 / 回放 / 控件树 / VLM tap
8767           │    Playground ──► MobileVisionActionPort
               └──► 同一 adb 设备
```

- **预览**：scrcpy 高帧画布（`mobile_scrcpy_bridge` + `static/js/mobile_scrcpy_mirror.js`）
- **录制**：手机端物理操作 → AccessibilityService → Agent WebSocket 推送步骤
- **Playground**：右侧智能操作（点一下/检查一下/读一下/帮我做）→ `/api/mobile/playground/*`
- **禁止**在主 UI 暴露 JSON/CLI/MCP；CLI/MCP 仅 Hermes / 开发者

## 启动

```powershell
python -m mobile_automation_gateway    # 8777 执行/录制
python -m mobile_scrcpy_bridge         # 8767 投屏（连接设备时可自动拉起）
```

环境变量：`MOBILE_AGENT_GATEWAY_URL`、`MOBILE_AGENT_GATEWAY_SECRET`、`MOBILE_SCRCPY_BRIDGE_PORT=8767`

## Agent API

| 端点 | 说明 |
|------|------|
| `POST /internal/devices/connect` | 连接设备 |
| `POST /internal/plugin/install` | 安装 Recorder Plugin |
| `POST /internal/recording/start` \| `stop` | 录制 |
| `WS /internal/events` | 实时 step / screenshot |
| `POST /internal/replay/run` | 回放（含 `ai_tap` / `assert_vision` / `wait_vision` / `extract_vision`） |
| `POST /internal/inspect/page-source` | 控件树 |
| `POST /internal/inspect/screenshot` | 截图 |

## Flask `/api/mobile/*`

| 路由 | 说明 |
|------|------|
| `POST /api/mobile/connect` | 返回 `mirror_ws_url`、`mirror_stream_url` |
| `POST /api/mobile/mirror/start` \| `stop` \| `GET status` | 投屏生命周期 |
| `POST /api/mobile/arm` \| `disarm` | 录制 |
| `POST /api/mobile/run` | 运行用例 |
| `POST /api/mobile/playground/{tap,assert,query,act}` | Playground |
| `POST /api/mobile/playground/save-steps` | 回放保存到用例（含 unit_id） |

## 插件 JSON-RPC（adb forward）

`startRecording` / `stopRecording` / `pollSteps` / `getPageSource` / `takeScreenshot` / `tap` / `swipe` / `input`

## 视觉默认参数

| 变量 | 默认 | 说明 |
|------|------|------|
| `MOBILE_WAIT_AFTER_ACTION_MS` | 300 | Tap/Act 后等待 |
| `MOBILE_SCREENSHOT_SHRINK_FACTOR` | 2 | VLM 截图缩小 |
| `MOBILE_PLAYGROUND_ACT_LIMIT` | 8 | Act 最大步数 |
| `VISION_STEP_REPORT_ENABLE` | 1 | HTML 回放 |

## 内部 CLI / MCP

```powershell
python -m testory_cli mobile tap --udid emulator-5554 --locate "登录"
python -m testory_cli mobile query --prompt "当前页面标题"
TESTORY_MCP_UDID=emulator-5554 python -m testory_mcp.mobile
```

MCP：`android_screenshot` / `android_tap` / `android_input` / `android_assert` / `android_query` / `android_run_steps`

## 排错

- **画布黑屏**：检查 scrcpy 桥 8767、SCRCPY_PATH；降级 screencap
- **插件未就绪**：开启 Testory Assistant 无障碍
- **Agent 未启动**：`MOBILE_AGENT_GATEWAY_URL` 可达性
- **VLM 失败**：Ollama 视觉模型、`LOCATOR_TIER_VLM_ENABLE=1`

文档：[`README_MOBILE.md`](../../../README_MOBILE.md)
