# 一键生成「用户零配置」桌面安装包（自动下载 Inno Setup、内置 Python/Chromium/WebView2）
# 必须在项目根目录执行:
#   cd D:\...\NewUITestPlatform\NewUITestPlatform
#   .\packaging\build_desktop_installer.ps1

$ErrorActionPreference = "Stop"

# 若在 dist 发布副本内误运行，转发到项目根目录
if ($PSScriptRoot -match '[\\/]dist[\\/]') {
    $walk = $PSScriptRoot
    while ($walk) {
        if ((Split-Path $walk -Leaf) -eq 'dist') {
            $realRoot = Split-Path $walk -Parent
            $realScript = Join-Path $realRoot "packaging\build_desktop_installer.ps1"
            if (Test-Path $realScript) {
                Write-Host "检测到在 dist 发布目录内运行，已转发到项目根目录..." -ForegroundColor Yellow
                & $realScript
                exit $LASTEXITCODE
            }
        }
        $parent = Split-Path $walk -Parent
        if (-not $parent -or $parent -eq $walk) { break }
        $walk = $parent
    }
    throw "请在项目根目录运行: .\packaging\build_desktop_installer.ps1"
}

. "$PSScriptRoot\tools\Get-ProjectRoot.ps1"
$Root = Get-ProjectRoot -StartPath $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Testory 全量离线安装包构建" -ForegroundColor Cyan
Write-Host " 项目根: $Root" -ForegroundColor DarkGray
Write-Host " （体积较大属正常，用户侧零下载）" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

& "$Root\packaging\bundle\prepare_offline_release.ps1" -Root $Root

$iscc = & "$Root\packaging\tools\Ensure-InnoSetup.ps1" -ProjectRoot $Root
& $iscc "$Root\packaging\inno\uat_platform.iss"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$setup = Join-Path $Root "dist\testory_setup.exe"
if (Test-Path $setup) {
    $mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Write-Host ""
    Write-Host "构建完成: $setup  (约 $mb MB)" -ForegroundColor Green
    Write-Host "发给用户仅此一个文件；无需 Inno、Python、WebView2 手动安装。" -ForegroundColor Green
} else {
    $setupLegacy = Join-Path $Root "dist\uat_platform_setup.exe"
    if (Test-Path $setupLegacy) {
        Write-Host "构建完成: $setupLegacy" -ForegroundColor Green
    } else {
        Write-Host "未找到输出文件 dist\testory_setup.exe，请检查 Inno 日志。" -ForegroundColor Red
        exit 1
    }
}
