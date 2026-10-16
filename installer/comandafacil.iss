; Instalador do ComandaFácil — HelpTech. Compilado pelo GitHub Actions (Inno Setup 6).
#define AppName "ComandaFácil"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F4B1D4E-3C2A-4C57-9B0E-4E0C5B9A7D11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=HelpTech
AppPublisherURL=https://comandafacil-eta.vercel.app
AppSupportPhone=(94) 99101-6248
DefaultDirName={localappdata}\Programs\ComandaFacil
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ComandaFacil-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\ComandaFacil.exe
DisableProgramGroupPage=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na área de trabalho"; Flags: checkedonce

[Files]
Source: "..\dist\ComandaFacil\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\ComandaFacil.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ComandaFacil.exe"; Tasks: desktopicon
Name: "{autoprograms}\{#AppName} - pasta de dados e backups"; Filename: "{localappdata}\ComandaFacil\dados"

[Run]
Filename: "{app}\ComandaFacil.exe"; Description: "Abrir o ComandaFácil agora"; Flags: nowait postinstall skipifsilent
