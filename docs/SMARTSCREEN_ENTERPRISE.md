# 无代码签名证书时的 SmartScreen 与企业策略

## 重要结论

**不存在**面向公众的合法「豁免 SmartScreen」开关。未签名的 `uat_platform_setup.exe` 在首次分发时几乎总会出现：

> Windows 已保护你的电脑  
> Microsoft Defender SmartScreen 无法验证此应用

下列为企业内 **可接受** 的治理手段（需 IT 管理员权限），**不是**绕过安全机制。

## 推荐优先级

| 优先级 | 手段 | 适用 |
|--------|------|------|
| 1 | **OV/EV 代码签名** + 时间戳 | 对外客户、互联网下载 |
| 2 | **Intune / SCCM** 推送已签名安装包 | 域内批量部署 |
| 3 | **组策略** 降低 SmartScreen（仅受管设备） | 内网实验室 |
| 4 | 用户点击「更多信息」→「仍要运行」 | 开发/POC，不可规模化 |

## 组策略（受管设备）

路径（因 Windows 版本略有差异）：

`计算机配置 → 管理模板 → Windows 组件 → Windows Defender SmartScreen → Explorer`

常见设置：

- **Configure Windows Defender SmartScreen**：已启用 → **警告** 或按安全部门要求禁用（**不推荐**全网禁用）
- **Prevent bypassing Windows Defender SmartScreen prompts for unverified files**：未配置或禁用（允许受控环境下用户确认）

配合 **应用控制**（WDAC / AppLocker）将 `HuFirst\UATPlatform` 安装目录加入允许路径，比全局关闭 SmartScreen 更安全。

## 内部信誉（仍建议签名）

即使无证书，随下载量增加，SmartScreen 对**同一文件哈希**的警告可能减少，但：

- 每次重新编译安装包 → **新哈希** → 警告重现  
- 无法替代正式签名与 Intune 分发  

## 开发机临时处理（仅限研发）

```powershell
# 解除 Mark-of-the-Web（不消除 SmartScreen，仅去掉「从互联网下载」标记）
.\packaging\enterprise\unblock_dev_build.ps1 -Path dist\uat_platform_setup.exe
```

**禁止**要求最终用户执行；生产环境必须使用签名安装包或 MDM 推送。

## 与差分更新 / 超大包的关系

- 差分补丁合并后的新 `setup.exe` 若无签名，SmartScreen **再次**拦截 → 发布流水线应对 **合并后的安装包** 统一签名（`sign_release.ps1`）。
- PyInstaller 超大单文件更易触发启发式扫描 → 优先 **onedir + Inno Setup** 并签名。

## 合规提示

- 勿指导用户永久关闭 Defender / SmartScreen。  
- 对外软件分发长期应规划 **代码签名证书**（含时间戳服务）。  
- 医疗/金融等行业可能还要求 SBOM、漏洞扫描，见 `packaging/enterprise/README.md`。
