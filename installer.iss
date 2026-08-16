#define MyAppName "LinkVideo.Helper"
#define MyAppVersion "3.0.10"
#define MyAppPublisher "LinkVideo"
#define MyAppExeName "LinkVideo.Helper.exe"

[Setup]
; IMPORTANT: AppId is intentionally the SAME as the previous 1.1.x/2.0.x releases.
; Inno Setup therefore treats this as an upgrade of the same application instead
; of creating another copy in Installed Apps.
AppId={{8D39F3B2-8D87-4D9F-B5F6-2D7B65F08C21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
UninstallDisplayName={#MyAppName}
DefaultDirName={autopf}\LinkVideo.Helper
DefaultGroupName=LinkVideo.Helper
UsePreviousAppDir=yes
OutputDir=installer_output
OutputBaseFilename=LinkVideo_VPN_Helper_Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
AppMutex=LinkVideoHelperMutex
VersionInfoVersion=3.0.10.0
VersionInfoCompany=LinkVideo
VersionInfoDescription=LinkVideo.Helper Setup
VersionInfoProductName=LinkVideo.Helper
VersionInfoProductVersion=3.0.10

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[InstallDelete]
; Clean old runtime before copying the new build. Do NOT delete unins*.exe/dat:
; Inno Setup reuses the uninstall log for the same AppId during an upgrade.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\linkvideo_vpn_helper"
Type: filesandordirs; Name: "{app}\scripts"
Type: files; Name: "{app}\LinkVideo.Helper.exe"
Type: files; Name: "{app}\LinkVideo VPN Helper.exe"
Type: files; Name: "{app}\updater.exe"
Type: files; Name: "{app}\*.py"
Type: files; Name: "{app}\*.pyc"
Type: files; Name: "{commondesktop}\LinkVideo VPN Helper.lnk"
Type: files; Name: "{group}\LinkVideo VPN Helper.lnk"

[Files]
Source: "dist\LinkVideo.Helper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LinkVideo.Helper"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\LinkVideo.Helper"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить LinkVideo.Helper"; Flags: nowait postinstall skipifsilent

[Code]
procedure KillOldProcesses();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /T /IM "LinkVideo VPN Helper.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /T /IM "LinkVideo.Helper.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /T /IM "updater.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup(): Boolean;
begin
  KillOldProcesses();
  Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  KillOldProcesses();
  Result := True;
end;
