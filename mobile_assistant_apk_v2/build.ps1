# Build script for Testory Assistant v2
# Usage: powershell -File build.ps1
# Or:    pwsh build.ps1

param(
    [ValidateSet("Debug", "Release")]
    [string]$BuildType = "Debug",

    [switch]$Clean = $false,

    [switch]$Test = $false,

    [switch]$CopyToBundles = $false
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Push-Location $ProjectDir
try {
    if ($Clean) {
        Write-Host "[CLEAN] Cleaning project..." -ForegroundColor Yellow
        & ./gradlew clean --no-daemon
    }

    if ($Test) {
        Write-Host "[TEST] Running unit tests..." -ForegroundColor Yellow
        & ./gradlew test --no-daemon
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed"
        }
    }

    $task = if ($BuildType -eq "Release") { "assembleRelease" } else { "assembleDebug" }
    Write-Host "[BUILD] Building $task..." -ForegroundColor Yellow
    & ./gradlew $task --no-daemon
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed"
    }

    $apkDir = if ($BuildType -eq "Release") {
        "app\build\outputs\apk\release"
    } else {
        "app\build\outputs\apk\debug"
    }
    $apkFile = Get-ChildItem "$apkDir\*.apk" | Select-Object -First 1

    Write-Host "[SUCCESS] APK built: $($apkFile.FullName)" -ForegroundColor Green
    Write-Host "  Size: $([math]::Round($apkFile.Length / 1MB, 2)) MB" -ForegroundColor Green

    if ($CopyToBundles) {
        $bundlesDir = Join-Path $ProjectDir "..\config\plugin_bundles"
        New-Item -ItemType Directory -Force -Path $bundlesDir | Out-Null
        $dest = Join-Path $bundlesDir "testory-assistant.apk"
        Copy-Item -Path $apkFile.FullName -Destination $dest -Force
        Write-Host "[COPY] APK copied to $dest" -ForegroundColor Green
    }
}
catch {
    Write-Host "[FAILED] $_" -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}
