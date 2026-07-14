# 准备「用户零配置」离线发布目录 dist\uat_release（Python + 依赖 + Chromium + 配置模板）
param(
    [string] $Root = "",
    [string] $OutDir = 'dist\uat_release',
    [switch] $Legacy
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

if ($Legacy) {
    Write-Host "=== 准备离线发布目录（Legacy：含明文 .py）===" -ForegroundColor Yellow
} else {
    Write-Host "=== 准备离线发布目录（Protected：PyInstaller onedir）===" -ForegroundColor Cyan
}

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

Write-Host "[0/8] Generate app icons..."
& $py -m pip install -q Pillow cairosvg
& $py "$Root\packaging\generate_brand_icons.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[0b/8] Fetch offline frontend vendors (Tailwind / Font Awesome / SweetAlert2)..."
& $py "$Root\packaging\fetch_frontend_vendors.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[1/8] Install build dependencies (project .venv)..."
& $py -m pip install -q -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed: requirements.txt" }
& $py -m pip install -q -r (Join-Path $Root "requirements-windows.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed: requirements-windows.txt" }
& $py -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed" }

Write-Host "[2/8] 复制程序文件..."
$release = Join-Path $Root $OutDir
if ($Legacy) {
    & "$Root\packaging\enterprise\stage_release.ps1" -Root $Root -OutDir $OutDir
} else {
    & "$Root\packaging\bundle\stage_release_assets.ps1" -Root $Root -OutDir $OutDir
}

if (-not $Legacy) {
    Write-Host "[2b/8] PyInstaller onedir（业务代码不进安装目录明文 .py）..."
    & "$Root\packaging\bundle\build_testory_onedir.ps1" -Root $Root -ReleaseDir $OutDir
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "[3/8] Create portable Python in release dir..."
& "$Root\packaging\bundle\Ensure-PortablePython.ps1" `
    -ReleaseDir $release `
    -BuildPython $BuildPython `
    -ProjectRoot $Root | Out-Null
$rpy = Join-Path $release ".venv\python.exe"
if (-not (Test-Path $rpy)) {
    throw "Portable Python missing: $rpy"
}

Write-Host "[4/8] 内置 Playwright Chromium（用户无需 playwright install）..."
& $rpy -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "playwright install chromium 失败"
}

$srcBrowsers = Join-Path $env:LOCALAPPDATA "ms-playwright"
$destBrowsers = Join-Path $release "playwright-browsers"
if (Test-Path $destBrowsers) { Remove-Item -Recurse -Force $destBrowsers }
if (Test-Path $srcBrowsers) {
    New-Item -ItemType Directory -Force -Path $destBrowsers | Out-Null
    Get-ChildItem -Path $srcBrowsers -Directory | Where-Object { $_.Name -like "chromium-*" } | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination (Join-Path $destBrowsers $_.Name) -Recurse -Force
        Write-Host "  已复制浏览器: $($_.Name)"
    }
    $ffDir = Join-Path $srcBrowsers "firefox-*"
    if (Test-Path $ffDir) {
        Write-Host "  跳过 Firefox（仅保留 Chromium 即可）" -ForegroundColor DarkGray
    }
} else {
    Write-Warning "未找到 ms-playwright，请检查 playwright install 是否成功"
}

Write-Host "[5/8] 生成默认配置..."
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

Write-Host "[6/8] 下载 WebView2 到 dist\redist（将打入安装包）..."
& "$Root\packaging\bundle\Ensure-WebView2.ps1" -ProjectRoot $Root
$wvBootstrap = Join-Path $Root "dist\redist\webview2\MicrosoftEdgeWebview2Setup.exe"
if (-not (Test-Path $wvBootstrap)) {
    throw "WebView2 引导包未下载成功: $wvBootstrap"
}
$wvReleaseDir = Join-Path $release "redist\webview2"
New-Item -ItemType Directory -Force -Path $wvReleaseDir | Out-Null
Copy-Item -Path $wvBootstrap -Destination (Join-Path $wvReleaseDir "MicrosoftEdgeWebview2Setup.exe") -Force
Write-Host "  已复制 WebView2 到 $OutDir\redist\webview2\" -ForegroundColor DarkGray

Write-Host "[7/8] 构建 Testory.exe 启动器..."
& "$Root\packaging\bundle\build_testory_launcher.ps1" -Root $Root -ReleaseDir $OutDir

Write-Host "[7b/8] 发布目录自检（布局 + pywebview + 后端）..."
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
foreach ($must in $must) {
    if (-not (Test-Path $must)) {
        throw "发布目录缺少关键文件: $must"
    }
}

Write-Host ""
Write-Host "离线发布目录就绪: $release" -ForegroundColor Green
Write-Host "下一步: 在项目根目录运行 .\packaging\build_desktop_installer.ps1"
