; HuFirst UAT — 全量离线安装包（用户零配置）
; 构建: .\packaging\build_desktop_installer.ps1
; 注意: Inno Setup 仅构建机使用，最终用户不会安装 Inno

#define MyAppName "HuFirst UAT Platform"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "HuFirst"
#define MyAppURL "https://example.com/uat"
#define ReleaseDir "..\..\dist\uat_release"
#define RedistDir "..\..\dist\redist\webview2"

[Setup]
AppId={{A8F3C2E1-9B4D-4F6A-8C1E-0123456789AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\HuFirst\UATPlatform
DefaultGroupName=HuFirst UAT
OutputDir=..\..\dist
OutputBaseFilename=uat_platform_setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
; 允许超大安装包
DiskSpanning=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"

[Files]
; 主程序 + 内置 .venv + playwright-browsers + 默认 data
Source: "{#ReleaseDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; WebView2 引导包（界面窗口所需，安装时按需静默安装）
Source: "{#RedistDir}\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{app}\redist\webview2"; Flags: ignoreversion; Check: WebView2BootstrapperExists

[Icons]
; 直接调用内置 pythonw，无需 PowerShell
Name: "{group}\{#MyAppName}"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\packaging\uat_desktop.py"""; WorkingDir: "{app}"; Comment: "HuFirst UAT"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\packaging\uat_desktop.py"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 若系统无 WebView2，静默安装（离线包内已带引导程序）
Filename: "{app}\redist\webview2\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装界面组件 (WebView2)..."; Flags: waituntilterminated; Check: NeedsWebView2AndBootstrapper
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\packaging\uat_desktop.py"""; Description: "启动 {#MyAppName}"; Flags: postinstall nowait skipifsilent; WorkingDir: "{app}"

[Code]
function WebView2BootstrapperExists: Boolean;
begin
  Result := FileExists(ExpandConstant('{#RedistDir}\MicrosoftEdgeWebview2Setup.exe'));
end;

function IsWebView2Installed: Boolean;
var
  Ver: String;
begin
  { 64-bit WebView2 Runtime 注册表项 }
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Ver) then
  begin
    Result := True;
    exit;
  end;
  if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Ver) then
  begin
    Result := True;
    exit;
  end;
  Result := False;
end;

function NeedsWebView2AndBootstrapper: Boolean;
begin
  Result := (not IsWebView2Installed) and WebView2BootstrapperExists;
end;

function InitializeSetup(): Boolean;
begin
  if not FileExists(ExpandConstant('{#ReleaseDir}\app.py')) then
  begin
    MsgBox('发布目录不完整。请先运行 packaging\build_desktop_installer.ps1', mbError, MB_OK);
    Result := False;
  end
  else if not FileExists(ExpandConstant('{#ReleaseDir}\.venv\Scripts\pythonw.exe')) then
  begin
    MsgBox('缺少内置 Python 环境 (.venv)。请重新执行 build_desktop_installer.ps1', mbError, MB_OK);
    Result := False;
  end
  else
    Result := True;
end;
