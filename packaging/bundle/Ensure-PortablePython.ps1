# 在安装目录内创建可移植 Python 运行时（嵌入式发行版，不依赖构建机 Python 路径）
param(
    [Parameter(Mandatory = $true)][string] $ReleaseDir,
    [Parameter(Mandatory = $true)][string] $BuildPython,
    [string] $ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$pyVer = (& $BuildPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')").Trim()
$majorMinor = ($pyVer -split '\.')[0..1] -join '.'
$tag = "python$($majorMinor -replace '\.', '')"

$cacheDir = Join-Path $ProjectRoot "packaging\tools\cache"
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
$embedZipName = "python-$pyVer-embed-amd64.zip"
$zipPath = Join-Path $cacheDir $embedZipName
$embedUrls = @(
    "https://www.python.org/ftp/python/$pyVer/$embedZipName",
    "https://npm.taobao.org/mirrors/python/$pyVer/$embedZipName"
)

if (-not (Test-Path $zipPath)) {
    Write-Host "  Downloading Python $pyVer embed runtime..." -ForegroundColor Cyan
    $downloaded = $false
    foreach ($embedUrl in $embedUrls) {
        try {
            Invoke-WebRequest -Uri $embedUrl -OutFile $zipPath -UseBasicParsing
            $downloaded = $true
            break
        } catch {
            Write-Host "  download failed: $embedUrl" -ForegroundColor DarkYellow
        }
    }
    if (-not $downloaded) {
        throw @"
Could not download Python embed package.
Place the file manually at:
  $zipPath
Expected name: $embedZipName
"@
    }
}

$venvRoot = Join-Path $ReleaseDir ".venv"
if (Test-Path $venvRoot) {
    Remove-Item -Recurse -Force $venvRoot
}
New-Item -ItemType Directory -Force -Path (Join-Path $venvRoot "Lib\site-packages") | Out-Null

$extractDir = Join-Path $env:TEMP "testory-embed-$pyVer"
if (Test-Path $extractDir) {
    Remove-Item -Recurse -Force $extractDir
}
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

Get-ChildItem -Path $extractDir -File | Where-Object {
    $_.Name -notin @("LICENSE.txt", "$tag._pth", "python.cat")
} | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $venvRoot -Force
}

$zipName = Get-ChildItem -Path $extractDir -Filter "$tag.zip" | Select-Object -First 1
if (-not $zipName) {
    throw "嵌入式包内未找到 $tag.zip"
}
Copy-Item -Path $zipName.FullName -Destination (Join-Path $venvRoot $zipName.Name) -Force

$pthPath = Join-Path $venvRoot "$tag._pth"
@(
    $zipName.Name
    "."
    ".."
    "Lib\site-packages"
    ""
    "import site"
) | Set-Content -Path $pthPath -Encoding ascii

$runtimePy = Join-Path $venvRoot "python.exe"
if (-not (Test-Path $runtimePy)) {
    throw "嵌入式 Python 解压失败: $runtimePy"
}

Write-Host "  Verify portable Python ..."
& $runtimePy -c "import _socket; import sys" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Portable Python self-check failed"
}
Write-Host "  Python $pyVer OK" -ForegroundColor DarkGray

$getPip = Join-Path $cacheDir "get-pip.py"
if (-not (Test-Path $getPip)) {
    Write-Host "  下载 get-pip.py ..."
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
}

Write-Host "  Installing pip ..."
$env:PYTHONNOUSERSITE = "1"
& $runtimePy $getPip -q --no-warn-script-location
if ($LASTEXITCODE -ne 0) {
    throw "get-pip 失败"
}

Write-Host "  Installing release dependencies..."
$reqMain = Join-Path $ReleaseDir "requirements.txt"
$reqWin = Join-Path $ReleaseDir "requirements-windows.txt"
& $runtimePy -m pip install --upgrade pip wheel -q --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $runtimePy -m pip install -q --no-warn-script-location -r $reqMain
if ($LASTEXITCODE -ne 0) { throw "pip install failed: $reqMain" }
& $runtimePy -m pip install -q --no-warn-script-location -r $reqWin
if ($LASTEXITCODE -ne 0) { throw "pip install failed: $reqWin" }

$postInstall = Join-Path $venvRoot "Scripts\pywin32_postinstall.py"
if (Test-Path $postInstall) {
    & $runtimePy $postInstall -install 2>&1 | Out-Null
}

Write-Host "  Verify bundled imports ..."
Push-Location $ReleaseDir
try {
    & $runtimePy -c "import database, requests; print('bundle imports ok')" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Bundled Python cannot import app modules from $ReleaseDir"
    }
} finally {
    Pop-Location
}

Write-Host "  Portable Python ready: $venvRoot" -ForegroundColor Green
