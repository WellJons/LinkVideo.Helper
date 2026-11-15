#define MyAppVersion "4.0.0-alpha.11"
[Setup]
AppId=LinkVideo.Helper.4.Background
AppName=LinkVideo.Helper 4 Background (Optional)
AppVersion={#MyAppVersion}
AppPublisher=LinkVideo
DefaultDirName={autopf}\LinkVideo.Helper.4.Background
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir=..\dist-v4-bg
OutputBaseFilename=LinkVideo.Helper.4.Background-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\icon.ico
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
[Files]
Source: "..\dist-v4-core\worker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Messages]
russian.WelcomeLabel2=Отдельный защищённый компонент аварийного резервирования 4.0. Установка не включает расписание и не изменяет задачу 3.x. Расписание настраивается только после явного согласия в Helper 4.
[Code]
function GetFileAttributesW(lpFileName: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function ProtectedPathOK(): Boolean;
var A: LongWord;
begin
  Result := CompareText(ExpandConstant('{app}'), ExpandConstant('{autopf}\LinkVideo.Helper.4.Background')) = 0;
  if not Result then exit;
  A := GetFileAttributesW(ExpandConstant('{app}'));
  if (A <> $FFFFFFFF) and ((A and $400) <> 0) then Result := False;
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
var E: Integer;
begin
  Result := '';
  if not ProtectedPathOK() then begin
    Result := 'Разрешена только защищённая папка Program Files.';
    exit;
  end;
  if FileExists(ExpandConstant('{app}\helper-core.exe')) then begin
    if not Exec(ExpandConstant('{app}\helper-core.exe'), '--lv4-background-installer-check', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, E) or (E <> 0) then
      Result := 'Перед обновлением отключите фоновую задачу 4.0 в Helper.';
  end;
end;
function InitializeUninstall(): Boolean;
var E: Integer;
begin
  Result := False;
  if not ProtectedPathOK() then exit;
  if not Exec(ExpandConstant('{app}\helper-core.exe'), '--lv4-background-disable', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, E) or (E <> 0) then begin
    MsgBox('Не удалось подтвердить отключение задачи 4.0. Удаление компонента остановлено.', mbError, MB_OK);
    exit;
  end;
  Result := True;
end;
