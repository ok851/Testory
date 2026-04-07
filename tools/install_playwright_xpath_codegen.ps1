# 在本机一键安装并构建 playwright-xpath-fork（XPath 优先 Codegen），适合开发者自用。
# 需要：Node.js 18+、npm，项目在磁盘上的完整克隆。
# 用法（PowerShell，项目根目录）:
#   .\tools\install_playwright_xpath_codegen.ps1
# 若仅需跳过 Electron 大文件下载（不做 Electron 测试时可开）:
#   .\tools\install_playwright_xpath_codegen.ps1 -SkipElectronBinary

param(
    [switch]$SkipElectronBinary
)

$ErrorActionPreference = "Stop"
# 本脚本位于仓库 tools\ 下，上一级为项目根目录
$Root = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $Root "playwright-xpath-fork\package.json"))) {
    Write-Error "未找到 playwright-xpath-fork。请在仓库根目录执行: .\tools\install_playwright_xpath_codegen.ps1"
}

$Fork = Join-Path $Root "playwright-xpath-fork"
Set-Location $Fork

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 node，请先安装 Node.js 18+（https://nodejs.org/）"
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 npm"
}

if ($SkipElectronBinary) {
    $env:ELECTRON_SKIP_BINARY_DOWNLOAD = "1"
    Write-Host "已设置 ELECTRON_SKIP_BINARY_DOWNLOAD=1（跳过 Electron 运行时下载，仅够构建 Codegen）" -ForegroundColor Cyan
}

Write-Host "正在 npm ci（可能较慢，请勿中断）..." -ForegroundColor Yellow
npm ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "正在 npm run build（可能数分钟）..." -ForegroundColor Yellow
npm run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Index = Join-Path $Fork "packages\playwright-core\lib\vite\recorder\index.html"
if (-not (Test-Path $Index)) {
    Write-Error "构建后未找到 lib/vite/recorder/index.html，请查看 npm run build 的报错日志"
}

Write-Host ""
Write-Host "完成。XPath 优先 Codegen 可用。示例：" -ForegroundColor Green
Write-Host "  cd `"$Fork`""
Write-Host "  node packages/playwright-core/cli.js codegen https://你的地址 --target python"
Write-Host "平台「启动 Playwright Codegen」在检测到上述文件后会自动走该 CLI。"
