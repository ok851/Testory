# 发布目录完整性自检（构建机执行，避免把残缺包装进安装包）
param(
    [Parameter(Mandatory = $true)][string] $ReleaseDir,
    [string] $Root = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path (Split-Path $PSScriptRoot -Parent) "tools\Get-ProjectRoot.ps1")
if (-not $Root) { $Root = Get-ProjectRoot -StartPath $PSScriptRoot }
$release = Join-Path $Root $ReleaseDir
$py = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "=== 安装包发布目录自检 ===" -ForegroundColor Cyan
Write-Host "  $release" -ForegroundColor DarkGray

$mustFiles = @(
    "Testory.exe",
    "Testory.ico",
    "packaging\uat_desktop.py",
    "packaging\launch_checks.py",
    "packaging\desktop_shell.py",
    "packaging\testory_runtime.py",
    "install_paths.py",
    ".venv\pythonw.exe",
    ".venv\python.exe",
    "static\desktop\shell_boot.html",
    "templates",
    "static",
    "requirements.txt",
    "requirements-windows.txt",
    "ai_provider_catalog.json",
    "config\ai_provider_catalog.json"
)
$mustDirs = @(
    "playwright-browsers",
    "redist\webview2"
)

$fail = 0
foreach ($rel in $mustFiles) {
    $p = Join-Path $release $rel
    if (-not (Test-Path $p)) {
        Write-Host "  [缺失] $rel" -ForegroundColor Red
        $fail++
    }
}

$protected = Test-Path (Join-Path $release "runtime\testory_app\TestoryBackend.exe")
if ($protected) {
    foreach ($rel in @(
        "runtime\testory_app\TestoryBackend.exe",
        "runtime\TestoryEmbeddedGw\TestoryEmbeddedGw.exe",
        "runtime\TestoryHermesGw\TestoryHermesGw.exe",
        "runtime\TestoryDesktopGw\TestoryDesktopGw.exe",
        "runtime\TestoryMobileGw\TestoryMobileGw.exe"
    )) {
        if (-not (Test-Path (Join-Path $release $rel))) {
            Write-Host "  [缺失] $rel" -ForegroundColor Red
            $fail++
        }
    }
} else {
    if (-not (Test-Path (Join-Path $release "app.py"))) {
        Write-Host "  [缺失] app.py（Legacy 模式）" -ForegroundColor Red
        $fail++
    }
}

foreach ($rel in $mustDirs) {
    if (-not (Test-Path (Join-Path $release $rel))) {
        Write-Host "  [缺失] $rel\" -ForegroundColor Red
        $fail++
    }
}

if ($fail -gt 0) {
    throw "发布目录自检失败：$fail 项缺失。请重新执行 prepare_offline_release / build_desktop_installer.ps1"
}

Write-Host "  文件布局 OK" -ForegroundColor Green

$catalogPath = Join-Path $release "ai_provider_catalog.json"
if (-not (Test-Path $catalogPath)) {
    $catalogPath = Join-Path $release "config\ai_provider_catalog.json"
}
if (Test-Path $catalogPath) {
    try {
        $cat = Get-Content -Raw -Encoding UTF8 $catalogPath | ConvertFrom-Json
        $n = @($cat.providers).Count
        if ($n -lt 1) {
            throw "ai_provider_catalog.json providers 为空"
        }
        Write-Host "  AI 供应商目录 OK ($n 家)" -ForegroundColor Green
    } catch {
        Write-Host "  [无效] ai_provider_catalog.json: $($_.Exception.Message)" -ForegroundColor Red
        $fail++
    }
} else {
    Write-Host "  [缺失] ai_provider_catalog.json" -ForegroundColor Red
    $fail++
}

if ($fail -gt 0) {
    throw "发布目录自检失败：$fail 项缺失。请重新执行 prepare_offline_release / build_desktop_installer.ps1"
}

$probeScript = Join-Path $release "packaging\uat_desktop.py"
$pyw = Join-Path $release ".venv\pythonw.exe"
& $pyw $probeScript --probe 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "uat_desktop --probe 失败（常见：pywebview 未装入 .venv）"
}

Write-Host "  Python 依赖 OK" -ForegroundColor Green
Write-Host "发布目录可用于 Inno 打包。" -ForegroundColor Green
