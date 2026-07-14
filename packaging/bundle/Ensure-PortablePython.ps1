# 在安装目录内创建可移植 Python 运行时（嵌入式发行版，不依赖构建机 Python 路径）
param(
    [Parameter(Mandatory = $true)][string] $ReleaseDir,
    [Parameter(Mandatory = $true)][string] $BuildPython,
    [string] $ProjectRoot = "",
    [switch] $Lite
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

function Resolve-RequirementsFile {
    param([string] $FileName)
    $inRelease = Join-Path $ReleaseDir $FileName
    if (Test-Path $inRelease) { return $inRelease }
    $inRoot = Join-Path $ProjectRoot $FileName
    if (Test-Path $inRoot) { return $inRoot }
    throw "未找到 $FileName（请在项目根目录保留，或复制到发布目录）"
}

Write-Host "  Copying dependencies from build venv..."
$buildVenv = Split-Path -Parent (Split-Path -Parent $BuildPython)
$buildSitePackages = Join-Path $buildVenv "Lib\site-packages"
$destSitePackages = Join-Path $venvRoot "Lib\site-packages"

if (-not (Test-Path $buildSitePackages)) {
    throw "构建机 venv site-packages 不存在: $buildSitePackages"
}

$skipPkgs = @(
    "__pycache__",
    "pip", "pip-*",
    "setuptools", "setuptools-*",
    "wheel", "wheel-*",
    "*.egg-info",
    "_pytest", "pytest", "pytest-*",
    "py", "py-*",
    "pluggy", "pluggy-*",
    "iniconfig", "iniconfig-*",
    "tomli", "tomli_*",
    "pyinstaller", "pyinstaller-*",
    "altgraph", "altgraph-*",
    "pefile", "pefile-*",
    "pyinstaller_hooks_contrib", "pyinstaller_hooks_contrib-*",
    "packaging_specs",
    "cairocffi", "cairocffi-*",
    "cairosvg", "cairosvg-*",
    "tinycss2", "tinycss2-*",
    "cssselect2", "cssselect2-*",
    "fire", "fire-*"
)

if ($Lite) {
    Write-Host "  Lite 模式：跳过 OpenCV 等大包（首次使用时自动下载）" -ForegroundColor DarkYellow
    $skipPkgs += @(
        "cv2", "opencv*",
        "cv2.pyd", "opencv_python_headless*"
    )
}

Write-Host "  从 $buildSitePackages 复制（跳过测试/构建工具）..."
Get-ChildItem -Path $buildSitePackages -Force | Where-Object {
    $name = $_.Name
    $skip = $false
    foreach ($pattern in $skipPkgs) {
        if ($pattern.Contains("*")) {
            if ($name -like $pattern) { $skip = $true; break }
        } else {
            if ($name -eq $pattern) { $skip = $true; break }
        }
    }
    -not $skip
} | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $destSitePackages -Recurse -Force
}

& $runtimePy -m pip install --upgrade pip setuptools wheel -q --no-warn-script-location 2>&1 | Out-Null

Write-Host "  Verifying core imports..."
& $runtimePy -c "import flask, requests, numpy, cv2, playwright; print('core imports ok')" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Warning: some imports failed, running pip check..." -ForegroundColor DarkYellow
    & $runtimePy -m pip check 2>&1 | Out-Null
}

$postInstall = Join-Path $destSitePackages "pywin32_system32"
if (Test-Path $postInstall) {
    Write-Host "  pywin32 already installed (copied from build venv)"
}

Write-Host "  Verify bundled imports ..."
$pyw = Join-Path $venvRoot "pythonw.exe"
if ($protectedRelease) {
    foreach ($interp in @($pyw, $runtimePy)) {
        if (-not (Test-Path $interp)) { continue }
        & $interp -c "import webview; print('desktop shell imports ok')" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Portable Python 无法导入 pywebview（桌面壳层需要）。请检查 requirements-windows.txt"
    }
} else {
    Push-Location $ReleaseDir
    try {
        & $runtimePy -c "import database, requests; print('bundle imports ok')" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Bundled Python cannot import app modules from $ReleaseDir"
        }
    } finally {
        Pop-Location
    }
}

Write-Host "  Portable Python ready: $venvRoot" -ForegroundColor Green
