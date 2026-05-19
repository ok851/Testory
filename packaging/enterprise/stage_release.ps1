# 将运行所需文件复制到发布目录，供 Inno Setup 打包
param(
    [string] $OutDir = "dist\uat_release",
    [string] $Root = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
)

$ErrorActionPreference = "Stop"
$Out = Join-Path $Root $OutDir
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Path $Out | Out-Null

$exclude = @(
    ".git", ".venv", "__pycache__", ".pytest_cache",
    "dist", "node_modules", "screenshots", "videos", "logs"
)

Get-ChildItem -Path $Root -Force | Where-Object {
    $_.Name -notin $exclude -and $_.Name -notlike ".*"
} | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $Out -Recurse -Force
}

# 确保空数据目录存在
@("data", "logs", "screenshots") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $Out $_) | Out-Null
}

Write-Host "已暂存到: $Out" -ForegroundColor Green
Write-Host "下一步: 在发布机上创建 .venv、playwright install，再运行 Inno Setup。"
