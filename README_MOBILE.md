# Testory 移动端测试（Android）

> **产品硬约束：** 录制与执行只在手机 Testory 助手 App 内完成；PC **不参与**手机端录制/回放过程。  
> PC 负责：配对与用例库、AI 推理代理（复用已绑定大模型）、产物归档、跨端编排中**等待手机本机跑完**后继续。

## 功能概览

- 手机安装 **Testory Assistant v2**，开启无障碍后在手机上录制 / 回放
- 与 PC 同网配对后：推拉用例、AI 生成步骤（走 PC 当前激活 LLM）、上报运行结果
- PC **移动端测试页** `/mobile-testing`：配对、安装助手、查看已同步步骤（**不是** PC 遥控录制台）
- Feature flag：`ENABLE_MOBILE=1`

## 架构（双通道，职责分离）

```
┌──────────────────────────────┐          Wi‑Fi LAN HTTP           ┌─────────────────────────┐
│  Testory Assistant APK       │ ─────────────────────────────────► │ Flask :5000             │
│  录制 / 本机回放 / 悬浮条     │  /api/mobile/sync/*               │ 配对 · 用例库 · AI 代理  │
│  OkHttpPcSyncClient          │ ◄───────────────────────────────── │ get_active_llm_profile  │
│  AccessibilityService        │                                    └───────────┬─────────────┘
│  PluginHttpServer (本机)     │ ◄── adb forward JSON-RPC ────────── Agent Gateway :8777      │
└──────────────────────────────┘     （安装 / 巡检 / 调试 ONLY）                 │
```

| 通道 | 用途 | 禁止 |
|------|------|------|
| **LAN sync**（Flask 默认 5000） | 配对、推拉用例、AI 生成、跑完上报 | 逐步遥控录制/回放 |
| **adb JSON-RPC**（Gateway 8777，可选） | 安装助手、连通性、控件树/截图巡检、调试 | 正式录制引擎或正式回放引擎；**PC「连接设备」不依赖 Gateway** |

跨端联动中的 mobile 阶段：PC **挂起等待**手机本机执行并上报 `stage_result`，不经 adb 逐步点手机。

> Gateway 不是日常配对/录制/回放路径。只有需要 PC 侧 adb 安装助手或插件巡检时才启动 `python -m mobile_automation_gateway`。

## 跨端 Agent（大脑 / 双手）

> **口径：** Agent 在 PC 上思考；桌面 UIA / 浏览器 CDP / 手机 APK 是执行器（双手）。一次会话可交替调用两端工具；验证码只是样例场景。

| 角色 | 实现 |
|------|------|
| 大脑 | Hermes / AI 自主测试工具循环 |
| 桌面双手 | `desktop_*`（别名）→ `windows_*` UIA |
| 手机双手 | `mobile_extract_otp` / `mobile_run_steps` → sync enqueue + **本机** await |

工具契约与验收剧本：[`docs/cross_end_agent_tools.md`](docs/cross_end_agent_tools.md)、[`demos/cross_end/desktop_mobile_otp_plan.json`](demos/cross_end/desktop_mobile_otp_plan.json)。

CI 无真机时可设 `MOBILE_OTP_MOCK=123456`。

## 1. 前置条件

| 组件 | 说明 |
|------|------|
| Android Platform-Tools | `adb`（插件市场或 `ADB_PATH`），用于安装助手与可选巡检 |
| USB 调试 | 安装/更新 APK、调试隧道时需要 |
| 同网 Wi‑Fi | 手机与 PC 配对、同步、AI 推理必需 |

**手机端（录制 / 执行必做）**

1. 安装 **Testory Assistant**，开启 **无障碍服务**
2. （推荐）授予悬浮窗权限，便于录制/回放控制条
3. 在 App 内用 PC 配对码连接（或填写 LAN IP + 端口）

## 2. 启动服务

**Flask 平台**（手机 sync / AI 目标，默认 5000）：

```bash
python app.py
```

**Mobile Agent Gateway**（可选：adb 安装/巡检，默认 8777；桌面版可自动拉起）：

```bash
python -m mobile_automation_gateway
```

> 说明：历史文档中的 `mobile_scrcpy_bridge` / Playground / PC 侧 Live Recording 已退役；投屏仅作可选「看」，不作「控」。

## 3. 推荐使用流程

1. PC **移动端测试** → 生成配对码 / 查看 LAN 地址 → 安装助手（可选 adb）
2. 手机 App → 输入配对码 → 确认无障碍已开
3. 手机 **开始录制** → 操作被测 App → 停止 → 同步到 PC
4. 或手机 **AI 助手** → 描述需求 → PC 用**已绑定大模型**生成步骤 → 保存后**本机回放**
5. 跨端场景：PC 编排到 mobile 阶段后等待；手机完成本机运行并上报后，PC 继续后续阶段

## 4. 页面分工

| 页面 | 职责 |
|------|------|
| 手机 Assistant App | **唯一**录制与执行场所；AI 意图入口；同步 |
| `/mobile-testing` | 配对、安装、同步用例/步骤管理 |
| `list_steps` | PC 上编辑已同步的步骤（无投屏遥控） |
| 跨端页 | 编排；mobile stage = await 手机结果 |

## 5. AI（手机 → PC 代理）

- 接口：`POST /api/mobile/sync/ai/generate`（设备 token）
- 推理：PC `get_active_llm_profile()` + `generate_case_and_steps(..., platform_type="android")`
- 状态：`GET /api/mobile/sync/ai/status`（provider / model / ready，无密钥）
- **不**把 API Key 下发到手机；手机不直连第三方大模型

## 6. 环境变量

```env
ENABLE_MOBILE=1
MOBILE_AGENT_GATEWAY_URL=http://127.0.0.1:8777
MOBILE_AGENT_GATEWAY_SECRET=your-secret
# 跨端等待手机本机跑完的超时（秒）
MOBILE_DEVICE_AWAIT_TIMEOUT_SEC=600
```

手机 sync 默认连 PC Flask 端口（常见 5000），不是 8777。

## 7. 排错

| 现象 | 处理 |
|------|------|
| 手机无法配对 | 同网、防火墙放行 Flask 端口；PC 打开 `/mobile-testing` 取 LAN 与配对码 |
| AI 失败 | PC 先绑定并激活大模型；看 `GET /api/mobile/sync/ai/status` |
| 录制无步骤 | 确认无障碍已开；录制与执行只在手机完成 |
| 插件隧道失败 | 仅影响 adb 巡检/安装；不影响手机本地录跑与 LAN sync |

相关 Skill：`.cursor/skills/testory-android-mobile/SKILL.md`
