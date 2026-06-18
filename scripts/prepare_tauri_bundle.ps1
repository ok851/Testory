# Prepare Tauri desktop bundle (vendor + optional cargo build)
param(
    [switch] $SkipCargo
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "Installing npm dependencies..." -ForegroundColor Cyan
npm ci
if ($LASTEXITCODE -ne 0) { npm install }

Write-Host "Building Tauri API vendor..." -ForegroundColor Cyan
npm run build:tauri-api
if (-not (Test-Path "static/vendor/tauri/window.js")) {
    throw "static/vendor/tauri build failed"
}

if ($SkipCargo) {
    Write-Host "SkipCargo set — vendor ready." -ForegroundColor Green
    exit 0
}

Write-Host "Building Tauri app (requires Rust toolchain)..." -ForegroundColor Cyan
Set-Location (Join-Path $Root "src-tauri")
cargo tauri build
exit $LASTEXITCODE
