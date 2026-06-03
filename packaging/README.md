# 本地版安装与发行

## 给最终用户：全量离线安装包（推荐，默认 Protected）

用户 **只装一个 exe**：内置 Python（桌面壳）、PyInstaller 后端、Chromium、WebView2；**不需要** Inno / 不需要另下 Python。  
**默认不在安装目录暴露** `app.py`、`license_manager.py` 等明文源码（PyInstaller onedir）。

```powershell
cd <项目根目录>
.\packaging\build_desktop_installer.ps1
# 明文源码包（仅内部调试）：.\packaging\build_desktop_installer.ps1 -Legacy
# 或指定 ISCC：-IsccPath "D:\Inno Setup 6\ISCC.exe"
# 或仅准备发布目录：-PrepareOnly（再在 Inno 图形界面编译 .iss）
```

- 构建机需本机安装 **Inno Setup 6**；脚本**只查找 ISCC，不会下载 Inno**
- **发给用户**：仅 `dist\testory_setup.exe` + 全部分卷 `testory_setup-*.bin`
- 打包与网盘分发说明：[docs/DESKTOP_PROTECTED_BUILD.md](../docs/DESKTOP_PROTECTED_BUILD.md)

**移动端模拟器（可选）**：将 Eclipse Temurin **JRE 11+** 解压到发布目录 `runtime\jre\`（与主程序同级），插件市场安装模拟器 SDK 时会自动使用，用户无需再装 Java。离线 Android 组件 zip 可放在 `offline_plugins\`。

说明：[docs/DESKTOP_APP_制作安装包.md](../docs/DESKTOP_APP_制作安装包.md) · [docs/DESKTOP_APP_用户使用说明.md](../docs/DESKTOP_APP_用户使用说明.md)

开发自测：`pip install pywebview` 后 `python packaging/uat_desktop.py`

---

## 开发人员：本机快速启动（会打开浏览器）

```powershell
cd <项目根目录>
.\packaging\run_uat_local.ps1
```

脚本将安装依赖并 **用系统浏览器** 打开 `http://127.0.0.1:5000`（适合改代码调试）。

## 日志与数据

| 路径 | 说明 |
|------|------|
| `logs/` | 平台运行日志 |
| `data/` | 执行锁、可配置 `DATABASE_PATH` 指向的 SQLite |
| `screenshots/` | 失败截图 |

## 升级

```powershell
git pull
.\packaging\run_uat_local.ps1
```

## 可选组件

### 桌面自动化网关（进程隔离）

```powershell
$env:DESKTOP_EXECUTION_MODE = "gateway"
$env:DESKTOP_AUTO_START_GATEWAY = "1"
python -m desktop_automation_gateway
python app.py
```

### PyInstaller 草稿

`uat_platform.spec` 为启动器原型，不包含 Playwright 浏览器体积；生产打包需另行处理 Chromium 分发。

### pywebview 托盘原型

```powershell
pip install pywebview
python packaging/uat_tray_webview.py
```

在独立窗口中加载 `http://127.0.0.1:5000`（需另终端运行 `app.py` 或由脚本拉起）。

## 架构说明

- 本地架构文档：[docs/ARCHITECTURE_LOCAL.md](../docs/ARCHITECTURE_LOCAL.md)
- 16:9 架构图：`docs/assets/architecture_local_16x9.svg` / `.png`
- Docker 编排：[docs/DOCKER_COMPOSE.md](../docs/DOCKER_COMPOSE.md)

## 企业发行（MSI / 签名 / 自动更新）

见 [packaging/enterprise/README.md](enterprise/README.md)。

扩展：**差分更新 UI**、**Intune/SCCM**、**Playwright 内置包**、**SmartScreen 策略** → [docs/ENTERPRISE_DISTRIBUTION.md](../docs/ENTERPRISE_DISTRIBUTION.md)。
