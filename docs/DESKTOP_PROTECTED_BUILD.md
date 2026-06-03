# Testory 保护版桌面安装包 — 打包与分发

默认构建为 **Protected（PyInstaller onedir）**：安装目录**不再**包含 `app.py`、`license_manager.py` 等明文业务源码；后端在 `runtime\testory_app\TestoryBackend.exe` 中运行。

## 一、在你电脑上打包（只需做一次环境准备）

### 1. 前置条件

- Windows 10/11
- [Inno Setup 6](https://jrsoftware.org/isinfo.php)（仅**构建机**需要，用户不需要）
- Python 3.10+，项目根目录已创建 `.venv` 并安装依赖

```powershell
cd D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pip install -r requirements-windows.txt
.\.venv\Scripts\python -m playwright install chromium
```

### 2. 一键构建安装包（推荐）

```powershell
cd <项目根目录>
.\packaging\build_desktop_installer.ps1
```

- 首次会下载 WebView2 引导包、编译 PyInstaller（约 **10～30 分钟**，视机器而定）
- 产物在项目根目录 `dist\` 下

### 3. 构建产物（发给用户的文件）

| 文件 | 说明 |
|------|------|
| `dist\testory_setup.exe` | 安装向导（必发） |
| `dist\testory_setup-1.bin` | 分卷数据（若存在则**必发**） |

**不要**把整个 `dist` 文件夹或 `uat_release` 发给用户。

### 4. 开发调试（仍用明文源码）

```powershell
.\packaging\run_uat_local.ps1
```

### 5. 仅当需要「旧版明文安装包」时

```powershell
.\packaging\build_desktop_installer.ps1 -Legacy
```

仅供内部调试，**不要**对外分发。

---

## 二、让别人下载使用（无需自有服务器）

### 方式 A：网盘（最简单）

1. 将 `testory_setup.exe` 与所有 `testory_setup-*.bin` 打成 zip  
2. 上传百度网盘 / 阿里云盘等，生成分享链接  
3. 附简短说明（见下「用户安装说明」）

### 方式 B：GitHub / Gitee Release

1. 新建 Release 标签，如 `v1.0.0`  
2. 上传 zip 或 exe+bin  
3. 把 Release 页面链接发给用户  

### 方式 C：企业内网

共享盘、U 盘、Intune/SCCM 推送 `testory_setup.exe`（见 `packaging/enterprise/README.md`）

---

## 三、给最终用户的安装说明（可复制）

1. 解压 zip，确保 **exe 与 bin 在同一文件夹**  
2. 双击 **`testory_setup.exe`**，按向导安装（体积较大，需几分钟）  
3. 从开始菜单打开 **Testory**  
4. 系统要求：**Windows 10/11**，无需安装 Python  
5. 首次启动若提示「未知发布者」，选「仍要运行」（未签名时常见）；正式对外建议代码签名  

数据与配置在：`%LOCALAPPDATA%\Testory\`（用户数据、`.env`、`license.key`）

---

## 四、License 与源码保护说明

| 项目 | 说明 |
|------|------|
| 业务 Python | 打进 `TestoryBackend.exe` / `_internal`，**非**明文 `.py` |
| `license_manager` | 随 PyInstaller 打包，比明文难篡改（仍无法 100% 防破解） |
| `templates` / `static` | 仍在安装目录（HTML/JS/CSS 可见，属正常） |
| 桌面壳层 | 少量 `packaging\*.py`（启动窗口用），不含完整业务逻辑 |

按客户签发 `license.key`：使用项目内 `generate_license.py` 或管理流程，与安装包一并交付或邮件发送。

---

## 五、常见问题

**PyInstaller 构建失败 `ModuleNotFoundError`？**  
在 `.venv` 中确认依赖已装全，必要时在 `packaging/pyinstaller/_spec_common.py` 的 `hiddenimports` 中补充模块名后重跑构建。

**安装后无法启动？**  
查看 `%LOCALAPPDATA%\Testory\logs\backend_startup.log` 与 `launcher.log`。

**安装包比旧版更大？**  
Protected 模式含独立后端运行时，体积正常；可分卷分发。

**仍想用明文包做对比？**  
`.\packaging\build_desktop_installer.ps1 -Legacy`
