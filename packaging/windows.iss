; Inno Setup script. Built in CI: iscc /DAppVersion=x.y.z packaging\windows.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8B3E5F0A-6C1D-4E8B-9C57-2F1A7D3B9E41}
AppName=GolfIn3D
AppVersion={#AppVersion}
AppPublisher=GolfIn3D
AppPublisherURL=https://kaorii-ako.github.io/GolfIn3D/
DefaultDirName={autopf}\GolfIn3D
DefaultGroupName=GolfIn3D
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=GolfIn3D-Setup-Windows-x64
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\GolfIn3D.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\GolfIn3D\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\GolfIn3D"; Filename: "{app}\GolfIn3D.exe"
Name: "{autodesktop}\GolfIn3D"; Filename: "{app}\GolfIn3D.exe"; Tasks: desktopicon

[Run]
; let FSX Play reach the OpenConnect listener
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GolfIn3D"" dir=in action=allow program=""{app}\GolfIn3D.exe"" enable=yes profile=private,domain"; Flags: runhidden
Filename: "{app}\GolfIn3D.exe"; Description: "Launch GolfIn3D"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GolfIn3D"""; Flags: runhidden; RunOnceId: "fw"
