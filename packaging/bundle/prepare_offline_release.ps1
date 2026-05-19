# 准备「用户零配置」离线发布目录 dist\uat_release（Python + 依赖 + Chromium + 配置模板）
param(
    [string] $Root = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string] $OutDir = "dist\uat_release"
)

$ErrorActionPreference = "Stop"
Set-Location $Root

Write-Host "=== 准备离线发布目录 ===" -ForegroundColor Cyan

# 构建机需要 Python 来「制作」安装包；最终用户不需要
$buildPy = $null
if (Test-Path ".\.venv\Scripts\python.exe") {
    $buildPy = ".\.venv\Scripts\python.exe"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $buildPy = "python"
} else {
    throw "构建机请先安装 Python 3.10+，或在本项目执行 python -m venv .venv"
}

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    & $buildPy -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"
$pip = ".\.venv\Scripts\pip.exe"

Write-Host "[1/6] 安装构建依赖..."
& $pip install -q -r requirements.txt
& $pip install -q -r requirements-windows.txt
& $py -m playwright install chromium

Write-Host "[2/6] 复制程序文件..."
& "$Root\packaging\enterprise\stage_release.ps1" -OutDir $OutDir
$release = Join-Path $Root $OutDir

Write-Host "[3/6] 在安装目录内创建独立 Python 环境（用户机无需安装 Python）..."
Set-Location $release
if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
& $buildPy -m venv .venv
$rpy = ".\.venv\Scripts\python.exe"
$rpip = ".\.venv\Scripts\pip.exe"
& $rpip install --upgrade pip wheel -q
& $rpip install -q -r requirements.txt
& $rpip install -q -r requirements-windows.txt

Write-Host "[4/6] 内置 Playwright Chromium（用户无需 playwright install）..."
& $rpy -m playwright install chromium
$srcBrowsers = Join-Path $env:LOCALAPPDATA "ms-playwright"
$destBrowsers = Join-Path $release "playwright-browsers"
if (Test-Path $destBrowsers) { Remove-Item -Recurse -Force $destBrowsers }
if (Test-Path $srcBrowsers) {
    Copy-Item -Path $srcBrowsers -Destination $destBrowsers -Recurse -Force
    Write-Host "  已复制浏览器到 playwright-browsers/"
} else {
    Write-Warning "未找到 ms-playwright，请检查 playwright install 是否成功"
}

Write-Host "[5/6] 生成默认配置..."
$envFile = Join-Path $release ".env"
if (-not (Test-Path $envFile) -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" $envFile
}
@("data", "logs", "screenshots") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $release $_) | Out-Null
}

# 标记版本供安装包读取
$ver = "1.0.0"
$verFile = Join-Path $release "packaging\APP_VERSION.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $verFile) | Out-Null
Set-Content -Path $verFile -Value $ver -Encoding utf8

Set-Location $Root
Write-Host "[6/6] 下载 WebView2 到 dist\redist（将打入安装包）..."
& "$Root\packaging\bundle\Ensure-WebView2.ps1"

Write-Host ""
Write-Host "离线发布目录就绪: $release" -ForegroundColor Green
Write-Host "下一步: 运行 packaging\build_desktop_installer.ps1 生成 uat_platform_setup.exe"
