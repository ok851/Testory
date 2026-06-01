# 将 Playwright 浏览器复制到发布目录，供 PLAYWRIGHT_BROWSERS_PATH 使用
param(
    [string] $VenvPath = ".\.venv",
    [string] $OutDir = "dist\uat_bundle",
    [string] $Browser = "chromium"
)

$ErrorActionPreference = "Stop"
$py = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $py)) { throw "未找到 venv: $py" }

Write-Host "playwright install $Browser ..."
& $py -m playwright install $Browser

$src = & $py -c "import pathlib; from playwright._impl._driver import compute_driver_executable; import os; print(pathlib.Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', pathlib.Path.home() / 'AppData/Local/ms-playwright')).resolve())" 2>$null
if (-not $src -or -not (Test-Path $src)) {
    $src = Join-Path $env:LOCALAPPDATA "ms-playwright"
}
if (-not (Test-Path $src)) {
    throw "未找到 Playwright 浏览器缓存，请先 playwright install"
}

$dest = Join-Path $OutDir "playwright-browsers"
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "复制 $src -> $dest"
Copy-Item -Path $src -Destination $dest -Recurse -Force
Write-Host "完成。构建 PyInstaller 时请指向 OutDir。"
