# Prepare offline release directory for Testory installer
param(
    [string] $Root = "",
    [string] $OutDir = 'dist\uat_release',
    [switch] $Legacy,
    [switch] $Lite
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

. (Join-Path (Split-Path $PSScriptRoot -Parent) "tools\Get-ProjectRoot.ps1")
if (-not $Root) {
    $Root = Get-ProjectRoot -StartPath $PSScriptRoot
} else {
    $Root = (Resolve-Path $Root).Path
}
Set-Location $Root
Write-Host "Project root: $Root" -ForegroundColor DarkGray

Write-Host "[0/8] Generate app icons..."
$rootVenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$BuildPython = $null
if (Test-Path $rootVenvPy) {
    $BuildPython = (Resolve-Path $rootVenvPy).Path
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $BuildPython = (Get-Command python).Source
} else {
    throw "Please install Python 3.10+ first, or run: python -m venv .venv"
}

if (-not (Test-Path $rootVenvPy)) {
    & $BuildPython -m venv (Join-Path $Root ".venv")
}
$py = (Resolve-Path $rootVenvPy).Path

& $py -m pip install -q Pillow cairosvg
& $py "$Root\packaging\generate_brand_icons.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[0b/8] Fetch offline frontend vendors..."
& $py "$Root\packaging\fetch_frontend_vendors.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[1/8] Install build dependencies..."
& $py -m pip install -q -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed: requirements.txt" }
& $py -m pip install -q -r (Join-Path $Root "requirements-windows.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed: requirements-windows.txt" }
& $py -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed" }

Write-Host "[2/8] Copy program files..."
$release = Join-Path $Root $OutDir
if ($Legacy) {
    & "$Root\packaging\enterprise\stage_release.ps1" -Root $Root -OutDir $OutDir
} else {
    & "$Root\packaging\bundle\stage_release_assets.ps1" -Root $Root -OutDir $OutDir
}

if (-not $Legacy) {
    Write-Host "[2b/8] PyInstaller onedir..."
    & "$Root\packaging\bundle\build_testory_onedir.ps1" -Root $Root -ReleaseDir $OutDir
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "[3/8] Create portable Python in release dir..."
$ensureArgs = @{
    ReleaseDir = $release
    BuildPython = $BuildPython
    ProjectRoot = $Root
}
if ($Lite) { $ensureArgs["Lite"] = $true }
if (-not $Legacy) { $ensureArgs["Protected"] = $true }
& "$Root\packaging\bundle\Ensure-PortablePython.ps1" @ensureArgs | Out-Null
$rpy = Join-Path $release ".venv\python.exe"
if (-not (Test-Path $rpy)) {
    throw "Portable Python missing: $rpy"
}

Write-Host "[4/8] Bundle Playwright Chromium..."
if ($Lite) {
    Write-Host "  Lite mode: skip Chromium bundling, auto-download on first use" -ForegroundColor DarkYellow
} else {
    & $rpy -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        throw "playwright install chromium failed"
    }

    $srcBrowsers = Join-Path $env:LOCALAPPDATA "ms-playwright"
    $destBrowsers = Join-Path $release "playwright-browsers"
    if (Test-Path $destBrowsers) { Remove-Item -Recurse -Force $destBrowsers }
    if (Test-Path $srcBrowsers) {
        New-Item -ItemType Directory -Force -Path $destBrowsers | Out-Null
        Get-ChildItem -Path $srcBrowsers -Directory | Where-Object { $_.Name -like "chromium-*" } | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination (Join-Path $destBrowsers $_.Name) -Recurse -Force
            Write-Host "  Copied browser: $($_.Name)"
        }
    } else {
        Write-Warning "ms-playwright not found, check playwright install"
    }
}

Write-Host "[5/8] Generate default config..."
$envFile = Join-Path $release ".env"
$envExample = Join-Path $release ".env.example"
if (-not (Test-Path $envFile) -and (Test-Path $envExample)) {
    Copy-Item $envExample $envFile
}
@("data", "logs", "screenshots") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $release $_) | Out-Null
}

$ver = "1.0.0"
$verFile = Join-Path $release "packaging\APP_VERSION.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $verFile) | Out-Null
Set-Content -Path $verFile -Value $ver -Encoding utf8

Write-Host "[6/8] Download WebView2..."
& "$Root\packaging\bundle\Ensure-WebView2.ps1" -ProjectRoot $Root
$wvBootstrap = Join-Path $Root "dist\redist\webview2\MicrosoftEdgeWebview2Setup.exe"
if (-not (Test-Path $wvBootstrap)) {
    throw "WebView2 bootstrap not found: $wvBootstrap"
}
$wvReleaseDir = Join-Path $release "redist\webview2"
New-Item -ItemType Directory -Force -Path $wvReleaseDir | Out-Null
Copy-Item -Path $wvBootstrap -Destination (Join-Path $wvReleaseDir "MicrosoftEdgeWebview2Setup.exe") -Force
Write-Host "  WebView2 copied to $OutDir\redist\webview2\" -ForegroundColor DarkGray

Write-Host "[7/8] Build Testory.exe launcher..."
& "$Root\packaging\bundle\build_testory_launcher.ps1" -Root $Root -ReleaseDir $OutDir

Write-Host "[7b/8] Verify release layout..."
& "$Root\packaging\bundle\verify_install_release.ps1" -ReleaseDir $OutDir -Root $Root
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$must = @(
    (Join-Path $release "Testory.exe"),
    (Join-Path $release "Testory.ico"),
    (Join-Path $release "TestoryShell.exe"),
    (Join-Path $release ".venv\python.exe"),
    (Join-Path $release "packaging\uat_desktop.py"),
    (Join-Path $release "packaging\launch_checks.py"),
    (Join-Path $release "static\brand\app.ico"),
    (Join-Path $release "static\vendor\tailwindcss\tailwind.min.js"),
    (Join-Path $release ".venv\pythonw.exe")
)
if (-not $Legacy) {
    $must += @(
        (Join-Path $release "runtime\testory_app\TestoryBackend.exe"),
        (Join-Path $release "install_paths.py")
    )
}
foreach ($m in $must) {
    if (-not (Test-Path $m)) {
        throw "Missing critical file: $m"
    }
}

Write-Host ""
Write-Host "Release directory ready: $release" -ForegroundColor Green
Write-Host "Next: run .\packaging\build_desktop_installer.ps1"
