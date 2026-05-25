; Kitchen Search — Windows Installer
; Requires Inno Setup 6+ : https://jrsoftware.org/isinfo.php
;
; To build the installer:
;   ISCC.exe installer.iss
; or open this file in the Inno Setup GUI and click Build.
;
; Prerequisites: run build_nuitka.sh first to produce nuitka-build\emoji-kitchen\

#define AppName    "Kitchen Search"
#define AppVersion "1.0"
#define BuildDir   "nuitka-build\emoji-kitchen"

[Setup]
; Keep AppId stable across updates so Windows recognises upgrades.
AppId={{6F3A2B1C-9D4E-4F87-B532-1A2C3D4E5F60}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Kitchen Search
AppPublisherURL=https://github.com/your-repo/kitchensearch
DefaultDirName={localappdata}\KitchenSearch
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; No admin rights needed — installs to %LOCALAPPDATA%
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=KitchenSearch-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\emoji-picker-tk.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
  Description: "Create a &desktop shortcut"; \
  Flags: unchecked
Name: "startup"; \
  Description: "Start with Windows and listen for &Ctrl+Alt+K"

[Files]
Source: "{#BuildDir}\*"; \
  DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\emoji-picker-tk.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}";     Filename: "{app}\emoji-picker-tk.exe"; Tasks: desktopicon

[Registry]
; Write startup entry when the checkbox is ticked; remove it on uninstall.
Root: HKCU; \
  Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\kitchensearch-daemon.exe"""; \
  Tasks: startup; \
  Flags: uninsdeletevalue

[UninstallRun]
; Clean up any startup entry the daemon may have written itself (e.g. via
; --setup), regardless of whether the installer checkbox was ticked.
Filename: "{app}\kitchensearch-daemon.exe"; \
  Parameters: "--uninstall"; \
  Flags: runhidden; \
  RunOnceId: "RemoveStartupEntry"

[Run]
; If startup was chosen, launch the daemon immediately so the hotkey works
; without needing to reboot.
Filename: "{app}\kitchensearch-daemon.exe"; \
  Tasks: startup; \
  Flags: nowait postinstall skipifsilent runhidden

; Offer to open the app once installation finishes.
Filename: "{app}\emoji-picker-tk.exe"; \
  Flags: nowait postinstall skipifsilent; \
  Description: "Launch {#AppName} now"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\kitchensearch');
    if DirExists(DataDir) then
    begin
      if MsgBox(
        'Remove downloaded cache and settings?' + #13#10 +
        '(' + DataDir + ')',
        mbConfirmation, MB_YESNO
      ) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
