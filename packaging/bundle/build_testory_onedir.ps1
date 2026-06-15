# 编译保护版 PyInstaller onedir（后端 + 网关辅助进程）
param(
    [Parameter(Mandatory = $true)][string] $Root,
    [Parameter(Mandatory = $true)][string] $ReleaseDir
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

function Stop-TestoryRuntimeProcesses {
    param([string[]] $PathPrefixes)
    $needles = @()
    foreach ($p in $PathPrefixes) {
        if ($p -and (Test-Path $p)) {
            $needles += (Resolve-Path $p).Path.TrimEnd('\').ToLowerInvariant()
        }
    }
    if (-not $needles.Count) { return }

    $exeNames = @(
        "TestoryBackend", "TestoryEmbeddedGw", "TestoryBrowserRuntime", "TestoryHermesGw", "TestoryDesktopGw", "TestoryMobileGw",
        "Testory", "TestoryShell", "python", "pythonw"
    )
    foreach ($name in $exeNames) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $procPath = $_.Path
                if (-not $procPath) { return }
                $procPath = $procPath.ToLowerInvariant()
                foreach ($needle in $needles) {
                    if ($procPath.StartsWith($needle)) {
                        Write-Host "  结束占用进程: $($_.ProcessName) ($procPath)" -ForegroundColor Yellow
                        Stop-Process -Id $_.Id -Force -ErrorAction Stop
                        break
                    }
                }
            } catch {
            }
        }
    }
}

function Clear-LockedDirectory {
    param([string] $Path)
    if (-not (Test-Path $Path)) { return }

    $parent = Split-Path $Path -Parent
    Stop-TestoryRuntimeProcesses -PathPrefixes @($Path, $parent, (Join-Path $Root "dist"))

    for ($i = 0; $i -lt 5; $i++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($i -ge 4) { break }
            Write-Host "  清理重试 ($($i + 1)/5): $($_.Exception.Message)" -ForegroundColor DarkYellow
            Stop-TestoryRuntimeProcesses -PathPrefixes @($Path, $parent, (Join-Path $Root "dist"))
            Start-Sleep -Seconds 2
        }
    }

    $stale = "${Path}.stale.$([DateTime]::Now.ToString('yyyyMMddHHmmss'))"
    Write-Host "  目录仍被占用，已改名为: $stale" -ForegroundColor Yellow
    Move-Item -LiteralPath $Path -Destination $stale -Force
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "请先在项目根目录创建 .venv 并安装依赖：python -m venv .venv; pip install -r requirements.txt"
}

Write-Host "=== PyInstaller onedir（保护版后端）===" -ForegroundColor Cyan
$releaseEarly = Join-Path $Root $ReleaseDir
Stop-TestoryRuntimeProcesses -PathPrefixes @(
    (Join-Path $releaseEarly "runtime"),
    $releaseEarly,
    (Join-Path $Root "dist")
)
Start-Sleep -Milliseconds 400

& $py -m pip install -q pyinstaller

$specDir = Join-Path $Root "packaging\pyinstaller"
$distOut = Join-Path $Root "dist"
$workOut = Join-Path $Root "dist\_pyi_work"
New-Item -ItemType Directory -Force -Path $distOut, $workOut | Out-Null

$specs = @(
    "testory_backend.spec",
    "testory_browser_runtime.spec",
    "testory_embedded_gw.spec",
    "testory_hermes_gw.spec",
    "testory_desktop_gw.spec",
    "testory_mobile_gw.spec"
)

Push-Location $env:TEMP
try {
    foreach ($spec in $specs) {
        Write-Host "[pyi] $spec ..." -ForegroundColor DarkGray
        & $py -m PyInstaller --noconfirm --clean `
            --distpath $distOut `
            --workpath $workOut `
            (Join-Path $specDir $spec)
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller 失败: $spec (exit $LASTEXITCODE)"
        }
    }
} finally {
    Pop-Location
}

$release = Join-Path $Root $ReleaseDir
$runtime = Join-Path $release "runtime"
Clear-LockedDirectory -Path $runtime
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

function Copy-Onedir($srcName, $destName) {
    $src = Join-Path $distOut $srcName
    if (-not (Test-Path $src)) {
        throw "未找到 PyInstaller 产物: $src"
    }
    $dest = Join-Path $runtime $destName
    Copy-Item -Path $src -Destination $dest -Recurse -Force
    Write-Host "  已复制 $srcName -> runtime\$destName" -ForegroundColor Green
}

Copy-Onedir "testory_app" "testory_app"
Copy-Onedir "TestoryBrowserRuntime" "TestoryBrowserRuntime"
Copy-Onedir "TestoryEmbeddedGw" "TestoryEmbeddedGw"
Copy-Onedir "TestoryHermesGw" "TestoryHermesGw"
Copy-Onedir "TestoryDesktopGw" "TestoryDesktopGw"
Copy-Onedir "TestoryMobileGw" "TestoryMobileGw"

$backend = Join-Path $runtime "testory_app\TestoryBackend.exe"
if (-not (Test-Path $backend)) {
    throw "缺少 TestoryBackend.exe"
}

# 安装根目录不应再保留明文业务 .py
$stripCount = 0
Get-ChildItem -Path $release -Filter "*.py" -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
    $rel = $_.FullName.Substring($release.Length + 1)
    if ($rel -match '^packaging\\') { return }
    if ($rel -eq "desktop_user_data.py") { return }
    if ($rel -eq "install_paths.py") { return }
    Remove-Item -LiteralPath $_.FullName -Force
    $stripCount++
}
if ($stripCount -gt 0) {
    Write-Host "  已移除发布目录中 $stripCount 个明文 .py（保留 packaging\ 壳层）" -ForegroundColor DarkGray
}

Write-Host "保护版 onedir 已写入 $runtime" -ForegroundColor Green
