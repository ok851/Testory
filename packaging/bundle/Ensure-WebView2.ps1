# 下载 WebView2 安装程序，打入最终用户安装包（离线机也可装界面）
param(
    [string] $ProjectRoot = "",
    [string] $RedistDir = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path (Split-Path $PSScriptRoot -Parent) "tools\Get-ProjectRoot.ps1")
if (-not $ProjectRoot) {
    $ProjectRoot = Get-ProjectRoot -StartPath $PSScriptRoot
}
if (-not $RedistDir) {
    $RedistDir = Join-Path $ProjectRoot "dist\redist\webview2"
}

New-Item -ItemType Directory -Force -Path $RedistDir | Out-Null
$bootstrapper = Join-Path $RedistDir "MicrosoftEdgeWebview2Setup.exe"
if (Test-Path $bootstrapper) {
    Write-Host "WebView2 安装程序已存在: $bootstrapper"
    return $bootstrapper
}

$url = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
Write-Host "下载 WebView2 运行库（将打入用户安装包）..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $url -OutFile $bootstrapper -UseBasicParsing
Write-Host "已保存: $bootstrapper" -ForegroundColor Green
return $bootstrapper
