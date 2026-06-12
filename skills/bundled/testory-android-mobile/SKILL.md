---
name: testory-android-mobile
description: Testory Android 移动端自动化：Appium bridge_daemon 结构化 dump/tap/scroll，含 UC WebView 虚拟列表与 uiautomator2 降级策略。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: mobile
    tags: [android, appium, adb, uiautomator2, webview, bridge]
---

# Testory Android 移动端自动化

## 双后端策略

| 后端 | 适用 | 限制 |
|------|------|------|
| **bridge_daemon** (Appium) | 原生控件 dump/find/wait | UC WebView 虚拟列表 collapsed 项不可点 |
| **uiautomator2** | WebView 滚动、预订/提交按钮 | 与 Appium **不可并行**（AccessibilityService 冲突） |
| **ADB input** | 坐标点击兜底 | UC WebView 内容区触摸可能被过滤 |

## 脚本路径

Bundled 脚本位于 Hermes skills 目录：

```
HERMES_HOME/skills/testory-android-mobile/scripts/
  bridge_daemon.py
  start_bridge.sh   # Linux/macOS
```

Windows 下直接：

```powershell
python skills\bundled\testory-android-mobile\scripts\bridge_daemon.py dump
```

或通过平台 API：`POST /api/mobile/bridge/dump`

## Quick Start

```bash
# 一次性启动 daemon（约 28s 预热）
bash HERMES_HOME/skills/testory-android-mobile/scripts/start_bridge.sh

# 交互命令（1-2s/次）
python3 bridge_daemon.py dump
python3 bridge_daemon.py tap '{"text": "查询"}'
python3 bridge_daemon.py scroll '{"direction": "down"}'
python3 bridge_daemon.py wait '{"text": "提交订单", "timeout": 30}'
```

## bridge 命令

| 命令 | 说明 |
|------|------|
| `dump` | 结构化屏幕 JSON（buttons/trains/alerts） |
| `tap` | 按 text/id 点击 |
| `tap_bounds` | 按 bounds 点击（h>20px 才可靠） |
| `tap_coords` | 坐标点击 |
| `scroll` | 方向滑动（UC WebView 可能无效） |
| `type` | 输入文本 |
| `wait` | 等待元素出现 |
| `screenshot` | 截图路径 |

## UC WebView 虚拟列表（关键）

12306、支付宝 Nebula 等 UC WebView：

- **可见项**：bounds 高度 60-130px，可点击
- **屏外项**：collapsed 至 h=6px，**不可**通过 accessibility 点击
- **操作按钮**（「预订」「提交订单」）：通常有正常 bounds

**滚动**：Appium `scroll` / `mobile: scrollGesture` 在 UC WebView 常失败 → 用 uiautomator2：

```python
import uiautomator2 as u2
d = u2.connect()
d.swipe_ext('up', scale=0.3)
d(text='预订').click()
```

Testory `MobileExecutor` 在 Appium swipe 失败时会自动尝试 uiautomator2 降级。

## 与 MobileExecutor 冲突

平台 `MobileExecutor` 持有 Appium 会话时，**不要**同时启动 bridge_daemon。API 会返回 `409 bridge_conflict`。

## 平台集成

- 设备连接：`POST /api/mobile/connect`
- 用例执行：`POST /api/mobile/run`
- Bridge dump：`POST /api/mobile/bridge/dump`
- Bridge 命令：`POST /api/mobile/bridge/{action}` body `{"args": {...}}`

## 排错

- **Daemon 超时**：重新 `start_bridge.sh`
- **AccessibilityService 冲突**：断开 MobileExecutor 或 quit bridge
- **G7004 搜不到**：UC WebView 文本带空格 `G 7 0 0 4`
