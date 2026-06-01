# 仅用于开发/内网测试：移除 MOTW，不能替代代码签名
param(
    [Parameter(Mandatory = $true)]
    [string] $Path
)
$ErrorActionPreference = "Stop"
Resolve-Path -LiteralPath $Path | ForEach-Object {
    Unblock-File -LiteralPath $_.Path
    Write-Host "已 Unblock-File: $($_.Path)" -ForegroundColor Yellow
    Write-Host "注意: SmartScreen 仍可能拦截未签名 exe。生产请使用 sign_release.ps1 或 Intune 推送。"
}
