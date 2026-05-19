# Intune 检测规则 — 已安装且版本不低于清单要求时返回 0
param(
    [string] $MinVersion = "1.0.0",
    [string] $InstallRoot = "${env:ProgramFiles}\HuFirst\UATPlatform"
)

function Parse-Ver([string]$v) {
    $nums = [regex]::Matches($v, '\d+') | ForEach-Object { [int]$_.Value }
    if (-not $nums) { return @(0) }
    return ,$nums
}

function Test-VerGe([string]$installed, [string]$required) {
    $a = Parse-Ver $installed
    $b = Parse-Ver $required
    $len = [Math]::Max($a.Count, $b.Count)
    for ($i = 0; $i -lt $len; $i++) {
        $ai = if ($i -lt $a.Count) { $a[$i] } else { 0 }
        $bi = if ($i -lt $b.Count) { $b[$i] } else { 0 }
        if ($ai -gt $bi) { return $true }
        if ($ai -lt $bi) { return $false }
    }
    return $true
}

$marker = Join-Path $InstallRoot "app.py"
if (-not (Test-Path $marker)) {
    Write-Host "未安装"
    exit 1
}

$ver = [Environment]::GetEnvironmentVariable("UAT_APP_VERSION", "Machine")
if (-not $ver) { $ver = "1.0.0" }

if (Test-VerGe $ver $MinVersion) {
    Write-Host "已安装 v$ver"
    exit 0
}
Write-Host "版本过低 v$ver < $MinVersion"
exit 1
