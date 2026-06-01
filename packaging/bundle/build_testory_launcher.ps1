# 构建 Testory.exe 启动器并复制到离线发布目录
param(
    [Parameter(Mandatory = $true)][string] $Root,
    [Parameter(Mandatory = $true)][string] $ReleaseDir
)

$ErrorActionPreference = "Stop"
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = Join-Path $Root ".venv\python.exe"
}
if (-not (Test-Path $py)) {
    throw "Build Python not found under $Root\.venv"
}

$icon = Join-Path $Root "packaging\inno\testory.ico"
if (-not (Test-Path $icon)) {
    throw "未找到 testory.ico，请先运行 packaging\generate_brand_icons.py"
}

Write-Host "[launcher] 安装 PyInstaller（构建机）..."
& $py -m pip install -q pyinstaller

$spec = Join-Path $Root "packaging\pyinstaller\testory_launcher.spec"
$distOut = Join-Path $Root "dist"
$workOut = Join-Path $Root "dist\_pyi_work"
New-Item -ItemType Directory -Force -Path $distOut, $workOut | Out-Null

Write-Host "[launcher] 编译 Testory.exe ..."
# 必须在非项目根目录运行，否则本地 packaging/ 包会遮蔽 PyInstaller 依赖的 packaging 模块
Push-Location $env:TEMP
try {
    & $py -m PyInstaller --noconfirm --clean `
        --distpath $distOut `
        --workpath $workOut `
        $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败，退出码 $LASTEXITCODE" }
} finally {
    Pop-Location
}

$builtExe = Join-Path $Root "dist\Testory.exe"
if (-not (Test-Path $builtExe)) {
    throw "未生成 dist\Testory.exe"
}

$release = Join-Path $Root $ReleaseDir
Copy-Item -Path $builtExe -Destination (Join-Path $release "Testory.exe") -Force

$appIco = Join-Path $Root "static\brand\app.ico"
if (Test-Path $appIco) {
    Copy-Item -Path $appIco -Destination (Join-Path $release "Testory.ico") -Force
} else {
    Copy-Item -Path $icon -Destination (Join-Path $release "Testory.ico") -Force
}

$cmdSrc = Join-Path $Root "packaging\Testory.cmd"
if (Test-Path $cmdSrc) {
    Copy-Item -Path $cmdSrc -Destination (Join-Path $release "packaging\Testory.cmd") -Force
}

$mb = [math]::Round((Get-Item (Join-Path $release "Testory.exe")).Length / 1MB, 2)
Write-Host "[launcher] 已写入 $ReleaseDir\Testory.exe ($mb MB) 与 Testory.ico" -ForegroundColor Green
