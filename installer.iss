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
ArchitecturesInstallIn64BitMode={#MyArch} = "x64"
ArchitecturesAllowed=x86 x64
MinVersion=10.0
DisableDirPage=no
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

[Files]
; Copia el binario correcto según arch
; Asegúrate de que tu workflow deje los .exe en estas rutas:
; dist\x64\Plugin\Plugin.exe  y  dist\x86\Plugin\Plugin.exe
Source: "dist\{#MyArch}\Plugin\Plugin.exe"; DestDir: "{app}"; Flags: ignoreversion

; Copia las fuentes (se usan en runtime por server.py)
Source: "fonts\DejaVuSans\*.ttf"; DestDir: "{app}\fonts\DejaVuSans"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FloWin Plugin"; Filename: "{app}\Plugin.exe"

[Run]
; Opcional: lanzar al final
; Filename: "{app}\Plugin.exe"; Description: "Iniciar {#MyAppName}"; Flags: nowait postinstall skipifsilent
