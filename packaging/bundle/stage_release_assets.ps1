# 保护版发布：只复制资源与桌面壳层，不复制业务 .py 源码树
param(
    [string] $OutDir = "dist\uat_release",
    [string] $Root = ""
)

$ErrorActionPreference = "Stop"

. (Join-Path (Split-Path $PSScriptRoot -Parent) "tools\Get-ProjectRoot.ps1")
if (-not $Root) {
    $Root = Get-ProjectRoot -StartPath $PSScriptRoot
} else {
    $Root = (Resolve-Path $Root).Path
}

$Out = Join-Path $Root $OutDir
if (Test-Path $Out) {
    Remove-Item -LiteralPath $Out -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $Out -Force | Out-Null

$dirNames = @(
    "templates", "static", "config", "plugin_bundles", "offline_plugins"
)
foreach ($name in $dirNames) {
    $src = Join-Path $Root $name
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $Out $name) -Recurse -Force
    }
}

if (Test-Path (Join-Path $Root ".env.example")) {
    Copy-Item (Join-Path $Root ".env.example") (Join-Path $Out ".env.example") -Force
}
foreach ($req in @("requirements.txt", "requirements-windows.txt")) {
    $src = Join-Path $Root $req
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $Out $req) -Force
    }
}
foreach ($dataJson in @("ai_provider_catalog.json", "ai_model_registry.json")) {
    $src = Join-Path $Root $dataJson
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $Out $dataJson) -Force
        $cfgDir = Join-Path $Out "config"
        New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
        Copy-Item $src (Join-Path $cfgDir $dataJson) -Force
    }
}

$packDest = Join-Path $Out "packaging"
New-Item -ItemType Directory -Force -Path $packDest | Out-Null
$packFiles = @(
    "uat_desktop.py",
    "desktop_shell.py",
    "win_app_icon.py",
    "testory_runtime.py",
    "launch_checks.py",
    "Testory.cmd",
    "__init__.py"
)
foreach ($f in $packFiles) {
    $src = Join-Path $Root "packaging\$f"
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $packDest $f) -Force
    }
}

$deskUser = Join-Path $Root "desktop_user_data.py"
if (Test-Path $deskUser) {
    Copy-Item $deskUser (Join-Path $Out "desktop_user_data.py") -Force
}
$installPaths = Join-Path $Root "install_paths.py"
if (Test-Path $installPaths) {
    Copy-Item $installPaths (Join-Path $Out "install_paths.py") -Force
}

@("data", "logs", "screenshots") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $Out $_) | Out-Null
}

Write-Host "保护版资源目录已暂存: $Out" -ForegroundColor Green
