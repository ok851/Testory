# HuFirst UAT 本地版一键启动（Windows · 开发人员）
# 用法：
#   .\packaging\run_uat_local.ps1           # 浏览器调试（dev only）
#   .\packaging\run_uat_local.ps1 -Desktop  # pywebview 桌面壳调试

param(
    [switch] $Desktop
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "== Testory 开发环境 ==" -ForegroundColor Cyan
Write-Host "项目目录: $Root"
if ($Desktop) {
    Write-Host "模式: 桌面壳 (pywebview)" -ForegroundColor Yellow
} else {
    Write-Host "模式: 浏览器 (dev only)" -ForegroundColor DarkGray
}

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

if (-not (Test-Command python)) {
    Write-Host "未找到 python，请先安装 Python 3.10+ 并加入 PATH。" -ForegroundColor Red
    exit 1
}

$venvPath = Join-Path $Root ".venv"
if (-not (Test-Path $venvPath)) {
    Write-Host "创建虚拟环境 .venv ..."
    python -m venv $venvPath
}

$py = Join-Path $venvPath "Scripts\python.exe"
$pip = Join-Path $venvPath "Scripts\pip.exe"
$pyw = Join-Path $venvPath "Scripts\pythonw.exe"

Write-Host "安装 Python 依赖..."
& $pip install -q -r requirements.txt
if (Test-Path "requirements-windows.txt") {
    & $pip install -q -r requirements-windows.txt
}

Write-Host "检查离线前端 vendor 资源..."
& $py packaging/fetch_frontend_vendors.py 2>$null

Write-Host "检查 Playwright 浏览器..."
& $py -m playwright install chromium 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "提示: 若 playwright 未安装，请运行: $py -m pip install playwright && $py -m playwright install" -ForegroundColor Yellow
}

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "已从 .env.example 创建 .env"
    }
}
& $py env_example_sync.py 2>$null | Out-Host

$env:EMBEDDED_BROWSER_AUTO_START_GATEWAY = "1"
$env:DEPLOYMENT_PROFILE = "local"
$env:DESKTOP_EXECUTION_MODE = "inprocess"
$env:PLAYWRIGHT_HEADLESS = "0"
$env:DESKTOP_AUTO_START_GATEWAY = "0"

New-Item -ItemType Directory -Force -Path "data" | Out-Null
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

if ($env:UAT_CHECK_UPDATE -eq "1" -and $env:UAT_UPDATE_MANIFEST_URL) {
    Write-Host "检查更新..."
    & $py packaging/enterprise/update_ui.py
    if ($LASTEXITCODE -ne 0 -and $env:UAT_UPDATE_MANDATORY -eq "1") { exit $LASTEXITCODE }
}

if ($Desktop) {
    $env:UAT_DESKTOP_MODE = "1"
    $env:DEPLOYMENT_MODE = "client"
    $env:FLASK_RUN_HOST = "127.0.0.1"
    $env:DESKTOP_LAZY_GATEWAY_BOOT = "1"
    $env:TESTORY_FRAMELESS_SHELL = "1"
    Write-Host "启动桌面壳..." -ForegroundColor Green
    if (Test-Path $pyw) {
        & $pyw packaging/uat_desktop.py
    } else {
        & $py packaging/uat_desktop.py
    }
    exit $LASTEXITCODE
}

$url = "http://127.0.0.1:5000"
Write-Host "启动 Flask (dev): $url" -ForegroundColor Green
Write-Host "提示: 最终用户应使用 Testory.exe 安装包，而非浏览器访问。" -ForegroundColor DarkGray
Start-Process $url
& $py app.py
