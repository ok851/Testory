# Testory 移动端测试（Android / Appium）

本文说明如何在 Testory 平台中启用 Android 真机 UI 自动化测试。

## 功能概览

- 通过 `automation_layer=android` 执行 Appium 步骤
- USB 设备发现、Appium 连接、scrcpy 投屏 + 平台内 canvas 预览
- AI 测试页可选择 **Web / Android** 生成对应用例
- Feature flag：`ENABLE_MOBILE=1`

## 1. 插件市场安装 adb（推荐）

无需单独配置 Android Studio。在平台 **用户菜单 → 插件市场** 安装 **「Android Platform-Tools (adb)」**：

| 方式 | 操作 |
|------|------|
| **公网** | 直接点安装（默认从 Google 官方地址下载） |
| **离线** | 将 `platform-tools-latest-windows.zip` 重命名为 `platform-tools-windows.zip`，放到项目 `plugin_bundles/` 目录后再点安装 |
| **内网 URL** | `.env` 设置 `ANDROID_PLATFORM_TOOLS_URL=https://你们的CDN/...zip` 或改 `config/plugin_bundles/android_platform_tools.json` |

详细说明见 [`plugin_bundles/README.md`](plugin_bundles/README.md)。安装后重启 `python app.py`，打开 **移动端测试** 页连接手机。

## 2. 前置条件

| 组件 | 说明 |
|------|------|
| JDK 11+ | Appium 2.x 需要 |
| Android SDK Platform-Tools | 提供 `adb`，加入 PATH 或配置 `ADB_PATH` |
| Node.js 18+ | 安装 Appium Server |
| USB 调试 | 手机开启开发者选项，连接电脑并授权 |

**手机端（必做）**

1. 设置 → 关于手机 → 连续点「版本号」7 次 → 开启 **开发者选项** → 打开 **USB 调试**。
2. USB 连接 PC 后，在弹窗点 **允许此计算机调试**（可勾选始终允许）。
3. 通知栏 USB 模式选 **文件传输 (MTP)** 或 **PTP**，不要仅充电。

**PC 端**

- 安装 [Platform-Tools](https://developer.android.com/tools/releases/platform-tools)，终端执行 `adb devices -l`，状态须为 `device`（不是 `unauthorized` / `offline`）。
- `.env` 中 `ADB_PATH` 建议写 `adb.exe` **完整路径**（未加入系统 PATH 时）；改 `.env` 后须 **重启** `python app.py`。
- Windows：设备管理器若有叹号 Android 设备，安装厂商 USB 驱动。

环境变量（推荐）：

```bash
ANDROID_HOME=C:\Users\<you>\AppData\Local\Android\Sdk
PATH=%PATH%;%ANDROID_HOME%\platform-tools
```

## 3. 安装 Appium 2.x

```bash
npm install -g appium
appium driver install uiautomator2
appium --address 127.0.0.1 --port 4723
```

验证：

```bash
curl http://127.0.0.1:4723/status
adb devices -l
```

## 4. 安装 scrcpy（投屏）

Windows：从 [scrcpy releases](https://github.com/Genymobile/scrcpy/releases) 下载，解压后将目录加入 PATH，或在 `.env` 中设置：

```bash
SCRCPY_PATH=C:\Tools\scrcpy\scrcpy.exe
```

连接设备后，平台会启动 scrcpy 独立窗口，并在 AI 测试页 canvas 中通过 adb 截图同步预览。

## 5. 安装 Python 依赖

```bash
pip install -r requirements-mobile-optional.txt
```

## 6. 平台配置（.env）

```bash
ENABLE_MOBILE=1
APPIUM_SERVER_URL=http://127.0.0.1:4723
ANDROID_DEVICE_NAME=Android
ANDROID_APP_PACKAGE=com.example.app
ANDROID_APP_ACTIVITY=.MainActivity
SCRCPY_PATH=scrcpy
ADB_PATH=adb
MOBILE_MIRROR_FPS=8
```

也可在 **设置 → 移动端配置** 中保存默认值到 `data/client_config.json`。

## 7. 使用流程（推荐：移动端测试）

1. `.env` 设置 `ENABLE_MOBILE=1`，安装可选依赖，重启平台
2. USB 连接手机并授权调试
3. 打开导航栏 **「移动端」** → [`/mobile-testing`](/mobile-testing)
4. 点击 **「一键连接」**（自动识别设备、分辨率、前台应用；启动 scrcpy + 画布投屏）
5. 左侧选择项目/用例
6. **点屏录制**（左侧「点屏录制」区）：
   - **操控**：画布单击/拖拽直接操作真机（ADB）
   - **元素**：点屏自动追加 `tap` 步骤（adb uiautomator 解析 resource-id / content-desc / text）
   - **图像**：点屏自动追加 `tap_image` 步骤（OpenCV 模板，Airtest 风格，需 `opencv-python`）
7. 可用 **AI 生成** 或 **完整编辑** 维护步骤；**运行用例** 走 Appium（需 `appium` 在线）

传统入口仍可用：**AI 测试** 页平台选择 Android、步骤编辑页自动化层。

### 驱动说明（少配置）

| 能力 | 默认实现 | 说明 |
|------|----------|------|
| 投屏 / 手动点击滑动 | **ADB** | 无需 Appium |
| 点屏录元素 | **uiautomator dump** | `POST /api/mobile/record-step` |
| 图像识别步骤 | **OpenCV 模板** | `tap_image` / `wait_image` / `assert_image` |
| 自动化步骤执行 | **Appium + UiAutomator2** | 运行用例前 `appium` 需在线 |
| 可选增强 | **uiautomator2** | `pip install uiautomator2`，点击更稳 |

环境变量 `MOBILE_DRIVER=auto`（默认）：投屏与手动操作用 ADB，执行步骤时尝试 Appium。

## 8. 步骤 JSON 示例

```json
{
  "action": "open_app",
  "automation_layer": "android",
  "input_value": "com.example.app",
  "mobile_spec": {"appPackage": "com.example.app", "appActivity": ".MainActivity"},
  "description": "启动被测应用"
}
```

```json
{
  "action": "tap",
  "automation_layer": "android",
  "strategy": "accessibility_id",
  "selector_type": "accessibility_id",
  "selector_value": "login_button",
  "description": "点击登录按钮"
}
```

坐标点击（点屏录制「元素」失败时的兜底，或手动写入）：

```json
{
  "action": "tap",
  "automation_layer": "android",
  "selector_type": "viewport_coord",
  "selector_value": "540,1200",
  "mobile_spec": {"tap_x": 540, "tap_y": 1200},
  "description": "坐标点击"
}
```

图像识别点击（点屏录制「图像」）：

```json
{
  "action": "tap_image",
  "automation_layer": "android",
  "selector_type": "visual_template",
  "selector_value": "{\"png_b64\":\"...\",\"threshold\":0.72,\"anchor_x\":540,\"anchor_y\":1200}",
  "description": "图像识别点击"
}
```

### 支持的动作

`open_app`, `close_app`, `tap`, `input_text`, `swipe`, `wait`, `assert_text`, `assert_element`, `screenshot`

Web 别名自动映射：`click`→`tap`，`input`→`input_text`。

### 定位策略（strategy）

`accessibility_id`（默认）, `id`, `xpath`, `class_name`, `android_uiautomator`

## 9. API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/mobile/config` | 读取配置 |
| POST | `/api/mobile/config` | 保存客户端默认配置 |
| GET | `/api/mobile/health` | adb + Appium 健康检查 |
| GET | `/api/mobile/devices` | USB 设备列表 |
| POST | `/api/mobile/connect` | 连接 Appium + 启动 scrcpy |
| POST | `/api/mobile/disconnect` | 断开 |
| GET | `/api/mobile/mirror/frame?session_id=` | 投屏帧（base64 PNG） |
| POST | `/api/mobile/tap-at` | canvas 坐标点击 |
| POST | `/api/mobile/run` | 运行 Android 用例 |

## 10. 故障排查

| 现象 | 处理 |
|------|------|
| 503 Appium 不可用 | 确认 `appium` 已启动，`APPIUM_SERVER_URL` 正确 |
| adb unauthorized | 手机上点「允许 USB 调试」 |
| 无设备列表 | 检查数据线、`adb devices`、驱动 |
| 元素找不到 | 用 Appium Inspector 确认 strategy/selector_value |
| ENABLE_MOBILE=0 | 修改 `.env` 后重启平台 |
| Web+Android 混排失败 | 当前版本不支持，请拆分用例 |

失败截图与日志：

- `static/mobile_screenshots/`
- `logs/` 与 **运行历史** 中的 step error 字段

## 11. 架构说明

执行路由：`step_executor` → `MobileExecutor`（Appium 单会话）  
与 Web（Playwright）、Desktop 共用 `execution_lock` 本机执行锁。

AI 模块化目录 `ai_modules/` 预留生成/执行/优化三分结构，后续迭代。
