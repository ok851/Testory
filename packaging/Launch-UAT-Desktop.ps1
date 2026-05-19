# 桌面版启动（安装包快捷方式应调用本脚本）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$py = Join-Path $Root ".venv\Scripts\python.exe"
$launcher = Join-Path $Root "packaging\uat_desktop.py"

function Show-Err($msg) {
    $ws = New-Object -ComObject WScript.Shell
    $ws.Popup($msg, 0, "HuFirst UAT", 16) | Out-Null
}

if (-not (Test-Path $launcher)) {
    Show-Err "未找到程序文件，请重新安装。"
    exit 1
}

if (Test-Path $pyw) {
    & $pyw $launcher
} elseif (Test-Path $py) {
    & $py $launcher
} else {
    Show-Err "未找到内置运行环境，请使用完整安装包安装。"
    exit 1
}
