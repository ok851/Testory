# 清理构建/运行产生的临时文件（不删除源码）
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
@(
    ".pytest_cache", "__pycache__", "screenshots", "logs\*.log",
    "dist\uat_release", "dist\redist", "packaging\tools\InnoSetup-*"
) | ForEach-Object {
    Get-ChildItem -Path $_ -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "已清理临时产物（保留 dist\uat_platform_setup.exe 若存在）"
