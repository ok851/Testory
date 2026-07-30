---
name: testory-android-mobile
description: Testory Android 移动端自动化：录制与执行在手机 APK 内完成；PC 负责配对/用例库/AI 代理（已绑定大模型）与跨端等待；adb JSON-RPC 仅安装与巡检。
version: 3.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: mobile
    tags: [android, adb, accessibility, plugin, sync, phone-only]
---

# Testory Android 移动端自动化

## 硬约束

- **录制、执行只在手机端**（AccessibilityService + 本机回放 + 悬浮条）
- PC **不参与**录制/回放过程（无 Live Recording、无逐步遥控正式路径）
- PC 角色：配对与用例库、**AI 推理代理**、结果归档、跨端 **await 手机本机跑完**

## 双通道

```
预览/巡检（可选）              业务主路径
adb → Gateway 8777 ──┐         手机 App ── LAN ──► Flask :5000
PluginHttpServer     │         录制/回放/AI意图      sync + AI profile
安装 / pageSource    │
（非正式执行引擎）    ┘
```

## 启动

```powershell
python app.py                          # Flask sync/AI（手机默认连此）
python -m mobile_automation_gateway    # 8777 安装/巡检（可选）
```

## Sync / AI API

| 端点 | 说明 |
|------|------|
| `POST /api/mobile/sync/pair/*` | 配对 |
| `POST /api/mobile/sync/cases/push` | 手机推送用例 |
| `GET /api/mobile/sync/ai/status` | PC 绑定模型就绪态（无密钥） |
| `POST /api/mobile/sync/ai/generate` | 用 **active LLM profile** 生成 Android 步骤 |
| `GET /api/mobile/sync/run/pending` | 手机拉取待办（本机执行） |
| `POST /api/mobile/sync/run/<job_id>/events` | 本机跑完上报 |

## Gateway（adb，调试用）

| 端点 | 说明 |
|------|------|
| `POST /internal/devices/connect` | 连接 + 可选插件隧道 |
| `POST /internal/plugin/install` | 安装 APK |
| `POST /internal/inspect/page-source` | 控件树巡检 |
| `POST /internal/recording/*` | **已废弃**（phone-only） |
| `POST /internal/replay/*` | **已废弃**（phone-only） |

## 跨端

mobile / android stage → 入队 sync run job → **等待**手机本机执行与 `stage_result` 上报 → 继续后续阶段。  
正式路径**不是** Gateway 逐步 `tap`。

APK `PcRunJobPoller`（无障碍服务启动后）：轮询 `extract_otp` → `run_steps`，本机执行后上报 events。

### Agent 口径（一脑多端双手）

- **同一个 Agent（大脑）**在 PC：工具循环 + 统一会话 `cross_end_vars`  
- **入口**：`/ai-test` 或手机 APK「Agent」模式（同一大脑，不是两套 Agent）  
- **双手**：已配对手机 → `mobile_*`；桌面可用 → `desktop_*` / `windows_*`  
- 禁止把 adb 逐步点当作正式引擎  

统一步骤 IR：[`docs/mobile_step_ir.md`](../../../docs/mobile_step_ir.md)。  
详见 [`docs/cross_end_agent_tools.md`](../../../docs/cross_end_agent_tools.md)。

## 排错

- **配对失败**：同网、Flask 端口、配对码 TTL
- **AI 失败**：PC 激活 LLM profile；查 `/api/mobile/sync/ai/status`
- **插件未就绪**：仅影响 adb 巡检；录跑仍在手机本地

文档：[`README_MOBILE.md`](../../../README_MOBILE.md)
