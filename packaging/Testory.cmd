@echo off
REM Testory 桌面客户端启动器（安装包快捷方式应指向本文件）
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"

set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "LAUNCHER=%ROOT%\packaging\uat_desktop.py"

if not exist "%LAUNCHER%" (
    mshta "javascript:var s=new ActiveXObject('WScript.Shell');s.Popup('未找到程序文件，请重新安装 Testory。',0,'Testory',16);close()"
    exit /b 1
)

if exist "%PYW%" (
    "%PYW%" "%LAUNCHER%"
    exit /b %ERRORLEVEL%
)

if exist "%PY%" (
    "%PY%" "%LAUNCHER%"
    exit /b %ERRORLEVEL%
)

mshta "javascript:var s=new ActiveXObject('WScript.Shell');s.Popup('未找到内置 Python 环境，请使用完整安装包重新安装。',0,'Testory',16);close()"
exit /b 1
