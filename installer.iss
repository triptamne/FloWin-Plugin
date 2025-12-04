; installer.iss
#define MyAppName "FloWin Plugin"
#define MyAppVersion "1.0.0"
#ifndef MyArch
  #define MyArch "x64"
#endif

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\FloWin\Plugin
DefaultGroupName=FloWin
OutputBaseFilename=Setup-FloWin-Plugin-{#MyArch}
ArchitecturesAllowed=x86 x64

; 👇 Solo instalamos en modo 64-bit cuando MyArch es x64
#if MyArch == "x64"
ArchitecturesInstallIn64BitMode=x64
#else
ArchitecturesInstallIn64BitMode=
#endif

MinVersion=10.0
DisableDirPage=no
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

[Files]
; dist\x64\Plugin\Plugin.exe  ó  dist\x86\Plugin\Plugin.exe
Source: "dist\{#MyArch}\Plugin\Plugin.exe"; DestDir: "{app}"; Flags: ignoreversion

; Fuentes (opcional, útil para tu carga en runtime)
Source: "fonts\*.ttf"; DestDir: "{app}\fonts"; Flags: ignoreversion

[Icons]
Name: "{group}\FloWin Plugin"; Filename: "{app}\Plugin.exe"

[Run]
; Opcional: lanzar al final
; Filename: "{app}\Plugin.exe"; Description: "Iniciar {#MyAppName}"; Flags: nowait postinstall skipifsilent
