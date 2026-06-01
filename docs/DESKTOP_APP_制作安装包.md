# 桌面版安装包 — 给制作人员（直白步骤）

目标：用户 **只安装一个 exe**，不出现 Python、Inno、WebView2、Playwright 等任何「请另行下载」提示。

## 重要：Inno Setup 不给最终用户

| 谁 | 是否需要 Inno Setup |
|----|---------------------|
| **您（制作安装包）** | 不需要手动装——`build_desktop_installer.ps1` 会自动下载到 `packaging\tools\` |
| **测试同事（最终用户）** | **完全不需要**，只运行 `uat_platform_setup.exe |

## 一条命令（推荐）

在项目根目录 PowerShell（构建机只需有 Python 用于「打包」一次）：

```powershell
cd D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform
.\packaging\build_desktop_installer.ps1
```

脚本会自动：

1. 准备 `dist\uat_release`（代码 + 内置 `.venv` + `playwright-browsers` + 默认 `.env`）
2. 下载 WebView2 安装程序并打进包（用户机离线可装界面）
3. 自动下载 Inno Setup 6 到 `packaging\tools` 并编译
4. 输出 **`dist\uat_platform_setup.exe`**（可能 1～3GB，正常）

完成后 **只把这一个 exe 发给用户**。

## 团队版：连接共享服务器

桌面安装包默认 `DEPLOYMENT_MODE=client`（见 `packaging/uat_desktop.py`）。用户首次启动需在 **团队服务器** 页面填写服务器地址与账号；数据保存在服务器，自动化仍在本机执行。详见 [ARCHITECTURE_CLIENT_SERVER.md](ARCHITECTURE_CLIENT_SERVER.md)。

团队服务器单独部署：

```powershell
$env:DEPLOYMENT_MODE = "server"
python app.py
```

创始人 License / 安装包统计：`python -m platform_admin.app`（默认端口 5100）。

## 原理（方便排查）

| 部分 | 文件 |
|------|------|
| 用户点的图标 | `packaging\Launch-UAT-Desktop.ps1` |
| 真正入口 | `packaging\uat_desktop.py` |
| 后台服务 | 安装目录内 `.venv` 跑 `app.py`（无黑窗口） |
| 界面窗口 | pywebview（Windows 上用 Edge 内核嵌入） |
| 环境变量 | 由 `uat_desktop.py` 自动设置，不依赖用户改 .env |

## 开发机自测桌面版（不必先打安装包）

```powershell
pip install pywebview
python packaging\uat_desktop.py
```

应弹出标题为「HuFirst UAT 测试平台」的窗口。

## 与旧版 `run_uat_local.ps1` 的区别

| | run_uat_local.ps1 | 桌面版 uat_desktop.py |
|--|-------------------|------------------------|
| 界面 | 系统浏览器打开网址 | 独立软件窗口 |
| 适合 | 开发调试 | 给测试人员安装 |

## 安装包内已包含（用户零下载）

| 组件 | 位置（安装后） |
|------|----------------|
| Python 运行时 | `{安装目录}\.venv\` |
| Playwright Chromium | `{安装目录}\playwright-browsers\` |
| WebView2 界面库 | 安装时按需从包内 `redist\webview2` 静默安装 |
| 默认配置 | 已带 `.env`，程序自动使用 |

## 体积说明

全量离线包通常 **1～3 GB**，体积大是因为把运行环境全部打进去了；换来用户 **断网也能装、装完就能用**。
