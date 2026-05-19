# 将安装目录或 setup.exe 打包为 .intunewin（需 Microsoft Win32 Content Prep Tool）
# 下载: https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool
param(
    [Parameter(Mandatory = $true)]
    [string] $SourceFolder,
    [string] $SetupFile = "uat_platform_setup.exe",
    [string] $OutputFolder = "dist\intune",
    [string] $ContentPrepExe = $env:INTUNE_WIN32_CONTENT_PREP_TOOL
)

$ErrorActionPreference = "Stop"

if (-not $ContentPrepExe -or -not (Test-Path $ContentPrepExe)) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft Intune Win32 Content Prep Tool\IntuneWinAppUtil.exe",
        "$PSScriptRoot\IntuneWinAppUtil.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $ContentPrepExe = $c; break }
    }
}
if (-not (Test-Path $ContentPrepExe)) {
    throw "未找到 IntuneWinAppUtil.exe。请设置 INTUNE_WIN32_CONTENT_PREP_TOOL 或将工具放到 intune 目录。"
}

New-Item -ItemType Directory -Force -Path $OutputFolder | Out-Null
$src = Resolve-Path $SourceFolder
$out = Resolve-Path $OutputFolder

Write-Host "打包 Win32 应用..."
& $ContentPrepExe -c $src -s $SetupFile -o $out -q
Write-Host "产物目录: $out"
Write-Host @"

Intune 门户配置建议:
  安装命令:   powershell.exe -ExecutionPolicy Bypass -File install.ps1
  卸载命令:   (由 Inno 生成的 UninstallString)
  检测规则:   自定义脚本 detect.ps1 或 文件存在 + 注册表版本
  安装上下文: 系统（若需写 Program Files）或 用户
  重启行为:   基于安装结果

"@
