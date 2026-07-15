; Testory 桌面客户端 — Inno Setup 脚本
; 构建: 在项目根目录执行  .\packaging\build_desktop_installer.ps1
; 输出: 项目根目录\dist\testory_setup.exe（大体积时另有 testory_setup-1.bin 等分卷）
; AppId={{GUID}} 中双花括号是 Inno 语法，编译后为 {GUID}，不是错误

#define MyAppName "Testory"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Testory"
#define MyAppURL "https://example.com"
#define ReleaseDir "..\..\dist\uat_release"
#define RedistDir "..\..\dist\redist\webview2"

[Setup]
AppId={{A8F3C2E1-9B4D-4F6A-8C1E-0123456789AB}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\Testory
DefaultGroupName=Testory
DisableDirPage=no
UninstallDisplayIcon={app}\Testory.ico
UninstallDisplayName={#MyAppName}
OutputDir=..\..\dist
OutputBaseFilename=testory_setup
SetupIconFile=testory.ico
Compression=lzma2/fast
SolidCompression=no
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; 禁用分卷，生成单个 exe 方便上传和下载
; DiskSpanning=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"

[Files]
; 主程序 + 内置 .venv + playwright-browsers + 默认 data
; 排除 Android SDK（路径过长且移动端测试通常不需要离线安装）
Source: "{#ReleaseDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "plugin_bundles\android-sdk\*,plugin_bundles\android-sdk"
; WebView2 引导包（界面窗口所需，安装时按需静默安装）
Source: "{#RedistDir}\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{app}\redist\webview2"; Flags: ignoreversion; Check: WebView2BootstrapperBundled

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\Testory.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Testory.ico"; Comment: "Testory 桌面客户端"
Name: "{group}\打开安装目录"; Filename: "{sys}\explorer.exe"; Parameters: """{app}"""; IconFilename: "{sys}\shell32.dll,4"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\Testory.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Testory.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\redist\webview2\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装界面组件 (WebView2)..."; Flags: waituntilterminated; Check: NeedsWebView2AndBootstrapper
Filename: "{app}\Testory.exe"; Description: "启动 {#MyAppName}"; Flags: postinstall nowait skipifsilent; WorkingDir: "{app}"

[Code]
function WebView2BootstrapperBundled: Boolean;
begin
  { 编译期已校验 Source 存在；安装时不再检查构建机路径 }
  Result := True;
end;

function WebView2BootstrapperInApp: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\redist\webview2\MicrosoftEdgeWebview2Setup.exe'));
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
  Result := (not IsWebView2Installed) and WebView2BootstrapperInApp;
end;
