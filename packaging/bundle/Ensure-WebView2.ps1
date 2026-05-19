# 下载 WebView2 安装程序，打入最终用户安装包（离线机也可装界面）
param(
    [string] $RedistDir = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "dist\redist\webview2")
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $RedistDir | Out-Null
$bootstrapper = Join-Path $RedistDir "MicrosoftEdgeWebview2Setup.exe"
if (Test-Path $bootstrapper) {
    Write-Host "WebView2 安装程序已存在: $bootstrapper"
    return $bootstrapper
}

# Microsoft 官方 Evergreen 引导程序
$url = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
Write-Host "下载 WebView2 运行库（将打入用户安装包）..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $url -OutFile $bootstrapper -UseBasicParsing
Write-Host "已保存: $bootstrapper" -ForegroundColor Green
return $bootstrapper
