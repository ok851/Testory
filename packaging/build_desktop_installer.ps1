# Build full offline desktop installer (portable Python + Chromium + WebView2 + Inno Setup)
# Run from project root:
#   .\packaging\build_desktop_installer.ps1
#
# Options:
#   -IsccPath "D:\Inno Setup 6\ISCC.exe"   explicit ISCC (optional if Inno is on PATH or default paths)
#   -InnoOnly                              skip prepare; compile existing dist\uat_release only
#   -PrepareOnly                           prepare dist\uat_release only; do NOT call ISCC (manual compile in Inno GUI)
#   -Legacy                                ship plaintext .py (dev only); default is Protected onedir

param(
    [string] $IsccPath = "",
    [switch] $InnoOnly,
    [switch] $PrepareOnly,
    [switch] $Legacy
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
                & $realScript @PSBoundParameters
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

$iss = Join-Path $Root "packaging\inno\uat_platform.iss"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Testory offline installer build" -ForegroundColor Cyan
Write-Host " Project root: $Root" -ForegroundColor DarkGray
if ($PrepareOnly) {
    Write-Host " Mode: prepare release only (manual Inno compile)" -ForegroundColor Yellow
} elseif ($InnoOnly) {
    Write-Host " Mode: Inno compile only" -ForegroundColor Yellow
} else {
    Write-Host " Mode: prepare + Inno compile" -ForegroundColor DarkGray
}
if ($Legacy) {
    Write-Host " Packaging: Legacy (plaintext .py in install dir)" -ForegroundColor Yellow
} else {
    Write-Host " Packaging: Protected (PyInstaller onedir, default)" -ForegroundColor Green
}
Write-Host "========================================" -ForegroundColor Cyan

if (-not $InnoOnly) {
    if ($Legacy) {
        & "$Root\packaging\bundle\prepare_offline_release.ps1" -Root $Root -Legacy
    } else {
        & "$Root\packaging\bundle\prepare_offline_release.ps1" -Root $Root
    }
} else {
    Write-Host " Skip prepare (-InnoOnly); using existing dist\uat_release" -ForegroundColor Yellow
    if (-not (Test-Path (Join-Path $Root "dist\uat_release\Testory.exe"))) {
        throw "dist\uat_release not ready. Run without -InnoOnly first."
    }
    Write-Host " Running release verify on existing dist\uat_release ..." -ForegroundColor DarkGray
    & "$Root\packaging\bundle\verify_install_release.ps1" -ReleaseDir "dist\uat_release" -Root $Root
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($PrepareOnly) {
    Write-Host ""
    Write-Host "Release directory ready:" -ForegroundColor Green
    Write-Host "  $(Join-Path $Root 'dist\uat_release')" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next: open Inno Setup 6 and compile:" -ForegroundColor Cyan
    Write-Host "  $iss" -ForegroundColor White
    Write-Host "Output will be: $(Join-Path $Root 'dist\testory_setup.exe')" -ForegroundColor DarkGray
    exit 0
}

if ($IsccPath) {
    if (-not (Test-Path -LiteralPath $IsccPath)) {
        throw "ISCC not found: $IsccPath"
    }
    $IsccPath = (Resolve-Path -LiteralPath $IsccPath).Path
}

Write-Host ""
Write-Host "Compiling installer with Inno Setup..." -ForegroundColor Cyan
$iscc = & "$Root\packaging\tools\Ensure-InnoSetup.ps1" -ProjectRoot $Root -IsccPath $IsccPath
& $iscc $iss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$setup = Join-Path $Root "dist\testory_setup.exe"
$bin = Join-Path $Root "dist\testory_setup-1.bin"
if (Test-Path $setup) {
    $mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Done: $setup (about $mb MB)" -ForegroundColor Green
    if (Test-Path $bin) {
        Write-Host "Also ship: $bin" -ForegroundColor Green
    }
    if ($env:UAT_CODESIGN_THUMBPRINT -or $env:UAT_CODESIGN_PFX) {
        Write-Host ""
        Write-Host "Code signing installer (UAT_CODESIGN_* set)..." -ForegroundColor Cyan
        $signScript = Join-Path $Root "packaging\enterprise\sign_release.ps1"
        if (Test-Path $signScript) {
            & $signScript -FilePath $setup -Description "Testory Desktop Client"
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
            if (Test-Path (Join-Path $Root "dist\Testory.exe")) {
                & $signScript -FilePath (Join-Path $Root "dist\uat_release\Testory.exe") -Description "Testory Launcher" 2>$null
            }
        } else {
            Write-Warning "sign_release.ps1 not found; skip signing."
        }
    } else {
        Write-Host "Tip: set UAT_CODESIGN_THUMBPRINT or UAT_CODESIGN_PFX to auto-sign the installer." -ForegroundColor DarkGray
    }
} else {
    $setupLegacy = Join-Path $Root "dist\uat_platform_setup.exe"
    if (Test-Path $setupLegacy) {
        Write-Host "Done: $setupLegacy" -ForegroundColor Green
    } else {
        Write-Host "Missing dist\testory_setup.exe - check Inno Setup log." -ForegroundColor Red
        exit 1
    }
}
