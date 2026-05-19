# 企业发行：MSI、代码签名与自动更新

本目录提供 **可落地的脚手架**；完整商用发行需贵司 CA 证书、更新服务器与 IT 策略配合。

## 1. MSI 安装包（Inno Setup）

### 前置

- [Inno Setup 6](https://jrsoftware.org/isinfo.php)（Windows）
- 已用 `packaging/run_uat_local.ps1` 或自有流程准备好 `.venv`、Playwright 浏览器、`data/` 目录

### 构建

```powershell
# 先在本机准备发布目录（示例）
.\packaging\enterprise\stage_release.ps1 -OutDir dist\uat_release

# 编译 MSI（需安装 Inno Setup）
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\inno\uat_platform.iss
```

产物默认：`dist\uat_platform_setup.exe`（可由 ISS 改为生成 MSI：`OutputBaseFilename=uat_platform` + `SetupType=msisetup` 等，见 ISS 注释）。

### 代码签名

在 **有 EV/OV 代码签名证书** 的机器上：

```powershell
.\packaging\enterprise\sign_release.ps1 -FilePath dist\uat_platform_setup.exe
```

参数见脚本头部：`/fd SHA256`、时间戳服务器、证书指纹或 PFX。

未签名安装包在 SmartScreen 下会被拦截——生产环境 **必须签名**。

## 2. 自动更新架构（推荐）

```
┌─────────────┐     HTTPS GET      ┌──────────────────┐
│ 已安装客户端 │ ─────────────────► │ update.json       │
│ update_checker│ ◄───────────────── │ （版本清单 CDN）  │
└─────────────┘   version, url,     └──────────────────┘
                  sha256, notes
        │
        ▼ 有新版本且用户确认
   下载 uat_platform_setup.exe / .msi
        │
        ▼
   静默安装 / 重启应用（需管理员或 per-user 安装策略）
```

### 清单示例

见 `update_manifest.example.json`。字段：

| 字段 | 说明 |
|------|------|
| `version` | SemVer，高于本地则提示更新 |
| `package_url` | HTTPS 安装包地址 |
| `sha256` | 安装包校验 |
| `mandatory` | 是否强制 |
| `release_notes_url` | 变更说明 |

### 客户端检查

`update_checker.py` 可在启动 `app.py` 前由启动器调用（见 `uat_launcher.py` 集成示例注释）。

生产建议：

- 清单与安装包均走 **HTTPS + 签名**
- 使用贵司更新域名，支持断点续传与内网镜像
- 企业环境可用 WSUS/Intune 分发 MSI，替代公网 `package_url`

## 3. 与本机套件关系

| 交付物 | 适用 |
|--------|------|
| `run_uat_local.ps1` | 开发 / 测试团队绿色运行 |
| Inno MSI | 正式安装、开始菜单、卸载项 |
| 签名 + 自动更新 | 企业大规模部署 |

桌面混排能力仍要求 **Windows 交互式会话**；MSI 仅打包运行环境，不能替代该约束。

## 4. 扩展能力（已实现脚手架）

详见 [docs/ENTERPRISE_DISTRIBUTION.md](../../docs/ENTERPRISE_DISTRIBUTION.md)。

| 主题 | 入口 |
|------|------|
| 差分更新 UI | `update_ui.py`、`build_delta.py`、`pip install bsdiff4` |
| Intune Win32 | `intune/Package-For-Intune.ps1`、`install.ps1`、`detect.ps1` |
| SCCM / ConfigMgr | `wsus/README.md` |
| Playwright 超大包 | `pyinstaller/README_ONEFILE.md`、`bundle_playwright.ps1` |
| SmartScreen（无证书） | [docs/SMARTSCREEN_ENTERPRISE.md](../../docs/SMARTSCREEN_ENTERPRISE.md) |

## 5. 尚未包含

- Windows Service 常驻、数据库迁移自动回滚
- 官方 Microsoft Store / Winget 发布流水线
