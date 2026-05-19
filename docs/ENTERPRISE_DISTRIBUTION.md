# 企业分发扩展能力

本文档汇总差分更新、MDM/SCCM、超大安装包与 SmartScreen 四类能力的路径与限制。

## 1. 差分更新 UI

### 组件

| 文件 | 作用 |
|------|------|
| `packaging/enterprise/update_ui.py` | Tkinter 向导：差分 / 完整包、进度条 |
| `packaging/enterprise/update_core.py` | 清单、下载、SHA256 |
| `packaging/enterprise/update_patch.py` | bsdiff4 构建/应用 |
| `packaging/enterprise/build_delta.py` | CI 生成差分包与 manifest 片段 |

### 依赖

```bash
pip install bsdiff4
```

### 清单字段（差分）

见 `update_manifest.example.json` 中 `base_version`、`patch_url`、`patch_sha256`、`patch_cache_basename`。

差分基线默认缓存：`%ProgramData%\HuFirst\UAT\update_cache\<上一版 setup.exe>`（首次需完整安装或完整下载一次）。

### 使用

```powershell
$env:UAT_UPDATE_MANIFEST_URL = "https://updates.example.com/uat/update.json"
$env:UAT_APP_VERSION = "1.1.0"
python packaging/enterprise/update_ui.py
```

### CI 构建差分

```powershell
python packaging/enterprise/build_delta.py `
  --old dist/uat_platform_setup_1.1.0.exe `
  --new dist/uat_platform_setup_1.2.0.exe `
  --out-patch dist/patch_1.1.0_to_1.2.0.bsdiff `
  --base-version 1.1.0 --new-version 1.2.0 `
  --patch-url https://updates.example.com/patch_1.1.0_to_1.2.0.bsdiff `
  --emit-json dist/patch_manifest_fragment.json
```

发布前对 **合并后的 setup.exe** 执行 `sign_release.ps1`。

---

## 2. Intune / WSUS(SCCM)

| 路径 | 说明 |
|------|------|
| `packaging/enterprise/intune/` | `install.ps1`、`detect.ps1`、`Package-For-Intune.ps1` |
| `packaging/enterprise/wsus/README.md` | Configuration Manager 部署步骤（WSUS 不推送自定义应用） |

Intune Win32 应用：

1. `stage_release.ps1` + Inno 生成 `uat_platform_setup.exe`  
2. 签名  
3. `Package-For-Intune.ps1 -SourceFolder dist\uat_intune -SetupFile install.ps1`（将 install.ps1 + setup 放入同一源文件夹）  
4. 上传 `.intunewin`，检测脚本指向 `detect.ps1`

---

## 3. 内置 Playwright 超大包

见 [packaging/pyinstaller/README_ONEFILE.md](../packaging/pyinstaller/README_ONEFILE.md)。

要点：**onedir + `playwright-browsers` 目录 + Inno 安装**，不要追求真正单文件 <200MB。

---

## 4. 无证书与 SmartScreen

见 [SMARTSCREEN_ENTERPRISE.md](SMARTSCREEN_ENTERPRISE.md)。

- 生产：**签名** 或 **Intune/SCCM**  
- 开发：`unblock_dev_build.ps1`（仅 MOTW）  
- 无合法「全局豁免」

---

## 能力矩阵

| 能力 | 仓库支持 | 生产必备 |
|------|----------|----------|
| 差分更新 UI | ✅ | CDN + 签名后的 patch/安装包 |
| Intune Win32 | ✅ 脚本 | `.intunewin` + 门户配置 |
| SCCM/MECM | ✅ 文档 | DP + 检测规则 |
| Playwright 内置 | ✅ onedir 脚手架 | Inno 打包整目录 |
| SmartScreen 无签名 | ⚠️ 仅策略文档 | 证书或 MDM |
