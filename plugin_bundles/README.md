# 插件市场离线安装包（移动端）

> **终端用户**：在软件内打开 **插件市场** 点「安装」即可，无需阅读本文。  
> **管理员 / 内网部署**：按下面说明准备离线 zip。

## Android 模拟器 SDK（命令行，推荐）

插件市场「**Android 模拟器 SDK（命令行）**」：安装模拟器、系统镜像与默认虚拟手机 `Testory_Pixel7`，环境自动配置（无需 Android Studio）。

**用户前置：** 本机已安装 Java 11+。

### 离线 / 内网（管理员）

1. 下载 [commandlinetools-win](https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip)（Linux/mac 见 `config/plugin_bundles/android_emulator_sdk.json`）
2. 重命名为 `commandlinetools-win-latest.zip`
3. 放入发布包目录 **`offline_plugins/`**（与主程序同级）或 **`plugin_bundles/`**
4. 用户在本机插件市场点击「安装」（后续组件下载仍可能需要外网，或由管理员预置完整 SDK）

**安装结果位置（用户数据）：** `%LOCALAPPDATA%\NewUITestPlatform\extensions\android\sdk\`

### 国内镜像（默认）

安装时优先从 **阿里云 Android 仓库镜像** 下载 commandlinetools 与 sdkmanager 组件；可在 `.env` 覆盖：

```env
ANDROID_SDK_REPO_MIRROR=https://mirrors.aliyun.com/android/repository/
```

### 开发 / 私有化配置（可选）

```env
ANDROID_CMDLINE_TOOLS_LOCAL_ZIP=D:\Tools\commandlinetools-win-latest.zip
ANDROID_CMDLINE_TOOLS_URL=https://你的CDN/commandlinetools-win-latest.zip
```

---

## Platform-Tools / adb

把 Google **Platform-Tools** 的 zip 放在本目录（或通过 URL / 环境变量指定），插件市场「Android Platform-Tools (adb)」即可一键安装。

## 你需要提供什么

任选 **一种** 方式即可（优先级从高到低）：

### 方式 A：本地 zip（推荐内网 / 离线）

1. 从 Google 下载对应系统 zip（或从已装 Android Studio 的 SDK 里复制）  
   - Windows: [platform-tools-latest-windows.zip](https://dl.google.com/android/repository/platform-tools-latest-windows.zip)  
   - 解压后应包含 `platform-tools/adb.exe`
2. **不要解压**，将 zip **原样** 放到本目录，并命名为：
   - Windows: `platform-tools-windows.zip`
   - Linux: `platform-tools-linux.zip`
   - macOS: `platform-tools-darwin.zip`
3. 在插件市场点击「安装」。

### 方式 B：下载地址（公网）

编辑 `config/plugin_bundles/android_platform_tools.json`，修改对应平台的 `url`（可改为你们 CDN/OSS 地址）：

```json
"windows": {
  "url": "https://你的域名/static/platform-tools-windows.zip",
  "sha256": "可选，填 zip 的 SHA256 十六进制"
}
```

或在 `.env` 中设置（无需改 JSON）：

```env
ANDROID_PLATFORM_TOOLS_URL=https://你的域名/path/platform-tools-latest-windows.zip
```

### 方式 C：指定本机任意路径

`.env`：

```env
ANDROID_PLATFORM_TOOLS_LOCAL_ZIP=D:\Tools\platform-tools-latest-windows.zip
```

## 安装结果位置

- 解压目录：`%LOCALAPPDATA%\NewUITestPlatform\extensions\android\platform-tools\`
- **无需用户手改 `.env`**：安装完成后平台自动：
  - 写入 `data/client_config.json` → `mobile_defaults.adb_path`
  - 当前服务进程内设置 `ADB_PATH`
  - 移动端测试 / `adb devices` 优先使用该路径

用户流程：插件市场点「安装」→ 打开「移动端测试」→ USB 连接手机 →「一键连接」。

## 校验

安装后在终端执行（路径以安装结果为准）：

```powershell
%LOCALAPPDATA%\NewUITestPlatform\extensions\android\platform-tools\adb.exe devices -l
```

## 许可

分发 zip 时请保留 Google SDK 许可说明，见 [SDK 条款](https://developer.android.com/studio/terms)。
