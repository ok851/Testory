# Testory 移动端测试（Android / Appium）

本文说明如何在 Testory 平台中启用 Android 真机 UI 自动化测试。

## 功能概览

- 新建用例时选择 **移动应用 (Android)**，在 **步骤编辑页** 维护步骤，在 **移动端测试页** 连接设备、投屏、录制与运行
- 通过 `automation_layer=android` 执行 Appium 步骤
- USB / 无线 ADB 连接、scrcpy 高帧画布投屏、点屏录制、元素探测器与元素库
- Feature flag：`ENABLE_MOBILE=1`

## 1. 插件市场（推荐）

在插件市场安装：

| 插件 | 用途 |
|------|------|
| **Android Platform-Tools (adb)** | USB/无线设备发现与操控 |
| **scrcpy 高帧率投屏** | 平台内高帧率 H.264 画布投屏（推荐） |

离线包说明见 [`plugin_bundles/README.md`](plugin_bundles/README.md)。

## 2. 前置条件

| 组件 | 说明 |
|------|------|
| Android Platform-Tools | 提供 `adb`（插件市场安装或配置 `ADB_PATH`） |
| Node.js 18+ | 安装 Appium Server |
| USB 调试 | 手机开启开发者选项，连接电脑并授权 |

**手机端（必做）**

1. 设置 → 关于手机 → 连续点「版本号」7 次 → 开启 **开发者选项** → 打开 **USB 调试**。
2. USB 连接 PC 后，在弹窗点 **允许此计算机调试**（可勾选始终允许）。
3. 通知栏 USB 模式选 **文件传输 (MTP)** 或 **PTP**，不要仅充电。

**无线调试（Android 11+）**

1. 开发者选项 → **无线调试** → 使用配对码配对设备。
2. 在移动端测试页填写手机 IP、配对端口、6 位配对码与调试端口，点 **无线配对并连接**。

## 3. 安装 Appium 2.x

```bash
npm install -g appium
appium driver install uiautomator2
appium --address 127.0.0.1 --port 4723
```

验证：

```bash
curl http://127.0.0.1:4723/status
```

## 4. 使用流程

1. **项目 → 用例管理** → 创建用例，测试类型选 **移动应用 (Android)**
2. **步骤编辑**（`list_steps`）维护关键字步骤，或点 **移动端测试** 跳转录制
3. **移动端测试**（`/mobile-testing`）→ 连接设备 → 点屏录制 / 运行用例
4. 执行结果写入与 Web 相同的 **运行历史** 与 **测试报告**

## 5. 页面分工

| 页面 | 职责 |
|------|------|
| `list_cases_v2` | 创建移动应用用例 |
| `list_steps` | 步骤维护、查看、关键字编辑（无投屏） |
| `mobile-testing` | 设备连接、投屏、录制、取点、运行调试 |

## 6. Python 依赖

```bash
pip install -r requirements-mobile-optional.txt
```

## 7. 环境变量（可选）

```env
ENABLE_MOBILE=1
ADB_PATH=C:\path\to\adb.exe
APPIUM_SERVER_URL=http://127.0.0.1:4723
MOBILE_MIRROR_BACKEND=auto
MOBILE_DEVICE_SCRCPY=1
```
