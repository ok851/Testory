# Copy runtime files to release directory for Inno Setup packaging (Legacy mode)
param(
    [string] $OutDir = 'dist\uat_release',
    [string] $Root = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
)

$ErrorActionPreference = "Stop"
$Out = Join-Path $Root $OutDir

function Stop-ReleasePythonProcesses {
    param([string] $ReleasePath)
    if (-not (Test-Path $ReleasePath)) { return }
    $needle = $ReleasePath.TrimEnd('\').ToLowerInvariant()
    foreach ($name in @("python", "pythonw")) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $procPath = $_.Path
                if ($procPath -and $procPath.ToLowerInvariant().StartsWith($needle)) {
                    Write-Host "  stopping $($_.ProcessName) ($procPath)" -ForegroundColor Yellow
                    Stop-Process -Id $_.Id -Force -ErrorAction Stop
                }
            } catch {
            }
        }
    }
}

function Clear-ReleaseDirectory {
    param([string] $Path)
    if (-not (Test-Path $Path)) { return }

    Stop-ReleasePythonProcesses -ReleasePath $Path
    Start-Sleep -Milliseconds 500

    for ($i = 0; $i -lt 5; $i++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($i -ge 4) { break }
            Write-Host "  retry cleanup ($($i + 1)/5): $($_.Exception.Message)" -ForegroundColor DarkYellow
            Stop-ReleasePythonProcesses -ReleasePath $Path
            Start-Sleep -Seconds 2
        }
    }

    $stale = "${Path}.stale.$([DateTime]::Now.ToString('yyyyMMddHHmmss'))"
    Write-Host "  directory locked; moving aside: $stale" -ForegroundColor Yellow
    Move-Item -LiteralPath $Path -Destination $stale -Force
}

Clear-ReleaseDirectory -Path $Out
New-Item -ItemType Directory -Path $Out -Force | Out-Null

$exclude = @(
    ".git", ".venv", "__pycache__", ".pytest_cache",
    "dist", "node_modules", "screenshots", "videos", "logs",
    "mobile_assistant_apk_v2", "docs", "jenkins",
    "data", "mobile_sync", "%UAT_DATA_DIR%",
    "examples", "browser_extension",
    "build", "enterprise.key", "license.key"
)

Get-ChildItem -Path $Root -Force | Where-Object {
    $_.Name -notin $exclude -and $_.Name -notlike ".*"
} | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $Out -Recurse -Force
}

@("data", "logs", "screenshots") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $Out $_) | Out-Null
}

Write-Host "Staged to: $Out" -ForegroundColor Green
Write-Host "Next: create .venv, playwright install, then run Inno Setup."
