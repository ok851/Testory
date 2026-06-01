# Intune Win32 应用 — 安装脚本（以 SYSTEM 或用户上下文运行）
# 将 $Installer 指向已签名的 setup.exe 或 MSI
param(
    [string] $Installer = "$PSScriptRoot\..\uat_platform_setup.exe",
    [string] $LogDir = "$env:ProgramData\HuFirst\UAT\logs"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$log = Join-Path $LogDir "intune_install_$(Get-Date -Format yyyyMMdd_HHmmss).log"
Start-Transcript -Path $log -Force

try {
    if (-not (Test-Path $Installer)) {
        throw "安装包不存在: $Installer"
    }
    Write-Host "安装: $Installer"
    $proc = Start-Process -FilePath $Installer -ArgumentList "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES /LOG=`"$LogDir\inno_setup.log`"" -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "安装程序退出码: $($proc.ExitCode)"
    }
    [Environment]::SetEnvironmentVariable("UAT_APP_VERSION", "1.0.0", "Machine")
    Write-Host "安装完成"
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
finally {
    Stop-Transcript
}
