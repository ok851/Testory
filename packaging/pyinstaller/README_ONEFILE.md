# 内置 Playwright 的超大安装包

Playwright Chromium 约 **150–300MB**，与 Python 运行时、依赖合计后 **单文件 exe 常超过 500MB**，启动慢且易触发杀毒启发式扫描。

## 推荐策略

| 策略 | 体积 | 启动 | 说明 |
|------|------|------|------|
| **onedir + 外置浏览器目录** | 大，可拆分 | 快 | **推荐**；见 `bundle_playwright.ps1` |
| onefile | 单 exe | 慢（解压 TEMP） | 仅适合演示 |
| 安装后 `playwright install` | 小安装包 | 首次启动下载 | `run_uat_local.ps1` 模式 |

## 构建 onedir（含浏览器）

```powershell
# 1. 准备 venv 与浏览器
.\packaging\run_uat_local.ps1   # 或手动 venv + playwright install chromium

# 2. 复制浏览器到发布目录
.\packaging\pyinstaller\bundle_playwright.ps1 -VenvPath .\.venv -OutDir dist\uat_bundle

# 3. PyInstaller
.\.venv\Scripts\pip install pyinstaller
.\.venv\Scripts\pyinstaller packaging\pyinstaller\uat_onedir.spec

# 4. 启动器会设置 PLAYWRIGHT_BROWSERS_PATH=.\playwright-browsers
```

产物：`dist\uat_onedir\` 整个文件夹需一起分发（可用 Inno Setup 打包，见 `packaging/inno/uat_platform.iss`）。

## 环境变量

安装目录内启动器设置：

```
PLAYWRIGHT_BROWSERS_PATH=%InstallDir%\playwright-browsers
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
```

## 限制

- 仍 **不支持** 在单 exe 内可靠嵌入「每次运行自解压 Chromium」且体积 <200MB。
- 桌面 pywinauto 步骤依赖 **Windows 桌面会话**，与打包形态无关。
