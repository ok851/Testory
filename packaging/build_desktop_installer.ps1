# 一键生成「用户零配置」桌面安装包（自动下载 Inno Setup、内置 Python/Chromium/WebView2）
# 在项目根目录执行:  .\packaging\build_desktop_installer.ps1
#
# 说明:
#   - Inno Setup 仅在本机构建时使用，不会打进给用户的安装包
#   - 用户只需运行 dist\uat_platform_setup.exe，无需 Python / 浏览器 / 环境变量

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " HuFirst UAT 全量离线安装包构建" -ForegroundColor Cyan
Write-Host " （体积较大属正常，用户侧零下载）" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

& "$Root\packaging\bundle\prepare_offline_release.ps1"

$iscc = & "$Root\packaging\tools\Ensure-InnoSetup.ps1"
& $iscc "$Root\packaging\inno\uat_platform.iss"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$setup = Join-Path $Root "dist\uat_platform_setup.exe"
if (Test-Path $setup) {
    $mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Write-Host ""
    Write-Host "构建完成: $setup  (约 $mb MB)" -ForegroundColor Green
    Write-Host "发给用户仅此一个文件；无需 Inno、Python、WebView2 手动安装。" -ForegroundColor Green
} else {
    Write-Host "未找到输出文件，请检查 Inno 日志。" -ForegroundColor Red
    exit 1
}
