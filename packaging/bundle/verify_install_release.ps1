# Verify release directory layout and dependencies
param(
    [Parameter(Mandatory = $true)][string] $ReleaseDir,
    [string] $Root = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path (Split-Path $PSScriptRoot -Parent) "tools\Get-ProjectRoot.ps1")
if (-not $Root) { $Root = Get-ProjectRoot -StartPath $PSScriptRoot }
$release = Join-Path $Root $ReleaseDir
$py = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "=== Release Directory Self-Check ===" -ForegroundColor Cyan
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
    "ai_provider_catalog.json"
)
$mustDirs = @(
    "redist\webview2"
)

$fail = 0
foreach ($rel in $mustFiles) {
    $p = Join-Path $release $rel
    if (-not (Test-Path $p)) {
        Write-Host "  [MISSING] $rel" -ForegroundColor Red
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
            Write-Host "  [MISSING] $rel" -ForegroundColor Red
            $fail++
        }
    }
} else {
    if (-not (Test-Path (Join-Path $release "app.py"))) {
        Write-Host "  [MISSING] app.py (Legacy mode)" -ForegroundColor Red
        $fail++
    }
}

$hasChromium = Test-Path (Join-Path $release "playwright-browsers")
if ($hasChromium) {
    Write-Host "  Chromium: bundled" -ForegroundColor Green
} else {
    Write-Host "  Chromium: not bundled (Lite mode, auto-download on first use)" -ForegroundColor DarkYellow
}

foreach ($rel in $mustDirs) {
    if (-not (Test-Path (Join-Path $release $rel))) {
        Write-Host "  [MISSING] $rel\" -ForegroundColor Red
        $fail++
    }
}

if ($fail -gt 0) {
    throw "Release directory self-check FAILED: $fail item(s) missing. Please re-run prepare_offline_release / build_desktop_installer.ps1"
}

Write-Host "  File layout OK" -ForegroundColor Green

$catalogPath = Join-Path $release "ai_provider_catalog.json"
if (-not (Test-Path $catalogPath)) {
    $catalogPath = Join-Path $release "config\ai_provider_catalog.json"
}
if (Test-Path $catalogPath) {
    try {
        $cat = Get-Content -Raw -Encoding UTF8 $catalogPath | ConvertFrom-Json
        $n = @($cat.providers).Count
        if ($n -lt 1) {
            throw "ai_provider_catalog.json providers is empty"
        }
        Write-Host "  AI provider catalog OK ($n providers)" -ForegroundColor Green
    } catch {
        Write-Host "  [INVALID] ai_provider_catalog.json: $($_.Exception.Message)" -ForegroundColor Red
        $fail++
    }
} else {
    Write-Host "  [MISSING] ai_provider_catalog.json" -ForegroundColor Red
    $fail++
}

if ($fail -gt 0) {
    throw "Release directory self-check FAILED: $fail item(s) missing."
}

$probeScript = Join-Path $release "packaging\uat_desktop.py"
$pyw = Join-Path $release ".venv\pythonw.exe"
& $pyw $probeScript --probe 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "uat_desktop --probe failed (common cause: pywebview not installed in .venv)"
}

Write-Host "  Python dependencies OK" -ForegroundColor Green
Write-Host "Release directory is ready for Inno Setup packaging." -ForegroundColor Green
