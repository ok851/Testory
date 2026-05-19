# WSUS / Configuration Manager (SCCM) 分发

> **说明**：经典 **WSUS** 仅用于 Windows 系统更新，**不能**直接推送自定义应用。  
> 企业内分发 HuFirst UAT 安装包请使用 **Microsoft Intune** 或 **Configuration Manager (MEMCM/SCCM)**。

## Configuration Manager 流程概要

1. 构建并 **代码签名** `uat_platform_setup.exe`（见 `../sign_release.ps1`）。
2. 在 SCCM 控制台：**软件库 → 应用程序** → 创建应用程序。
3. **部署类型**：
   - 安装程序：`uat_platform_setup.exe`
   - 安装参数：`/VERYSILENT /NORESTART /SUPPRESSMSGBOXES`
   - 卸载：使用注册表 `HKLM\...\Uninstall\{AppId}_is1` 中的 `UninstallString`
4. **检测方法**（任选）：
   - 文件：`%ProgramFiles%\HuFirst\UATPlatform\app.py` 存在
   - 脚本：复用 `../intune/detect.ps1`
   - MSI 产品码（若改用 MSI 封装）
5. **分发内容**：将 setup.exe 放到 DP 内容库，部署到设备集合（测试组 → 生产组）。
6. **维护窗口**：桌面自动化用例需在 **用户登录且桌面可用** 时执行；SCCM 仅负责安装平台，不负责用例调度。

## 与差分更新关系

- SCCM/Intune 适合 **全量安装包** 升级（版本检测 + 新 setup.exe）。
- 公网客户端可使用 `update_ui.py` 差分；域内机器建议 **统一由 IT 推送完整 MSI/EXE**，避免每台机自行打补丁。

## 组策略配合 SmartScreen

见 [docs/SMARTSCREEN_ENTERPRISE.md](../../../docs/SMARTSCREEN_ENTERPRISE.md)。
