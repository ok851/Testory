# 对发行包进行 Authenticode 代码签名（需已安装 SignTool 与有效代码签名证书）
# 用法:
#   .\packaging\enterprise\sign_release.ps1 -FilePath dist\uat_platform_setup.exe
#   .\packaging\enterprise\sign_release.ps1 -FilePath dist\*.exe -PfxPath C:\certs\codesign.pfx -PfxPassword (Read-Host -AsSecureString)

param(
    [Parameter(Mandatory = $true)]
    [string[]] $FilePath,
    [string] $Thumbprint = $env:UAT_CODESIGN_THUMBPRINT,
    [string] $PfxPath = $env:UAT_CODESIGN_PFX,
    [securestring] $PfxPassword,
    [string] $TimestampUrl = "http://timestamp.digicert.com",
    [string] $Description = "HuFirst UAT Platform"
)

$ErrorActionPreference = "Stop"

function Find-SignTool {
    $kits = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (Test-Path $kits) {
        $st = Get-ChildItem -Path $kits -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($st) { return $st.FullName }
    }
    $fallback = "${env:ProgramFiles(x86)}\Windows Kits\10\App Certification Kit\signtool.exe"
    if (Test-Path $fallback) { return $fallback }
    throw "未找到 signtool.exe，请安装 Windows SDK。"
}

$signTool = Find-SignTool
Write-Host "SignTool: $signTool" -ForegroundColor Cyan

foreach ($file in $FilePath) {
    $resolved = Resolve-Path -LiteralPath $file
    $args = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/d", $Description)
    if ($PfxPath) {
        if (-not $PfxPassword) {
            $PfxPassword = Read-Host "PFX 密码" -AsSecureString
        }
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($PfxPassword)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        $args += @("/f", $PfxPath, "/p", $plain)
    } elseif ($Thumbprint) {
        $args += @("/sha1", $Thumbprint)
    } else {
        throw "请设置 -Thumbprint / UAT_CODESIGN_THUMBPRINT 或 -PfxPath / UAT_CODESIGN_PFX"
    }
    $args += $resolved.Path
    Write-Host "签名: $($resolved.Path)" -ForegroundColor Green
    & $signTool @args
    if ($LASTEXITCODE -ne 0) { throw "签名失败: $($resolved.Path)" }
}

Write-Host "签名完成。" -ForegroundColor Green
