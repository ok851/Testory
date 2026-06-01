# Build full offline desktop installer (Inno Setup + portable Python + Chromium + WebView2)
# Run from project root:
#   .\packaging\build_desktop_installer.ps1
# Optional:
#   .\packaging\build_desktop_installer.ps1 -InnoOnly
#   .\packaging\build_desktop_installer.ps1 -IsccPath "D:\Inno Setup 6\ISCC.exe"

param(
    [string] $IsccPath = "",
    [switch] $InnoOnly
)

$ErrorActionPreference = "Stop"

if ($PSScriptRoot -match '[\\/]dist[\\/]') {
    $walk = $PSScriptRoot
    while ($walk) {
        if ((Split-Path $walk -Leaf) -eq 'dist') {
            $realRoot = Split-Path $walk -Parent
            $realScript = Join-Path $realRoot "packaging\build_desktop_installer.ps1"
            if (Test-Path $realScript) {
                Write-Host "Redirecting to project root script..." -ForegroundColor Yellow
                & $realScript
                exit $LASTEXITCODE
            }
        }
        $parent = Split-Path $walk -Parent
        if (-not $parent -or $parent -eq $walk) { break }
        $walk = $parent
    }
    throw "Run from project root: .\packaging\build_desktop_installer.ps1"
}

. "$PSScriptRoot\tools\Get-ProjectRoot.ps1"
$Root = Get-ProjectRoot -StartPath $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Testory offline installer build" -ForegroundColor Cyan
Write-Host " Project root: $Root" -ForegroundColor DarkGray
Write-Host " Large output size is expected (fully offline bundle)." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if (-not $InnoOnly) {
    & "$Root\packaging\bundle\prepare_offline_release.ps1" -Root $Root
} else {
    Write-Host " Skip prepare (-InnoOnly); using existing dist\uat_release" -ForegroundColor Yellow
    if (-not (Test-Path (Join-Path $Root "dist\uat_release\Testory.exe"))) {
        throw "dist\uat_release not ready. Run without -InnoOnly first."
    }
}

if ($IsccPath) {
    if (-not (Test-Path -LiteralPath $IsccPath)) {
        throw "ISCC not found: $IsccPath"
    }
    $env:INNO_SETUP_ISCC = (Resolve-Path -LiteralPath $IsccPath).Path
}

$iscc = & "$Root\packaging\tools\Ensure-InnoSetup.ps1" -ProjectRoot $Root
& $iscc "$Root\packaging\inno\uat_platform.iss"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$setup = Join-Path $Root "dist\testory_setup.exe"
if (Test-Path $setup) {
    $mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Done: $setup (about $mb MB)" -ForegroundColor Green
    Write-Host "Ship this single file to end users." -ForegroundColor Green
} else {
    $setupLegacy = Join-Path $Root "dist\uat_platform_setup.exe"
    if (Test-Path $setupLegacy) {
        Write-Host "Done: $setupLegacy" -ForegroundColor Green
    } else {
        Write-Host "Missing dist\testory_setup.exe - check Inno Setup log." -ForegroundColor Red
        exit 1
    }
}