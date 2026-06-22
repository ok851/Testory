# Testory 移动端测试（Android）

本文说明如何在 Testory 平台中启用 Android 真机/模拟器 UI 自动化，以及 **scrcpy 投屏 + Plugin 录制** 双通道架构。

## 功能概览

- 新建用例时选择 **移动应用 (Android)**，在 **步骤编辑页** 维护步骤，在 **移动端测试页** 连接设备、投屏、录制与运行
- **双通道**：scrcpy WebSocket 高帧预览；Testory Assistant Plugin 结构化录制/回放/控件树
- **Playground**（Midscene 风格）：点一下 / 检查一下 / 读一下 / 帮我做 — 自然语言即时操作
- 视觉步骤回放：`ai_tap`、`assert_vision`、`wait_vision`、`extract_vision`
- Feature flag：`ENABLE_MOBILE=1`

## 架构（双通道）

```
┌─────────────────────────────────────────────────────────────┐
│  /mobile-testing  Mobile Studio                             │
│  ┌──────────────────┐  ┌────────────────────────────────┐ │
│  │ 预览通道 scrcpy   │  │ 执行通道 Agent + Plugin APK    │ │
│  │ H.264 WebSocket  │  │ 录制 / 回放 / 控件树 / VLM 点击 │ │
│  └────────┬─────────┘  └───────────────┬────────────────┘ │
└───────────┼────────────────────────────┼───────────────────┘
            │                            │
     mobile_scrcpy_bridge          mobile_automation_gateway
     (默认 ws://127.0.0.1:8767)   (默认 http://127.0.0.1:8777)
            │                            │
            └────────────┬───────────────┘
                         ▼
                   Android 设备 (adb)
```

| 通道 | 用途 | 技术 |
|------|------|------|
| **预览** | 实时看画面、点屏直觉 | scrcpy-server + WebCodecs / screencap 降级 |
| **录制** | 步骤、控件树、结构化 tap | Assistant APK + AccessibilityService |
| **Playground** | 即时 AI 操作 | `MobileVisionActionPort` + Ollama 视觉模型 |
| **回放** | 用例执行 | `replay.py`（含视觉步骤） |

## 1. 插件市场（推荐）

| 插件 | 用途 |
|------|------|
| **Android Platform-Tools (adb)** | USB/无线设备发现与操控 |
| **scrcpy 高帧率投屏** | 平台内 H.264 画布投屏（推荐） |

离线包说明见 [`plugin_bundles/README.md`](plugin_bundles/README.md)。未安装 scrcpy 完整包时，平台使用内置 `static/vendor/scrcpy-server`。

## 2. 前置条件

| 组件 | 说明 |
|------|------|
| Android Platform-Tools | `adb`（插件市场或 `ADB_PATH`） |
| Node.js 18+ | 可选，仅 Appium 模式需要 |
| USB 调试 | 手机开启开发者选项并授权 |

**手机端（录制必做）**

1. 开启 **USB 调试** 并授权本机。
2. 安装 **Testory Assistant** 并开启 **无障碍服务**（录制与 Plugin 回放）。

**无线调试（Android 11+）**：移动端测试页 → 填写 IP、配对码、端口 → **无线配对并连接**。

## 3. 启动服务

**Mobile Agent Gateway**（执行/录制，桌面版可自动拉起）：

```bash
python -m mobile_automation_gateway
```

**scrcpy 投屏桥**（连接设备时自动启动，也可单独调试）：

```bash
python -m mobile_scrcpy_bridge
```

## 4. 使用流程

1. **项目 → 用例管理** → 创建 **移动应用 (Android)** 用例（可归属 **单元**）
2. **步骤编辑** 维护步骤，或跳转 **移动端测试** 录制
3. **移动端测试** `/mobile-testing`：
   - 连接设备 → 中央画布 scrcpy 投屏
   - 右侧 **智能操作** Playground（四 Tab）
   - 操作后可 **查看回放**、**保存到当前用例**
4. **运行用例** → 结果写入运行历史与测试报告

## 5. Playground 四 Tab

| Tab | 说明 | API |
|-----|------|-----|
| 点一下 | 自然语言点击 | `POST /api/mobile/playground/tap` |
| 检查一下 | 画面断言 | `POST /api/mobile/playground/assert` |
| 读一下 | 从画面读取信息 | `POST /api/mobile/playground/query` |
| 帮我做 | 多步自动规划 | `POST /api/mobile/playground/act` |

保存步骤：`POST /api/mobile/playground/save-steps`（`run_id` + `case_id`，继承用例 `unit_id`）。

## 6. 页面分工

| 页面 | 职责 |
|------|------|
| `list_cases_v2` | 项目 / 单元 / 用例 |
| `list_steps` | 步骤维护（无投屏） |
| `mobile-testing` | 投屏、Playground、录制、运行 |
| `ai-test` | Web 测试（移动端镜像降级 screencap） |

## 7. 环境变量

```env
ENABLE_MOBILE=1
MOBILE_AGENT_GATEWAY_URL=http://127.0.0.1:8777
MOBILE_AGENT_GATEWAY_SECRET=your-secret

# 投屏（默认 auto：有 scrcpy 则用 scrcpy_ws，否则 screencap）
MOBILE_MIRROR_BACKEND=auto
SCRCPY_PATH=C:\Tools\scrcpy\scrcpy.exe
MOBILE_SCRCPY_BRIDGE_PORT=8767
MOBILE_SCRCPY_FPS=24

# 视觉 / Playground（默认开启，显式 0 关闭）
LOCATOR_TIER_VLM_ENABLE=1
VISION_STEP_REPORT_ENABLE=1
MOBILE_WAIT_AFTER_ACTION_MS=300
MOBILE_SCREENSHOT_SHRINK_FACTOR=2
MOBILE_PLAYGROUND_ACT_LIMIT=8
```

完整列表见 [`.env.example`](.env.example)。

## 8. 内部 CLI / MCP（开发者 / Hermes，不进主 UI）

```bash
python -m testory_cli mobile tap --udid emulator-5554 --locate "登录按钮"
python -m testory_cli mobile assert --condition "显示首页"
python -m testory_cli mobile query --prompt "当前用户 ID"
python -m testory_cli mobile act --goal "打开设置并进入 WLAN"
python -m testory_cli mobile run-steps --file steps.json

TESTORY_MCP_UDID=emulator-5554 python -m testory_mcp.mobile
```

MCP 工具：`android_screenshot`、`android_tap`、`android_input`、`android_assert`、`android_query`、`android_run_steps`。

## 9. Python 依赖

```bash
pip install -r requirements-mobile-optional.txt
# scrcpy WebSocket 桥另需 requirements.txt 中的 websockets>=12
```

## 10. 排错

| 现象 | 处理 |
|------|------|
| 画布黑屏 | 检查 `MOBILE_SCRCPY_BRIDGE_PORT`、adb 连接；降级为 screencap |
| 插件未就绪 | 开启 Assistant 无障碍；`POST /api/mobile/assistant/install` |
| VLM 点击失败 | 确认 Ollama 视觉模型；调低 `MOBILE_SCREENSHOT_SHRINK_FACTOR` |
| 回放视觉步骤失败 | 确认 Agent Gateway 可达；步骤 `automation_layer=android` |

Agent Skill：`.cursor/skills/testory-android-mobile/SKILL.md`
