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
[UninstallDelete]
Type: files; Name: "{app}\state\operation.lock"
Type: dirifempty; Name: "{app}\state"
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
function RunAcl(Args: String): Boolean;
var E: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\icacls.exe'), Args, ExpandConstant('{sys}'), SW_HIDE, ewWaitUntilTerminated, E) and (E = 0);
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
var P: String;
    F: TFindRec;
begin
  Result := '';
  P := ExpandConstant('{app}');
  if not ProtectedPathOK() then begin
    Result := 'Разрешена только защищённая папка Program Files.';
    exit;
  end;
  if DirExists(P) then begin
    if FindFirst(P + '\*', F) then begin
      try
        repeat
          if (F.Name <> '.') and (F.Name <> '..') then begin
            Result := 'Сначала отключите и удалите предыдущий Background-компонент 4.0.';
            exit;
          end;
        until not FindNext(F);
      finally
        FindClose(F);
      end;
    end;
  end else if not ForceDirectories(P) then begin
    Result := 'Не удалось создать защищённую папку.';
    exit;
  end;
  if not RunAcl('"' + P + '" /reset') or
     not RunAcl('"' + P + '" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX"') or
     not RunAcl('"' + P + '" /setowner "*S-1-5-32-544"') then
    Result := 'Не удалось защитить ACL компонента.';
end;
procedure CurStepChanged(CurStep: TSetupStep);
var E: Integer;
begin
  if CurStep = ssPostInstall then begin
    if not RunAcl('"' + ExpandConstant('{app}') + '" /setowner "*S-1-5-32-544" /T') then
      RaiseException('Не удалось подтвердить владельца файлов компонента.');
    if not Exec(ExpandConstant('{app}\helper-core.exe'), '--lv4-background-check-installation', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, E) or (E <> 0) then
      RaiseException('Защищённое размещение компонента не подтверждено.');
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
