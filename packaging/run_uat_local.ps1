# HuFirst UAT 本地版一键启动（Windows）
# 用法：在 PowerShell 中执行  .\packaging\run_uat_local.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "== HuFirst UAT 本地版 ==" -ForegroundColor Cyan
Write-Host "项目目录: $Root"

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

Write-Host "安装 Python 依赖..."
& $pip install -q -r requirements.txt
if (Test-Path "requirements-windows.txt") {
    & $pip install -q -r requirements-windows.txt
}

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

$env:DEPLOYMENT_PROFILE = "local"
$env:DESKTOP_EXECUTION_MODE = "inprocess"
$env:PLAYWRIGHT_HEADLESS = "0"
$env:DESKTOP_AUTO_START_GATEWAY = "0"

New-Item -ItemType Directory -Force -Path "data" | Out-Null
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

$url = "http://127.0.0.1:5000"

if ($env:UAT_CHECK_UPDATE -eq "1" -and $env:UAT_UPDATE_MANIFEST_URL) {
    Write-Host "检查更新..."
    & $py packaging/enterprise/update_ui.py
    if ($LASTEXITCODE -ne 0 -and $env:UAT_UPDATE_MANDATORY -eq "1") { exit $LASTEXITCODE }
}

Write-Host "启动 Flask: $url" -ForegroundColor Green
Start-Process $url

& $py app.py
