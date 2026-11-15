#define MyAppName "LinkVideo.Helper 4 Preview"
#define MyAppVersion "4.0.0-alpha.7"
[Setup]
AppId=LinkVideo.Helper.4.Preview
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=LinkVideo
DefaultDirName={localappdata}\Programs\LinkVideo.Helper.4
DefaultGroupName=LinkVideo.Helper 4
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist-v4
OutputBaseFilename=LinkVideo.Helper.4.0.0-alpha.7-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardImageFile=wizard-brand.bmp
WizardSmallImageFile=wizard-small.bmp
SetupIconFile=..\icon.ico
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\LinkVideo.Helper.4.exe
CloseApplications=yes
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
[Files]
Source: "..\dist-v4\LinkVideo.Helper.4-win32-x64\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\LinkVideo.Helper 4 Preview"; Filename: "{app}\LinkVideo.Helper.4.exe"
Name: "{autodesktop}\LinkVideo.Helper 4 Preview"; Filename: "{app}\LinkVideo.Helper.4.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: unchecked
[Run]
Filename: "{app}\LinkVideo.Helper.4.exe"; Description: "Запустить LinkVideo.Helper 4"; Flags: nowait postinstall skipifsilent

[Messages]
russian.WelcomeLabel1=LinkVideo.Helper 4.0
russian.WelcomeLabel2=Установка новой рабочей области технической поддержки LinkVideo.%n%nВерсия 4.0 устанавливается отдельно от Helper 3.x и не изменяет его данные.%n%nНажмите «Далее», чтобы продолжить.
russian.FinishedHeadingLabel=Рабочая область готова
russian.FinishedLabel=LinkVideo.Helper 4.0 установлен. Теперь можно запустить приложение и войти в рабочий аккаунт.
