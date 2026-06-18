# Export monorepo into three standalone project directories.
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination,

    [switch]$InitGit
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dest = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

function Copy-Tree($From, $To, [string[]]$ExcludeNames = @()) {
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Get-ChildItem -LiteralPath $From -Force | ForEach-Object {
        if ($ExcludeNames -contains $_.Name) { return }
        $target = Join-Path $To $_.Name
        if ($_.PSIsContainer) {
            Copy-Tree $_.FullName $target $ExcludeNames
        } else {
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
}

function Ensure-EnvExample($ProjectDir) {
    $example = Join-Path $ProjectDir ".env.example"
    $envFile = Join-Path $ProjectDir ".env"
    if ((Test-Path $example) -and -not (Test-Path $envFile)) {
        Copy-Item $example $envFile
        Write-Host "  created .env from .env.example"
    }
}

function Write-ProjectReadme($Path, [string]$Title, [string]$StartCmd, [string]$Port) {
    @"
# $Title

从 Testory monorepo 导出的独立项目。

## 启动

``````powershell
pip install -r requirements.txt
$StartCmd
``````

默认端口 **$Port**。配置见 ``.env``（由 ``.env.example`` 复制而来，请修改密钥）。

## 跨项目密钥

与主平台、其它子项目对齐：

- ``PLATFORM_ADMIN_SECRET``（官网、控制面、主平台 ``platform_sync`` 需一致）
- 生产环境 ``PLATFORM_ADMIN_URL`` / ``WEBSITE_URL`` 用内网或公网域名，勿写 ``127.0.0.1``

详见 monorepo ``docs/PROJECT_SPLIT.md``。
"@ | Set-Content -Path (Join-Path $Path "README.md") -Encoding UTF8
}

$commonSrc = Join-Path $Root "packages\testory_common"
$commonDestName = "testory_common"

# 1) Website
Write-Host "Exporting testory-website..."
$webOut = Join-Path $Dest "testory-website"
Copy-Tree (Join-Path $Root "projects\testory-website") $webOut
Copy-Tree $commonSrc (Join-Path $webOut $commonDestName)
Ensure-EnvExample $webOut
Write-ProjectReadme $webOut "Testory 产品官网" "python app.py" "5200"

# 2) Platform admin
Write-Host "Exporting testory-platform-admin..."
$adminOut = Join-Path $Dest "testory-platform-admin"
Copy-Tree (Join-Path $Root "projects\testory-platform-admin") $adminOut
Copy-Tree $commonSrc (Join-Path $adminOut $commonDestName)
Ensure-EnvExample $adminOut
Write-ProjectReadme $adminOut "Testory 创始人控制面" "python app.py" "5100"

# 3) Main platform
Write-Host "Exporting testory (main platform)..."
$mainOut = Join-Path $Dest "testory"
$exclude = @(
    "projects", "website", "platform_admin", "packages", ".git", ".venv", "node_modules",
    "src-tauri\target", "mobile_assistant_apk\app\build", ".npm-cache", ".env"
)
Copy-Tree $Root $mainOut $exclude
Copy-Tree $commonSrc (Join-Path $mainOut "packages\testory_common")

@"
# Testory 主平台

从 monorepo 导出的主应用（自动化 + Web UI + 可选 Tauri 桌面壳）。

## 启动

``````powershell
pip install -r requirements.txt
python app.py
``````

## 配置

复制根目录 ``.env.example``（若有）或从 monorepo 根 ``.env`` 中**只保留主平台相关变量**到本目录 ``.env``。

与官网/控制面联动时需设置：

- ``PLATFORM_ADMIN_URL`` — 控制面地址（生产用内网域名）
- ``WEBSITE_URL`` — 官网地址（支付跳转、下载页）
- ``PLATFORM_ADMIN_SECRET`` — 与另两个项目一致

## Tauri 桌面

``````powershell
npm install
npm run tauri dev
``````

详见 ``docs/TAURI_DESKTOP.md``。
"@ | Set-Content -Path (Join-Path $mainOut "README.md") -Encoding UTF8

if ($InitGit) {
    foreach ($dir in @($mainOut, $webOut, $adminOut)) {
        Push-Location $dir
        if (-not (Test-Path ".git")) {
            git init | Out-Null
            Write-Host "  git init in $dir"
        }
        Pop-Location
    }
}

Write-Host ""
Write-Host "Exported to:"
Write-Host "  $mainOut"
Write-Host "  $webOut"
Write-Host "  $adminOut"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit each project's .env (secrets must match across all three)"
Write-Host "  2. pip install -r requirements.txt in each folder"
Write-Host "  3. Start admin :5100, website :5200, main :5000"
Write-Host "  4. Optional: re-run with -InitGit to create three git repos"
