# 准备「用户零配置」离线发布目录 dist\uat_release（Python + 依赖 + Chromium + 配置模板）
param(
    [string] $Root = "",
    [string] $OutDir = "dist\uat_release"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

# 若在 dist\...\uat_release 副本内误运行，自动转发到项目根目录的真实脚本
if ($PSScriptRoot -match '[\\/]dist[\\/]') {
    $walk = $PSScriptRoot
    while ($walk) {
        if ((Split-Path $walk -Leaf) -eq 'dist') {
            $realRoot = Split-Path $walk -Parent
            $realScript = Join-Path $realRoot "packaging\bundle\prepare_offline_release.ps1"
            if (Test-Path $realScript) {
                Write-Host "检测到在 dist 发布目录内运行，已转发到项目根目录脚本..." -ForegroundColor Yellow
                Write-Host "  $realScript" -ForegroundColor DarkGray
                & $realScript -Root $realRoot -OutDir $OutDir
                exit $LASTEXITCODE
            }
        }
        $parent = Split-Path $walk -Parent
        if (-not $parent -or $parent -eq $walk) { break }
        $walk = $parent
    }
    throw @"
请勿在 dist\uat_release 目录内运行此脚本（那是旧副本）。
请执行:
  cd D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform
  .\packaging\build_desktop_installer.ps1
"@
}

. (Join-Path (Split-Path $PSScriptRoot -Parent) "tools\Get-ProjectRoot.ps1")
if (-not $Root) {
    $Root = Get-ProjectRoot -StartPath $PSScriptRoot
} else {
    $Root = (Resolve-Path $Root).Path
}
Set-Location $Root
Write-Host "项目根目录: $Root" -ForegroundColor DarkGray

Write-Host "=== 准备离线发布目录 ===" -ForegroundColor Cyan

# 清理此前在错误目录下生成的嵌套 dist
$badNested = Join-Path (Join-Path $Root $OutDir) "dist"
if (Test-Path $badNested) {
    Write-Host "清理错误的嵌套目录: $badNested" -ForegroundColor Yellow
    Remove-Item -Recurse -Force $badNested
}

$rootVenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$BuildPython = $null
if (Test-Path $rootVenvPy) {
    $BuildPython = (Resolve-Path $rootVenvPy).Path
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $BuildPython = (Get-Command python).Source
} else {
    throw "构建机请先安装 Python 3.10+，或在本项目根目录执行: python -m venv .venv"
}

if (-not (Test-Path $rootVenvPy)) {
    & $BuildPython -m venv (Join-Path $Root ".venv")
}
$py = (Resolve-Path $rootVenvPy).Path

Write-Host "[0/7] Generate app icons..."
& $py -m pip install -q Pillow
& $py "$Root\packaging\generate_brand_icons.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[1/7] Install build dependencies (project .venv)..."
& $py -m pip install -q -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed: requirements.txt" }
& $py -m pip install -q -r (Join-Path $Root "requirements-windows.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed: requirements-windows.txt" }
& $py -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed" }

Write-Host "[2/7] 复制程序文件..."
& "$Root\packaging\enterprise\stage_release.ps1" -Root $Root -OutDir $OutDir
$release = Join-Path $Root $OutDir

Write-Host "[3/7] Create portable Python in release dir..."
& "$Root\packaging\bundle\Ensure-PortablePython.ps1" `
    -ReleaseDir $release `
    -BuildPython $BuildPython `
    -ProjectRoot $Root | Out-Null
$rpy = Join-Path $release ".venv\python.exe"
if (-not (Test-Path $rpy)) {
    throw "Portable Python missing: $rpy"
}

Write-Host "[4/7] 内置 Playwright Chromium（用户无需 playwright install）..."
& $rpy -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "playwright install chromium 失败"
}

$srcBrowsers = Join-Path $env:LOCALAPPDATA "ms-playwright"
$destBrowsers = Join-Path $release "playwright-browsers"
if (Test-Path $destBrowsers) { Remove-Item -Recurse -Force $destBrowsers }
if (Test-Path $srcBrowsers) {
    Copy-Item -Path $srcBrowsers -Destination $destBrowsers -Recurse -Force
    Write-Host "  已复制浏览器到 playwright-browsers/"
} else {
    Write-Warning "未找到 ms-playwright，请检查 playwright install 是否成功"
}

Write-Host "[5/7] 生成默认配置..."
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

Write-Host "[6/7] 下载 WebView2 到 dist\redist（将打入安装包）..."
& "$Root\packaging\bundle\Ensure-WebView2.ps1" -ProjectRoot $Root

Write-Host "[7/7] 构建 Testory.exe 启动器..."
& "$Root\packaging\bundle\build_testory_launcher.ps1" -Root $Root -ReleaseDir $OutDir

foreach ($must in @(
    (Join-Path $release "Testory.exe"),
    (Join-Path $release "Testory.ico"),
    (Join-Path $release "TestoryShell.exe"),
    (Join-Path $release ".venv\python.exe"),
    (Join-Path $release "packaging\uat_desktop.py"),
    (Join-Path $release "static\brand\app.ico")
)) {
    if (-not (Test-Path $must)) {
        throw "发布目录缺少关键文件: $must"
    }
}

Write-Host ""
Write-Host "离线发布目录就绪: $release" -ForegroundColor Green
Write-Host "下一步: 在项目根目录运行 .\packaging\build_desktop_installer.ps1"
